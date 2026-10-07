"""Explicit, server-local issue186 video rollout; no paid requests or DB restore.

stage: validate accepted canary, save protected backups/baselines and profile.
promote: own drain markers, stop public admission, swap two gateways then public.
verify: free authenticated discovery and preservation checks only.
rollback: restore retained containers using CURRENT data, never restore dumps.
Failures preserve state and require operator review; no automatic resubmission.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse

import canary as c

PRIVATE = c.EVIDENCE / 'rollout'
PROFILE = Path('/opt/xtai/secrets/video-billing/nody-image-input-186.json')
PROFILE_CONTAINER = '/run/secrets/video-billing/nody-image-input-186.json'
GATEWAYS = ('xtai-video-public-execution', 'xtai-video-job-gateway-v2-production')
PUBLIC = 'xtai-public-video-catalog176'
TARGETS = (*GATEWAYS, PUBLIC)
NGINX = 'ai-api-stack-nginx-1'
IMAGES = {GATEWAYS[0]: 'xtai/video-job-gateway:issue186', GATEWAYS[1]: 'xtai/video-job-gateway:issue186', PUBLIC: 'xtai/public-video:issue186'}
DATA = {GATEWAYS[0]: Path('/opt/xtai/state/public-video-execution/data'),
        GATEWAYS[1]: Path('/opt/xtai/state/video-billing-v2-production/data')}
DRAIN_CONTENT = b'issue186-owned-video-rollout-drain\n'
STATE = PRIVATE / 'state.json'


class DockerConnection(http.client.HTTPConnection):
    """Use the host-local Docker socket; no remote control transport exists."""

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def docker(path: str, *, method: str = 'GET', body: dict | None = None) -> object:
    """Bounded Docker API calls; never expose environment-containing responses."""
    connection = DockerConnection('localhost', timeout=90)
    try:
        wire = json.dumps(body).encode() if body is not None else None
        connection.request(method, path, body=wire, headers={'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(16 * 1024 * 1024 + 1)
        if response.status not in (200, 201, 204, 304):
            c.private_json(PRIVATE / ('docker-error-' + str(time.time_ns()) + '.json'),
                           {'status': response.status, 'path': path, 'response': raw.decode('utf-8', errors='replace')})
        c.require(response.status in (200, 201, 204, 304) and len(raw) <= 16 * 1024 * 1024,
                  'Docker action failed; preserved private state needs review')
        return json.loads(raw) if raw else {}
    finally:
        connection.close()


def save(state: dict) -> None:
    """Record each irreversible boundary before subsequent rollout operations."""
    c.private_json(STATE, state)


def production_sql(statement: str) -> str:
    """The entire rollout has read-only access to production accounting."""
    return c.sql(statement, database=c.PRODUCTION_DB, production_read=True)


def ordinary_key() -> str:
    """Select an existing authorized normal-user token without ACL changes."""
    raw = production_sql("SELECT t.key FROM tokens t JOIN users u ON u.id=t.user_id "
                         "WHERE u.status=1 AND u.role=1 "
                         "AND t.status IN (1,4) AND t.deleted_at IS NULL AND u.deleted_at IS NULL "
                         "AND (t.expired_time=-1 OR t.expired_time>extract(epoch from now())) "
                         "AND coalesce(t.allow_ips,'')='' AND t.model_limits_enabled=false "
                         "AND coalesce(t.\"group\",'') IN ('','auto') ORDER BY t.id DESC LIMIT 1;")
    c.require(bool(raw) and '\n' not in raw and len(raw) <= 128, 'No suitable existing authorized normal-user key')
    return raw if raw.startswith('sk-') else 'sk-' + raw


def base(name: str, port: int) -> str:
    """Resolve only the exact inspected production container's internal address."""
    c.require(name in TARGETS, 'Unapproved rollout target')
    return 'http://' + c.inspect(name)['NetworkSettings']['Networks']['app-net']['IPAddress'] + ':' + str(port)


def snapshot(name: str) -> dict:
    """Free HTTP snapshots; sensitive bodies remain protected local artifacts."""
    info = c.inspect(name)
    token = ordinary_key() if name == PUBLIC else c.environment(info)['VIDEO_JOB_GATEWAY_TOKEN']
    address = base(name, 8098 if name == PUBLIC else 8091)
    result = {}
    for path in (('/ready', '/v1/models', '/v1/capabilities', '/v1/video-prices', '/api/pricing') if name == PUBLIC
                 else ('/health', '/v1/capabilities', '/v1/video-prices')):
        status, payload = c.fetch(address + path, token=token)
        c.require(status == 200, 'Production free snapshot not ready: ' + name + path)
        result[path] = payload
    return result


