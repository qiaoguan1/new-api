"""Operator-invoked, Linux-only routing change; no generation or service restart.

Prepare is the default. Apply journals intent before one bounded SQL transaction.
Unknown results are inspected, never automatically replayed. This module never
changes prices, wallets, credentials or video configuration.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import time
import urllib.parse

import routing_plan as planner

FIRST = (6, 38, 39, 48, 52, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 69)
DEPLETED = (23,)
TARGETS = tuple(sorted(FIRST + DEPLETED))
ROOT = Path('/opt/ai-api-stack/backups')
NATIVE = 'ai-api-stack-new-api-1'
PG = 'ai-api-stack-postgres-1'
NATIVE_IMAGE = 'sha256:7d231e67f87f98ace75943882f3b8128dadfd7963ff579b31d03a6999c30d2f2'
AFFINITY_RULES = ('codex cli trace', 'claude cli trace')
AFFINITY_PATH = '/api/option/channel_affinity_cache'


def operation_path(operation: str) -> Path:
    """Require an opaque fixed operation ID, not a caller-controlled path."""
    if not re.fullmatch(r'[0-9a-f]{32}', operation):
        raise ValueError('invalid operation ID')
    return ROOT / ('issue195-funded-routing-' + operation)


def read_json(path: Path) -> dict:
    """Read bounded private regular evidence without linked ancestry."""
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError('linked evidence')
    info = path.stat()
    if not path.is_file() or info.st_size > 2 * 1024 * 1024:
        raise ValueError('invalid evidence size/type')
    if os.name == 'posix' and (info.st_uid != 0 or info.st_mode & 0o077):
        raise ValueError('evidence is not root-private')
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError('invalid evidence object')
    return data


def write_json(path: Path, data: dict) -> None:
    """Atomically fsync owned evidence; never follow an existing symlink."""
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError('linked evidence')
    if path.exists():
        read_json(path)
    stage = path.with_suffix('.tmp')
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(stage, path)
    if os.name == 'posix':
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def command(arguments: list[str], input_text: str | None = None) -> str:
    """Run fixed argv without shell interpolation; never print private output."""
    result = subprocess.run(arguments, input=input_text, text=True, capture_output=True, timeout=25)
    if result.returncode:
        raise RuntimeError('bounded command failed')
    if len(result.stdout.encode('utf-8')) > 2 * 1024 * 1024:
        raise ValueError('command output too large')
    return result.stdout


def inspect(container: str) -> dict:
    """Read exact known Docker metadata; callers never persist Config/Env."""
    return json.loads(command(['docker', 'inspect', container]))[0]


def psql(sql: str) -> str:
    """Send SQL through stdin to the fixed production database container."""
    native = inspect(NATIVE)
    pg = inspect(PG)
    ne = dict(value.split('=', 1) for value in native['Config']['Env'] if '=' in value)
    pe = dict(value.split('=', 1) for value in pg['Config']['Env'] if '=' in value)
    database = pe.get('POSTGRES_DB', 'new-api')
    if urllib.parse.urlparse(ne.get('SQL_DSN', '')).path.lstrip('/') != database:
        raise ValueError('native database identity differs')
    return command(['docker', 'exec', '-i', PG, 'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1',
                    '-U', pe.get('POSTGRES_USER', 'postgres'), '-d', database], sql)


def capture() -> dict:
    """Read canonical target rows, model shadow and unrelated-state guards."""
    native = inspect(NATIVE)
    if native['Image'] != NATIVE_IMAGE or not native['State']['Running'] or native['State']['Restarting']:
        raise ValueError('native runtime differs')
    ids = ','.join(map(str, TARGETS))
    guard_sql = """SELECT json_build_object(
        'untouched',encode(sha256(convert_to((SELECT coalesce(jsonb_agg(jsonb_build_object(
          'id',c.id,'status',c.status,'priority',c.priority,'immutable',IMM) ORDER BY c.id),'[]')::text
          FROM channels c WHERE c.id NOT IN (IDS)) ||
          (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.channel_id,a."group",a.model),'[]')::text
          FROM abilities a WHERE a.channel_id NOT IN (IDS)),'UTF8')),'hex'),
        'options',encode(sha256(convert_to((SELECT coalesce(jsonb_agg(to_jsonb(o) ORDER BY o.key),'[]')::text
          FROM options o),'UTF8')),'hex'),
        'shadow',SHADOW) FROM channels c WHERE c.id=48;"""
    guard_sql = guard_sql.replace('IDS', ids).replace('IMM', planner.immutable_channel_sql('c')).replace(
        'SHADOW', planner.immutable_channel_sql('c', models_override='gpt-5.5,gpt-5.6-sol'))
    raw = psql('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY; SET LOCAL statement_timeout=\'5s\';\n'
              + planner.read_projection_sql(TARGETS) + '\n' + guard_sql + '\nROLLBACK;')
    objects = [json.loads(line) for line in raw.splitlines() if line.startswith('{')]
    if len(objects) != 2:
        raise ValueError('incomplete database snapshot')
    return {'snapshot': objects[0], 'guards': objects[1], 'native_id': native['Id']}


def evidence() -> dict:
    """Check fresh dated source values; do not call an upstream or create jobs."""
    data = json.loads(Path('/opt/ai-api-stack/channel-monitor/data/upstream-balance-live.json').read_bytes())
    if not 0 <= time.time() - data['updated_at'] <= 3600:
        raise ValueError('funding evidence is stale')
    rows = data['providers'].values() if isinstance(data['providers'], dict) else data['providers']
    selected = {row['name']: row for row in rows}
    for name in ('Code Plan', '寒鹤', 'Paisio', 'nodyhub'):
        row = selected[name]
        value = row['balance_usd']
        if row['status'] != 'complete' or type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('funded provider evidence differs')
    value = selected['maolao API']['balance_usd']
    if selected['maolao API']['status'] != 'complete' or type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value < 0.060000:
        raise ValueError('depleted provider evidence differs')
    return {'updated_at': data['updated_at'], 'updated_at_iso': data['updated_at_iso'],
            'provider_values': {name: selected[name]['balance_usd'] for name in
                                ('Code Plan', '寒鹤', 'Paisio', 'nodyhub', 'maolao API')}}


def source_hashes() -> dict:
    """Bind the exact reviewed planner and runner to the private manifest."""
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in ('routing_runtime.py', 'routing_plan.py')}


def prepare(root: Path) -> dict:
    """Create before/after recovery evidence without changing production data."""
    if root.exists() or ROOT.is_symlink() or ROOT.resolve() != ROOT.absolute():
        raise ValueError('operation already exists or parent linked')
    funding = evidence()
    before = capture()
    alignment = {'id': 48, 'before_models': 'gpt-6,gpt-5.5,gpt-5.6-sol',
                 'after_models': 'gpt-5.5,gpt-5.6-sol', 'after_immutable_sha256': before['guards']['shadow']}
    plan = planner.build_plan(before['snapshot'], FIRST, DEPLETED, 100, [alignment])
    root.mkdir(mode=0o700)
    manifest = {'plan': plan, 'guards': before['guards'], 'native_id': before['native_id'],
                'funding': funding, 'source_hashes': source_hashes(), 'prepared_at': time.time()}
    write_json(root / 'manifest.json', manifest)
    write_json(root / 'recovery.json', {'before': plan['before'], 'after': plan['after'],
                                     'rollback_sql': planner.render_rollback_sql(plan)})
    return {'status': 'prepared', 'before_hash': plan['before_hash'], 'after_hash': plan['after_hash'],
            'channels': len(plan['before']['channels']), 'abilities': len(plan['before']['abilities'])}


def reconcile_hash(current: str, before: str, after: str) -> str:
    """Classify actual state without replaying an uncertain transaction."""
    return 'target_verified' if current == after else 'not_applied' if current == before else 'foreign_or_unknown'


def load_manifest(root: Path) -> dict:
    """Require the exact frozen scope, protected source and operation directory."""
    info = root.stat()
    if root.is_symlink() or root.resolve() != root.absolute() or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('operation directory is not private')
    manifest = read_json(root / 'manifest.json')
    plan = manifest['plan']
    if tuple(plan['first_ids']) != tuple(sorted(FIRST)) or tuple(plan['depleted_ids']) != DEPLETED or plan['target_priority'] != 100:
        raise ValueError('manifest scope differs')
    if plan['model_alignments'] != [{'id': 48, 'before_models': 'gpt-6,gpt-5.5,gpt-5.6-sol',
                                   'after_models': 'gpt-5.5,gpt-5.6-sol',
                                   'after_immutable_sha256': manifest['guards']['shadow']}]:
        raise ValueError('model alignment differs')
    if manifest['source_hashes'] != source_hashes():
        raise ValueError('reviewed source changed')
    planner.render_apply_sql(plan)
    return manifest


def reconcile(root: Path) -> dict:
    """Only read actual state and compare to before/after; never execute SQL."""
    manifest = load_manifest(root)
    current = capture()
    plan = manifest['plan']
    state = reconcile_hash(planner.snapshot_hash(current['snapshot']), plan['before_hash'], plan['after_hash'])
    guards_match = all(current['guards'][key] == manifest['guards'][key] for key in ('untouched', 'options'))
    return {'status': state, 'outside_unchanged': guards_match, 'native_same': current['native_id'] == manifest['native_id'],
            'actual_hash': planner.snapshot_hash(current['snapshot'])}


def apply(root: Path) -> dict:
    """Send exactly one SQL transaction after durable intent; never auto retry."""
    manifest = load_manifest(root)
    if (root / 'state.json').exists():
        raise ValueError('existing intent requires read-only inspection')
    evidence()
    before = reconcile(root)
    if before['status'] != 'not_applied' or not before['outside_unchanged'] or not before['native_same']:
        raise ValueError('pre-apply state drift')
    write_json(root / 'state.json', {'status': 'apply_intent', 'started_at': time.time()})
    try:
        psql(planner.render_apply_sql(manifest['plan']))
    except Exception:
        write_json(root / 'state.json', {'status': 'outcome_unknown', 'at': time.time()})
        raise RuntimeError('outcome requires read-only inspection') from None
    result = reconcile(root)
    write_json(root / 'state.json', dict(result, at=time.time()))
    return result


class LocalTLS(http.client.HTTPSConnection):
    """Strict certificate validation for the fixed local Nginx host."""
    def connect(self) -> None:
        raw = socket.create_connection(('127.0.0.1', 443), timeout=self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname='api.aixingtuyun.com')


def clear_affinity(root: Path) -> dict:
    """Clear only two relevant rule caches after verified SQL; no settings edit."""
    current = reconcile(root)
    if current['status'] != 'target_verified' or not current['outside_unchanged'] or not current['native_same']:
        raise ValueError('routing is not verified')
    if (root / 'affinity.json').exists():
        raise ValueError('affinity operation already recorded')
    raw = psql("BEGIN READ ONLY; SELECT json_build_object('id',id,'token',access_token) FROM users "
               "WHERE role>=100 AND status=1 AND deleted_at IS NULL AND coalesce(access_token,'')<>'' ORDER BY id LIMIT 1; ROLLBACK;")
    auth = json.loads(next(line for line in raw.splitlines() if line.startswith('{')))
    write_json(root / 'affinity.json', {'status': 'intent', 'at': time.time()})
    counts = []
    for rule in AFFINITY_RULES:
        connection = LocalTLS('api.aixingtuyun.com', timeout=10)
        try:
            connection.request('DELETE', AFFINITY_PATH + '?rule_name=' + urllib.parse.quote(rule, safe=''),
                               headers={'Host': 'api.aixingtuyun.com', 'Authorization': 'Bearer ' + auth['token'],
                                        'New-Api-User': str(auth['id'])})
            response = connection.getresponse()
            data = response.read(65537)
            if response.status != 200 or len(data) > 65536:
                raise ValueError('affinity response not confirmed')
            result = json.loads(data)
            if result.get('success') is not True or type(result.get('data', {}).get('deleted')) is not int:
                raise ValueError('affinity deletion unconfirmed')
            counts.append({'rule': rule, 'deleted': result['data']['deleted']})
        finally:
            connection.close()
    result = {'status': 'cleared', 'counts': counts, 'at': time.time()}
    write_json(root / 'affinity.json', result)
    return result


def main() -> None:
    """Explicit operator actions; prepare is the safe default, never autorun."""
    parser = argparse.ArgumentParser()
    parser.add_argument('operation')
    parser.add_argument('--action', choices=('prepare', 'apply', 'inspect', 'clear-affinity'), default='prepare')
    arguments = parser.parse_args()
    if os.name != 'posix' or os.geteuid() != 0:
        raise ValueError('Linux root operation required')
    root = operation_path(arguments.operation)
    actions = {'prepare': prepare, 'apply': apply, 'inspect': reconcile, 'clear-affinity': clear_affinity}
    print(json.dumps(actions[arguments.action](root), ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'status': 'needs_processing', 'error_class': type(error).__name__}))
        raise SystemExit(1)