def sqlite_snapshot(name: str, label: str) -> None:
    """SQLite backup API captures a consistent WAL-aware snapshot, not file copy."""
    source_path = DATA[name] / 'video-jobs.sqlite3'
    c.require(source_path.is_file() and not source_path.is_symlink(), 'Expected exact gateway SQLite state file')
    output_path = PRIVATE / (name + '.' + label + '.sqlite3')
    fd = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    source = sqlite3.connect('file:' + str(source_path) + '?mode=ro', uri=True, timeout=30)
    target = sqlite3.connect(output_path, timeout=30)
    try:
        source.backup(target)
        c.require(target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite backup integrity check failed')
    finally:
        target.close()
        source.close()


def inflight_safe(*, rollback: bool = False) -> dict:
    """Never stop during submit; preserve known polling tasks without replay."""
    checks = {}
    for name in GATEWAYS:
        connection = sqlite3.connect('file:' + str(DATA[name] / 'video-jobs.sqlite3') + '?mode=ro', uri=True, timeout=30)
        try:
            rows = connection.execute('SELECT status,upstream_task_id,submit_attempts,provider_id,payload_json FROM video_jobs').fetchall()
            c.require(not any(row[0] in ('queued', 'submitting') for row in rows), 'Queued/submitting gateway work must finish before replacement')
            c.require(not any(row[0] == 'running' and not row[1] for row in rows), 'Running task without upstream identity needs manual review')
            c.require(not any(row[0] == 'reconciling' and not row[1] and row[3] != 'nodyhub' for row in rows),
                      'Non-Nody uncertain submit could replay after restart; manual review required')
            if rollback:
                c.require(not any(row[0] in ('running', 'reconciling', 'uncertain', 'pending_review')
                                  and json.loads(row[4]).get('mode', 'text') != 'text' for row in rows),
                          'New image tasks must complete before restoring an old execution image')
            checks[name] = {status: sum(1 for row in rows if row[0] == status) for status in {row[0] for row in rows}}
        finally:
            connection.close()
    unresolved = production_sql("SELECT count(*) FROM public_video_tasks WHERE state IN ('reserved','submitted','running','pending_review') AND coalesce(backend_id,'')='';")
    c.require(unresolved == '0', 'Public task without confirmed backend identity must finish or receive manual review; do not replay')
    if rollback:
        c.require(production_sql("SELECT count(*) FROM public_video_tasks WHERE state<>'settled' AND body LIKE '%\"images\"%';") == '0',
                  'Unsettled image public-wallet task prevents safe old-code rollback')
    checks['public_unconfirmed_backend'] = 0
    return checks


def stage() -> None:
    """Capture rollback state and publish only an inactive, accepted profile file."""
    c.require(not STATE.exists(), 'Rollout state exists; review before retry, never overwrite')
    accepted = json.loads((c.PRIVATE / 'settlement-acceptance.json').read_text())
    c.require(accepted.get('wallet_delta_exact') is True and accepted.get('has_video') is True
              and accepted.get('has_audio') is True and accepted.get('cross_user') == 404
              and accepted.get('quota_delta') == 337500 and accepted.get('charged_cny') == '0.675000',
              'Canary wallet/media acceptance is incomplete')
    profiles = json.loads((c.PRIVATE / 'profiles.json').read_text())
    c.require(profiles == c.evidence_profiles(), 'Canary profiles differ from current six verified private receipts')
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PRIVATE, 0o700)
    native_info = c.inspect(c.NATIVE_PROD)
    c.private_json(PRIVATE / (c.NATIVE_PROD + '.before.json'), native_info)
    state = {'phase': 'staging', 'created_at': int(time.time()), 'operation_id': secrets.token_hex(16), 'swapped': [], 'drains': [],
             'native': {'id': native_info['Id'], 'image': native_info['Image']}, 'targets': {}}
    save(state)
    existing_names = c.command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
    for name in TARGETS:
        c.require(name + '-rollback186' not in existing_names and name + '-failed186' not in existing_names,
                  'Owned rollback/failed container name already exists')
        info = c.inspect(name)
        c.require(info['State']['Running'] is True, 'Expected running production video container')
        if name in GATEWAYS:
            mounts = info.get('Mounts') or []
            c.require(any(m.get('Destination') == '/data' and m.get('Source') == str(DATA[name]) and m.get('RW') is True for m in mounts),
                      'Gateway data mount differs from exact existing state directory')
            c.require(any(m.get('Destination') == '/run/secrets/video-billing' and m.get('Source') == '/opt/xtai/secrets/video-billing'
                          and m.get('RW') is False for m in mounts), 'Expected read-only production billing secrets mount')
            c.require(not (DATA[name] / c.environment(info).get('VIDEO_JOB_GATEWAY_DRAIN_FILE_NAME', 'DRAIN')).exists(),
                      'Pre-existing drain file belongs to another operation')
        c.private_json(PRIVATE / (name + '.before.json'), info)
        c.private_json(PRIVATE / (name + '.baseline.json'), snapshot(name))
        image = docker('/images/' + urllib.parse.quote(IMAGES[name], safe='') + '/json')
        state['targets'][name] = {'before_id': info['Id'], 'before_image': info['Image'], 'candidate_image': image['Id']}
        save(state)
    state['initial_inflight'] = inflight_safe()
    for name in GATEWAYS:
        sqlite_snapshot(name, 'before')
    pg_dump = PRIVATE / 'production.before.dump'
    fd = os.open(pg_dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        result = subprocess.run(['docker', 'exec', c.PG, 'pg_dump', '-U', 'newapi', '-d', c.PRODUCTION_DB, '-Fc'], stdout=output, stderr=subprocess.PIPE)
        c.require(result.returncode == 0, 'Read-only production PostgreSQL backup failed')
    c.require(not PROFILE.exists(), 'Persistent issue186 profile already exists; review ownership before overwrite')
    fd = os.open(PROFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(profiles, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.chown(PROFILE, 10002, 10002)
    # Existing secrets-directory ownership/mode is never altered.
    state['profile_sha256'] = hashlib.sha256(PROFILE.read_bytes()).hexdigest()
    state['phase'] = 'staged'
    save(state)
    print(json.dumps({'staged': True, 'targets': list(TARGETS), 'private_backup': str(PRIVATE), 'paid_requests': 0}))


def drains(state: dict, *, remove: bool = False) -> None:
    """Create/remove only exact recorded drain files with task-owned content."""
    owned_content = DRAIN_CONTENT + state['operation_id'].encode('ascii') + b'\n'
    if remove:
        for value in state['drains']:
            path = Path(value)
            c.require(path in [DATA[name] / 'DRAIN' for name in GATEWAYS], 'Unapproved recorded drain path')
            if not path.exists() and not path.is_symlink():
                continue
            c.require(not path.is_symlink() and path.read_bytes() == owned_content,
                      'Drain ownership changed; do not remove it')
            path.unlink()
        state['drains'] = []
        save(state)
        return
    for name in GATEWAYS:
        info = json.loads((PRIVATE / (name + '.before.json')).read_text())
        filename = c.environment(info).get('VIDEO_JOB_GATEWAY_DRAIN_FILE_NAME', 'DRAIN') or 'DRAIN'
        c.require(filename == 'DRAIN', 'Non-default drain filename requires operator review')
        path = DATA[name] / filename
        if str(path) in state['drains'] and path.exists():
            c.require(not path.is_symlink() and path.read_bytes() == owned_content, 'Recorded drain ownership changed')
            continue
        c.require(not path.exists() and not path.is_symlink(), 'Pre-existing drain must not be overwritten')
        if str(path) not in state['drains']:
            state['drains'].append(str(path))
            save(state)  # durable intent before exclusive marker creation
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as output:
            output.write(owned_content)
            output.flush()
            os.fsync(output.fileno())
        os.chown(path, 10002, 10002)
        save(state)


def create_config(info: dict, image: str, *, gateway: bool, operation_id: str = '') -> dict:
    """Retain complete Docker HostConfig and explicit network aliases/settings."""
    config = copy.deepcopy(info['Config'])
    config['Image'] = image
    config['Labels'] = {**(config.get('Labels') or {}), 'com.aixingtuyun.rollout': 'issue186',
                        'com.aixingtuyun.rollout-operation': operation_id}
    values = c.environment(info)
    if gateway:
        values['VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE'] = PROFILE_CONTAINER
        hosts = set(filter(None, values.get('VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS', '').split(',')))
        hosts.add('upload.aixingtuyun.com')
        values['VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS'] = ','.join(sorted(hosts))
    config['Env'] = [key + '=' + value for key, value in values.items()]
    config['HostConfig'] = copy.deepcopy(info['HostConfig'])
    endpoints = {}
    for name, network in info['NetworkSettings']['Networks'].items():
        endpoint = {'Aliases': [alias for alias in network.get('Aliases') or [] if alias not in (info['Id'], info['Id'][:12]) ]}
        for key in ('IPAMConfig', 'DriverOpts', 'Links'):
            if network.get(key) is not None:
                endpoint[key] = network[key]
        endpoints[name] = endpoint
    config['NetworkingConfig'] = {'EndpointsConfig': endpoints}
    return config


def swap(name: str, state: dict) -> None:
    """Keep stopped originals; use their current persistent data, not snapshots."""
    saved = json.loads((PRIVATE / (name + '.before.json')).read_text())
    current = c.inspect(name)
    c.require(current['Id'] == state['targets'][name]['before_id'], 'Production identity changed since staging')
    image = docker('/images/' + urllib.parse.quote(IMAGES[name], safe='') + '/json')
    c.require(image['Id'] == state['targets'][name]['candidate_image'], 'Candidate image changed since staging; restage required')
    # Journal intent BEFORE stop/rename/disconnect/create. Rollback follows the
    # immutable saved ID plus operation label even if the next API call fails.
    state['swapped'].append(name)
    state['targets'][name]['swap_intent'] = True
    save(state)
    docker('/containers/' + current['Id'] + '/stop?t=60', method='POST')
    docker('/containers/' + current['Id'] + '/rename?name=' + name + '-rollback186', method='POST')
    for network in current['NetworkSettings']['Networks']:
        docker('/networks/' + urllib.parse.quote(network, safe='') + '/disconnect', method='POST', body={'Container': current['Id'], 'Force': False})
    created = docker('/containers/create?name=' + name, method='POST', body=create_config(saved, IMAGES[name], gateway=name in GATEWAYS,
                                                                                     operation_id=state['operation_id']))
    state['targets'][name]['new_id'] = created['Id']
    save(state)
    docker('/containers/' + created['Id'] + '/start', method='POST')


def await_health(name: str) -> None:
    """Retry only free readiness reads during bounded process startup."""
    for attempt in range(20):
        try:
            code, _ = c.fetch(base(name, 8098 if name == PUBLIC else 8091) + ('/ready' if name == PUBLIC else '/health'))
            if code == 200:
                return
        except (OSError, ValueError):
            pass
        if attempt < 19:
            time.sleep(1)
    raise RuntimeError('Replacement video process did not become healthy; explicit rollback required')


def compare_catalog(name: str, current: dict, before: dict, *, images: bool) -> None:
    """Verify exact additive modes/prices while preserving historical text rows."""
    old_prices = before['/v1/video-prices']['pricing']['models']
    new_prices = current['/v1/video-prices']['pricing']['models']
    c.require(old_prices == new_prices, 'Existing text-video pricing changed')
    old_rows = {row['id']: row for row in before['/v1/capabilities']['capabilities']['video']['models']}
    new_rows = {row['id']: row for row in current['/v1/capabilities']['capabilities']['video']['models']}
    c.require(set(old_rows) == set(new_rows), 'Existing capability model IDs changed')
    for model, old in old_rows.items():
        new = new_rows[model]
        if model not in c.MODELS:
            c.require(new == old, 'Unrelated or undocumented model capabilities changed')
            continue
        if model not in c.IMAGE_MODELS:
            c.require({key: value for key, value in old.items() if key != 'image_reference'}
                      == {key: value for key, value in new.items() if key != 'image_reference'}, 'Undocumented text-only model capabilities changed')
            c.require(new.get('operation_modes') == ['text'] and not new.get('image_reference', {}).get('available'),
                      'Undocumented Nody model advertised image capability')
            continue
        allowed = {'image_reference', 'operation_modes', 'resolutions', 'max_images', 'max_total_assets'}
        c.require({key: value for key, value in old.items() if key not in allowed}
                  == {key: value for key, value in new.items() if key not in allowed}, 'Existing text capability fields changed')
        c.require(set(old.get('resolutions') or []).issubset(set(new.get('resolutions') or [])), 'Existing text resolution disappeared')
        c.require(set(old.get('operation_modes') or []).issubset(set(new.get('operation_modes') or [])), 'Existing text mode disappeared')
    if images:
        expected = {(m, mode, count, res, seconds) for m, (res, seconds) in c.IMAGE_MODELS.items()
                    for mode, count in (('reference', 1), ('all_reference', 2))}
        tuples = lambda rows: {(p['model'], p['operation_mode'], p['image_count'], p['resolution'], p['duration']) for p in rows}
        prices = current['/v1/video-prices']['image_reference_pricing']['models']
        c.require(len(prices) == 6 and tuples(prices) == expected, 'Exact six public image price tuples missing')
        caps = [p for model in c.IMAGE_MODELS for p in new_rows[model].get('image_reference', {}).get('specifications', [])]
        c.require(len(caps) == 6 and tuples(caps) == expected, 'Exact six capability image tuples missing')
        profiles = json.loads(PROFILE.read_text())['profiles']
        costs = {(p['model'], p['mode'], p['image_count'], p['resolution'], p['duration']): Decimal(p['actual_cost_cny_exact']) * Decimal('1.5') for p in profiles}
        c.require(all(Decimal(p['amount_cny_exact']) == costs[next(iter(tuples([p])))] for p in prices), 'Image retail prices differ from evidence cost times1.5')
    if name == PUBLIC:
        ids = {row['id'] for row in current['/v1/models']['data']}
        c.require(set(c.MODELS).issubset(ids), 'Existing seven normal-user text video IDs disappeared')
        c.require({row['id'] for row in before['/v1/models']['data']}.issubset(ids), 'An existing unrelated text/image model ID disappeared')
        old = before['/api/pricing']['data']
        new = current['/api/pricing']['data']
        normalized = []
        for rows in (old, new):
            mapped = {}
            for row in rows:
                copy_row = {key: value for key, value in row.items() if key not in ('image_reference', 'image_reference_pricing', 'pricing_version')}
                for field in ('enable_groups', 'supported_endpoint_types'):
                    if isinstance(copy_row.get(field), list):
                        copy_row[field] = sorted(copy_row[field], key=lambda value: json.dumps(value, sort_keys=True))
                if row.get('model_name') in c.IMAGE_MODELS and isinstance(copy_row.get('description'), str):
                    copy_row['description'] = copy_row['description'].split('另支持已验证图片参考；', 1)[0]
                mapped[row['model_name']] = copy_row
            normalized.append(mapped)
        c.require(normalized[0] == normalized[1], 'Native/text/image-market pricing changed beyond additive image information')


def verify(*, rollback: bool | None = None) -> None:
    """Only health/discovery reads with existing authorized normal-user access."""
    state = json.loads(STATE.read_text())
    if rollback is None:
        rollback = state.get('phase') == 'rolled_back_verified'
    native = c.inspect(c.NATIVE_PROD)
    c.require(native['Id'] == state['native']['id'] and native['Image'] == state['native']['image'] and native['State']['Running'],
              'Native runtime identity/running state changed')
    for name in TARGETS:
        current = snapshot(name)
        before = json.loads((PRIVATE / (name + '.baseline.json')).read_text())
        compare_catalog(name, current, before, images=not rollback)
        c.private_json(PRIVATE / (name + ('.rollback-verification.json' if rollback else '.verification.json')), current)
    state['phase'] = 'rolled_back_verified' if rollback else 'promoted_verified'
    state['verified_at'] = int(time.time())
    save(state)
    print(json.dumps({'verified': True, 'rollback': rollback, 'normal_user_text_models': 7, 'image_profiles': 0 if rollback else 6, 'paid_requests': 0, 'native_unchanged': True}))


def promote_replacements() -> None:
    """Explicit replacement, with admission closed until gateway health is read."""
    state = json.loads(STATE.read_text())
    c.require(state['phase'] == 'staged' and not state['swapped'], 'Rollout is not freshly staged; review partial state instead of retry')
    c.require(hashlib.sha256(PROFILE.read_bytes()).hexdigest() == state['profile_sha256'], 'Accepted profile changed')
    drains(state)
    state['phase'] = 'draining'
    save(state)
    docker('/containers/' + c.inspect(PUBLIC)['Id'] + '/stop?t=60', method='POST')
    state['final_inflight'] = inflight_safe()
    save(state)
    for name in GATEWAYS:
        sqlite_snapshot(name, 'drained')
        swap(name, state)
        await_health(name)
    swap(PUBLIC, state)
    await_health(PUBLIC)
    state['phase'] = 'replaced_drained'
    save(state)
    c.command(['docker', 'exec', NGINX, 'nginx', '-t'])
    c.command(['docker', 'exec', NGINX, 'nginx', '-s', 'reload'])
    drains(state, remove=True)
    verify()


def promote() -> None:
    """Try one scoped rollback after a promotion failure; never undo accounting."""
    try:
        promote_replacements()
    except Exception as error:
        state = json.loads(STATE.read_text())
        c.private_json(PRIVATE / 'promotion-error.json', {'error_type': type(error).__name__, 'phase': state['phase']})
        if state.get('swapped') or state.get('drains') or state.get('phase') == 'draining':
            try:
                rollback()
            except Exception as recovery:
                c.private_json(PRIVATE / 'rollback-error.json', {'error_type': type(recovery).__name__})
                raise RuntimeError('Promotion and safe rollback need operator review; current accounting preserved') from None
            raise RuntimeError('Promotion did not complete; owned changes safely rolled back without restoring databases') from None
        raise


def rollback() -> None:
    """Restore only owned containers without reverting any database transaction."""
    state = json.loads(STATE.read_text())
    c.require(state.get('swapped') or state.get('drains') or state.get('phase') == 'draining', 'No recorded rollout mutation to roll back')
    drains(state)
    inflight_safe(rollback=True)
    current_names = c.command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
    if PUBLIC in current_names:
        public = c.inspect(PUBLIC)
        expected = state['targets'][PUBLIC]
        owned = public['Id'] == expected['before_id'] or (public['Image'] == expected['candidate_image']
                and public['Config'].get('Labels', {}).get('com.aixingtuyun.rollout-operation') == state['operation_id'])
        c.require(owned, 'Public container identity ambiguous; do not stop it')
        docker('/containers/' + public['Id'] + '/stop?t=60', method='POST')
    for name in [name for name in TARGETS if name in state['swapped']]:
        target = state['targets'][name]
        names = c.command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
        current = c.inspect(name) if name in names else None
        if current is not None and current['Id'] != target['before_id']:
            c.require(current['Image'] == target['candidate_image']
                      and current['Config'].get('Labels', {}).get('com.aixingtuyun.rollout-operation') == state['operation_id']
                      and (not target.get('new_id') or current['Id'] == target['new_id']),
                      'Replacement container ownership changed; do not remove or rename it')
            docker('/containers/' + current['Id'] + '/stop?t=60', method='POST')
            docker('/containers/' + current['Id'] + '/rename?name=' + name + '-failed186', method='POST')
            for network in current['NetworkSettings']['Networks']:
                docker('/networks/' + urllib.parse.quote(network, safe='') + '/disconnect', method='POST', body={'Container': current['Id'], 'Force': False})
        saved = json.loads((PRIVATE / (name + '.before.json')).read_text())
        old = c.inspect(target['before_id'])
        c.require(old['Id'] == target['before_id'] and old['Name'] in ('/' + name, '/' + name + '-rollback186'),
                  'Retained old immutable identity/name differs')
        if old['Name'] != '/' + name:
            docker('/containers/' + old['Id'] + '/rename?name=' + name, method='POST')
        current_networks = c.inspect(name)['NetworkSettings']['Networks']
        for network, endpoint in create_config(saved, saved['Config']['Image'], gateway=False)['NetworkingConfig']['EndpointsConfig'].items():
            if network not in current_networks:
                docker('/networks/' + urllib.parse.quote(network, safe='') + '/connect', method='POST', body={'Container': old['Id'], 'EndpointConfig': endpoint})
        docker('/containers/' + old['Id'] + '/start', method='POST')
        await_health(name)
    if PUBLIC not in state['swapped']:
        docker('/containers/' + c.inspect(PUBLIC)['Id'] + '/start', method='POST')
    c.command(['docker', 'exec', NGINX, 'nginx', '-t'])
    c.command(['docker', 'exec', NGINX, 'nginx', '-s', 'reload'])
    drains(state, remove=True)
    verify(rollback=True)


def main() -> None:
    """Expose reviewed actions only; serialize them under a private local lock."""
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('stage', 'promote', 'verify', 'rollback'))
    args = parser.parse_args()
    c.require(sys.platform.startswith('linux'), 'Server-local Linux rollout helper only')
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PRIVATE, 0o700)
    fd = os.open(PRIVATE / 'rollout.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        {'stage': stage, 'promote': promote, 'verify': verify, 'rollback': rollback}[args.action]()
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'needs_attention': True, 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'Private rollout diagnostics need operator review',
                          'private_state': str(PRIVATE), 'paid_requests': 0, 'automatic_retry': False}))
        raise SystemExit(1)
