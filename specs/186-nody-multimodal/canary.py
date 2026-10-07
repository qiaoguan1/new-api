"""Server-local issue186 canary; never promotes or modifies production wallets.

Actions: prepare (schema-only isolation, no generation), verify-free (catalog
and rejection accounting), submit (ONE durable paid intent), poll (read-only
observation, settlement and media verification). Run only on the relay host.
Sensitive HTTP bodies, keys, environment files and receipts stay mode0600.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path('/opt/ai-api-stack')
EVIDENCE = ROOT / 'backups/nody-multimodal-186-20261007'
PRIVATE = EVIDENCE / 'canary'
SOURCE = ROOT / 'releases/issue186-nody-multimodal/source'
DATA = Path('/opt/xtai/state/issue186-canary/data')
DB = 'xtai_issue186_canary'
PRODUCTION_DB = 'new-api'
PG = 'ai-api-stack-postgres-1'
NATIVE_PROD = 'ai-api-stack-new-api-1'
GATEWAY_PROD = 'xtai-video-public-execution'
NATIVE = 'xtai-native186-canary'
EXECUTION = 'xtai-execution186-canary'
PUBLIC = 'xtai-public186-canary'
NETWORKS = ('app-net', 'ai-api-stack_stack-internal')
MODELS = ('wan3.0-video', 'wan3.0-video-prime', 'grok-imagine-1.5-video',
          'grok-video-3', 'grok-imagine-video-official', 'omni-flash', 'flux-3-video')
IMAGE_MODELS = {'grok-video-3': ('720p', 6), 'grok-imagine-1.5-video': ('720p', 6),
                'grok-imagine-video-official': ('480p', 1)}
QUOTA = 25_000_000
RATE = Decimal('500000')
CAP = Decimal('50')
EXPOSURE = Decimal('2.4')
INTENT = PRIVATE / 'public-wallet-intent.json'
UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')


def require(condition: bool, message: str) -> None:
    """Fail closed without rendering secret-containing source objects."""
    if not condition:
        raise RuntimeError(message)


def command(args: list[str], *, input_text: str | None = None) -> str:
    """Keep command arguments, stderr and environments out of public output."""
    result = subprocess.run(args, input=input_text, text=True, capture_output=True)
    require(result.returncode == 0, 'Server command failed; inspect private diagnostics locally')
    return result.stdout.strip()


def inspect(name: str) -> dict:
    """Read an exact named container without printing its environment."""
    return json.loads(command(['docker', 'inspect', name]))[0]


def environment(info: dict) -> dict[str, str]:
    """Decode Docker environment in memory only."""
    return dict(row.split('=', 1) for row in info['Config']['Env'])


def private_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    """Durably save private state before a potentially paid network call."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    if exclusive:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
    else:
        temporary = path.with_name(path.name + '.tmp-' + secrets.token_hex(4))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def sql(statement: str, *, database: str = DB, production_read: bool = False) -> str:
    """Only the exact task DB permits writes; production reads are SELECT only."""
    if database != DB:
        require(production_read and database == PRODUCTION_DB and statement.lstrip().upper().startswith('SELECT '),
                'Production database writes are prohibited')
    return command(['docker', 'exec', '-i', PG, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                    '-U', 'newapi', '-d', database, '-At'], input_text=statement)


def canary_dsn(original: str) -> str:
    """Replace and recheck the database component, retaining server-local auth."""
    parsed = urllib.parse.urlsplit(original)
    require(parsed.scheme in ('postgres', 'postgresql') and parsed.hostname is not None,
            'Expected explicit PostgreSQL URL DSN')
    result = urllib.parse.urlunsplit(parsed._replace(path='/' + DB))
    require(urllib.parse.urlsplit(result).path == '/' + DB and DB != PRODUCTION_DB,
            'Canary DSN isolation check failed')
    return result


def media_proven(record: dict) -> bool:
    """Accept downloaded/probed AV proof, never a successful task status alone."""
    proof = record.get('delivery_check') or record.get('media_check') or record.get('media_proof') or {}
    if not isinstance(proof, dict):
        return False
    http_ok = proof.get('http', proof.get('http_status', 200)) == 200
    av = proof.get('has_video') is True and proof.get('has_audio') is True
    streams = (record.get('media_probe') or {}).get('streams') or proof.get('streams') or []
    if isinstance(streams, list):
        kinds = {s.get('codec_type') for s in streams if isinstance(s, dict)}
        av = av or {'video', 'audio'}.issubset(kinds)
    return http_ok and av and (int(proof.get('bytes') or proof.get('download_bytes') or proof.get('length') or 0) > 0
                               or bool(proof.get('sha256')))


def evidence_profiles() -> dict:
    """Activate only all six exact successful authenticated-bill/media tuples."""
    paths = sorted(EVIDENCE.glob('grok-*.json'))
    require(len(paths) == 6, 'Expected exactly six private direct-test records')
    rows = []
    total = Decimal(0)
    seen = set()
    for path in paths:
        record = json.loads(path.read_text())
        model = record.get('model')
        count = record.get('image_count')
        require(model in IMAGE_MODELS and type(count) is int and count in (1, 2), 'Unexpected direct-test tuple')
        require((model, count) not in seen, 'Duplicate direct-test tuple')
        seen.add((model, count))
        poll = record.get('last_poll') or record.get('result') or {}
        query = record.get('billing_query') or {}
        require(query.get('success') is True, 'Authenticated billing query must succeed')
        items = (query.get('data') or {}).get('items') or []
        task = (record.get('response') or {}).get('id')
        bill_matches = [item for item in items if item.get('task_id') == task]
        require(len(bill_matches) == 1, 'Authenticated task bill must uniquely match submitted UUID')
        bill = bill_matches[0]
        require(isinstance(poll, dict) and bill.get('status') == 'SUCCESS', 'Missing successful authenticated direct-test bill')
        observation = record.get('normalized_observation') or {}
        if observation:
            require(observation.get('task_id') == task, 'Delivery observation task UUID differs')
        status = poll.get('status') or record.get('status') or observation.get('status')
        require(status in ('SUCCESS', 'completed', 'succeeded'), 'Direct-test task not successful')
        require(media_proven(record), 'Every direct-test result needs downloaded audio/video media proof')
        require(isinstance(task, str) and UUID.fullmatch(task) is not None, 'Missing authenticated billing task UUID')
        source = 'nodyhub_authenticated_video_task'
        cost_raw = record.get('actual_cost_cny_exact')
        require(isinstance(cost_raw, str), 'Exact actual CNY billing string required')
        cost = Decimal(cost_raw)
        require(cost.is_finite() and cost > 0 and cost.as_tuple().exponent >= -6, 'Invalid exact CNY cost')
        require(type(bill.get('quota')) is int and bill['quota'] > 0
                and Decimal(bill['quota']) / RATE * Decimal('1.5') == cost,
                'Exact CNY cost differs from authenticated task quota and configured Nody rate')
        total += cost
        resolution, duration = IMAGE_MODELS[model]
        request = record.get('request') or {}
        require(request.get('duration') == duration, 'Direct-test duration differs from documented tuple')
        source_resolution = request.get('resolution') or request.get('quality')
        require(isinstance(source_resolution, str) and source_resolution.lower() == resolution,
                'Direct-test resolution differs from documented tuple')
        rows.append({'model': model, 'mode': 'reference' if count == 1 else 'all_reference',
                     'image_count': count, 'resolution': resolution, 'duration': duration,
                     'actual_cost_cny_exact': format(cost, '.6f'), 'evidence_task_id': task,
                     'evidence_source': source, 'status': 'succeeded', 'cost_status': 'actual'})
    require(total == Decimal('4.5'), 'Six direct-test bills must match the verified CNY4.5 total')
    return {'schema_version': 'xtai-nody-image-input-v1', 'revision': 'issue186-six-verified-20261007', 'profiles': rows}


def run_container(name: str, image: str, values: dict[str, str], binds: list[str]) -> None:
    """Create isolated names/networks without host ports or production aliases."""
    require(name in (NATIVE, EXECUTION, PUBLIC), 'Unapproved container target')
    if name in (NATIVE, PUBLIC):
        require(urllib.parse.urlsplit(values.get('SQL_DSN', '')).path == '/' + DB,
                'Main DSN must explicitly use the canary DB')
        # Public-video rejects a separate log DSN and binds LOG_DB = DB itself.
        require((name == NATIVE and urllib.parse.urlsplit(values.get('LOG_SQL_DSN', '')).path == '/' + DB)
                or (name == PUBLIC and values.get('LOG_SQL_DSN') == ''), 'Log database isolation differs')
    path = PRIVATE / (name + '.env')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        for key, value in sorted(values.items()):
            require('\n' not in value and '\r' not in value, 'Unsafe Docker environment value')
            output.write(key + '=' + value + '\n')
    args = ['docker', 'create', '--name', name, '--network', NETWORKS[0], '--restart', 'no',
            '--env-file', str(path), '--label', 'com.aixingtuyun.task=issue186-canary', '--memory', '1g']
    for bind in binds:
        args += ['-v', bind]
    command(args + [image])
    command(['docker', 'network', 'connect', NETWORKS[1], name])
    command(['docker', 'start', name])


def prepare() -> None:
    """Clone schema only and seed two canary accounts plus basic group options."""
    require(not (PRIVATE / 'prepared.json').exists(), 'Canary already prepared; inspect it, do not overwrite')
    require(not DATA.exists(), 'Task data path already exists; inspect it, do not reuse unknown jobs')
    require(sql("SELECT 1 FROM pg_database WHERE datname='" + DB + "';", database=PRODUCTION_DB,
                production_read=True) == '', 'Task database already exists; no implicit drop/reuse')
    names = command(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
    require(not set((NATIVE, EXECUTION, PUBLIC)).intersection(names), 'Task container already exists')
    profiles = evidence_profiles()
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(PRIVATE, 0o700)
    private_json(PRIVATE / 'profiles.json', profiles)
    sys.path.insert(0, str(SOURCE / 'ops/video-job-gateway'))
    from nody_image_contracts import NodyImageContracts
    require(len(NodyImageContracts.load(PRIVATE / 'profiles.json').profiles) == 6,
            'Release contract parser did not accept all six exact evidence tuples')
    os.chown(PRIVATE / 'profiles.json', 10002, 10002)
    native_values = environment(inspect(NATIVE_PROD))
    require(urllib.parse.urlsplit(native_values.get('SQL_DSN', '')).path == '/' + PRODUCTION_DB,
            'Production native DSN does not match the known schema source')
    gateway_values = environment(inspect(GATEWAY_PROD))
    require(bool(gateway_values.get('VIDEO_JOB_GATEWAY_TOKEN')), 'Production execution service token unavailable')
    require(bool(gateway_values.get('VIDEO_JOB_NODYHUB_API_KEY')), 'Existing Nody execution key unavailable')
    schema = PRIVATE / 'schema-only.sql'
    fd = os.open(schema, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as output:
        result = subprocess.run(['docker', 'exec', PG, 'pg_dump', '-U', 'newapi', '-d', PRODUCTION_DB,
                                 '--schema-only', '--no-owner', '--no-privileges'], stdout=output, stderr=subprocess.PIPE)
        require(result.returncode == 0, 'Schema-only dump failed')
    # CREATE DATABASE cannot run in a transaction. This one administrative write
    # is hard-coded; no user-supplied database name or destructive command exists.
    command(['docker', 'exec', '-i', PG, 'psql', '-X', '-v', 'ON_ERROR_STOP=1', '-U', 'newapi',
             '-d', 'postgres', '-c', 'CREATE DATABASE ' + DB + ' OWNER newapi;'])
    with schema.open('rb') as source:
        restored = subprocess.run(['docker', 'exec', '-i', PG, 'psql', '-X', '-v', 'ON_ERROR_STOP=1',
                                   '-U', 'newapi', '-d', DB], stdin=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        require(restored.returncode == 0, 'Canary schema restore failed')
    require(sql('SELECT count(*) FROM users; SELECT count(*) FROM tokens; SELECT count(*) FROM tasks;')
            .splitlines() == ['0', '0', '0'], 'Schema clone unexpectedly contains user/job data')
    options = {'GroupRatio': {'default': 1, '视频': 1}, 'GroupGroupRatio': {},
               'UserUsableGroups': {'default': 'Default', '视频': 'Video', 'auto': 'Auto'},
               'AutoGroups': ['视频']}
    for key, value in options.items():
        encoded = json.dumps(value, ensure_ascii=False).replace("'", "''")
        sql('INSERT INTO options(key,value) VALUES (\'' + key + "','" + encoded + "');")
    accounts = []
    for suffix in ('owner', 'other'):
        key = secrets.token_hex(24)
        uid = int(sql("INSERT INTO users(username,password,role,status,quota,used_quota,request_count,\"group\",aff_code,created_at) VALUES ('v186c-" + suffix + "','!nonlogin-canary',1,1," + str(QUOTA) + ",0,0,'auto','" + secrets.token_hex(4) + "',extract(epoch from now())::bigint) RETURNING id;").splitlines()[0])
        tid = int(sql('INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) VALUES (' + str(uid) + ",'" + key + "',1,'issue186-canary',extract(epoch from now())::bigint,0,-1," + str(QUOTA) + ",false,false,'','',0,'',false) RETURNING id;").splitlines()[0])
        accounts.append({'user_id': uid, 'token_id': tid, 'key': 'sk-' + key, 'initial_quota': QUOTA})
    private_json(PRIVATE / 'accounts.json', accounts)
    DATA.mkdir(parents=True, mode=0o700)
    os.chown(DATA, 10002, 10002)
    dsn = canary_dsn(native_values['SQL_DSN'])
    native_values.update(SQL_DSN=dsn, LOG_SQL_DSN=dsn, NODE_TYPE='slave', REDIS_CONN_STRING='',
                         QUOTA_DB_AUTHORITATIVE='true', BATCH_UPDATE_ENABLED='false', MEMORY_CACHE_ENABLED='false')
    run_container(NATIVE, 'new-api-fixed:issue183-runtime', native_values, [])
    # Only the Nody configuration and generic gateway settings are cloned.
    execution_values = {key: value for key, value in gateway_values.items()
                        if key.startswith('VIDEO_JOB_GATEWAY_') or key.startswith('VIDEO_JOB_NODYHUB_') or key == 'TZ'}
    execution_values.update(VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS='nodyhub', VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS='nodyhub',
                            VIDEO_JOB_PAISIO_BILLING_ENABLED='0', VIDEO_JOB_ROLLDEK_BILLING_ENABLED='0', VIDEO_JOB_TOONFLOW_BILLING_ENABLED='0',
                            VIDEO_JOB_GATEWAY_DATA_DIR='/data', VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED='0', VIDEO_JOB_GATEWAY_WEBHOOK_URL='',
                            VIDEO_JOB_GATEWAY_WEBHOOK_SECRET='', VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL='',
                            VIDEO_JOB_GATEWAY_PRICING_URL='http://' + NATIVE + ':3000/api/pricing',
                            VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS='upload.aixingtuyun.com',
                            VIDEO_JOB_GATEWAY_V22_REFERENCE_VIDEO_ENABLED='0', VIDEO_JOB_GATEWAY_V22_REFERENCE_AUDIO_ENABLED='0',
                            VIDEO_JOB_GATEWAY_V22_REFERENCE_COMBINED_ENABLED='0', VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE='/run/issue186/profiles.json',
                            VIDEO_JOB_NODYHUB_BILLING_ENABLED='1', VIDEO_JOB_NODYHUB_BILLING_CREDENTIAL_FILE='/run/secrets/video-billing/nodyhub-session.json')
    run_container(EXECUTION, 'xtai/video-job-gateway:issue186', execution_values,
                  [str(DATA) + ':/data', '/opt/xtai/secrets/video-billing:/run/secrets/video-billing:ro',
                   str(PRIVATE / 'profiles.json') + ':/run/issue186/profiles.json:ro'])
    public_values = {key: value for key, value in native_values.items() if key in ('SQL_DSN', 'LOG_SQL_DSN', 'SESSION_SECRET', 'CRYPTO_SECRET', 'TZ')}
    public_values.update(LOG_SQL_DSN='', NODE_TYPE='slave', REDIS_CONN_STRING='', QUOTA_DB_AUTHORITATIVE='true', BATCH_UPDATE_ENABLED='false',
                         VIDEO_JOB_GATEWAY_TOKEN=execution_values['VIDEO_JOB_GATEWAY_TOKEN'],
                         PUBLIC_VIDEO_NATIVE='http://' + NATIVE + ':3000', PUBLIC_VIDEO_BACKEND='http://' + EXECUTION + ':8091',
                         PUBLIC_VIDEO_LEGACY='http://' + EXECUTION + ':8091', PUBLIC_VIDEO_BASE_URL='https://issue186-canary.invalid',
                         PUBLIC_VIDEO_QUOTA_PER_CNY=str(RATE), PUBLIC_VIDEO_TRUSTED_PROXIES='127.0.0.1')
    run_container(PUBLIC, 'xtai/public-video:issue186', public_values, [])
    private_json(PRIVATE / 'prepared.json', {'at': int(time.time()), 'database': DB, 'containers': [NATIVE, EXECUTION, PUBLIC],
                                           'profiles': 6, 'schema_only': True, 'paid_submissions': 0})
    print(json.dumps({'prepared': True, 'database': DB, 'containers': [NATIVE, EXECUTION, PUBLIC], 'paid_submissions': 0}))


def isolated_base() -> str:
    """Check all canary routing, database, volume and callback invariants live."""
    require((PRIVATE / 'prepared.json').exists(), 'Preparation receipt required')
    for name in (NATIVE, PUBLIC):
        values = environment(inspect(name))
        require(values.get('NODE_TYPE') == 'slave' and values.get('QUOTA_DB_AUTHORITATIVE') == 'true'
                and not values.get('REDIS_CONN_STRING'), 'Native/cache canary isolation differs')
        require(urllib.parse.urlsplit(values.get('SQL_DSN', '')).path == '/' + DB, 'Canary SQL target differs')
        require((name == NATIVE and urllib.parse.urlsplit(values.get('LOG_SQL_DSN', '')).path == '/' + DB)
                or (name == PUBLIC and values.get('LOG_SQL_DSN') == ''), 'Canary log target differs')
    public = inspect(PUBLIC)
    values = environment(public)
    require(values.get('PUBLIC_VIDEO_NATIVE') == 'http://' + NATIVE + ':3000'
            and values.get('PUBLIC_VIDEO_BACKEND') == 'http://' + EXECUTION + ':8091'
            and values.get('PUBLIC_VIDEO_LEGACY') == 'http://' + EXECUTION + ':8091', 'Canary routing is not isolated')
    execution = inspect(EXECUTION)
    values = environment(execution)
    require(values.get('VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS') == 'nodyhub'
            and values.get('VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED') == '0', 'Canary provider/callback scope differs')
    mounts = execution.get('Mounts') or []
    require(any(m.get('Destination') == '/data' and m.get('Source') == str(DATA) for m in mounts), 'Canary job DB path differs')
    require(any(m.get('Destination') == '/run/secrets/video-billing' and m.get('RW') is False for m in mounts), 'Billing secrets must be read-only')
    return 'http://' + public['NetworkSettings']['Networks']['app-net']['IPAddress'] + ':8098'


def fetch(url: str, *, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
    """Use bounded authenticated JSON calls without following redirects."""
    headers = {'Content-Type': 'application/json', 'X-XingTu-Contract-Version': 'xtai-video-billing-v2.2'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if body is not None and body.get('request_id'):
        headers['Idempotency-Key'] = body['request_id']
    request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=45) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
            require(len(raw) <= 4 * 1024 * 1024, 'Canary response too large')
            return response.status, json.loads(raw)
    except urllib.error.HTTPError as error:
        raw = error.read(4 * 1024 * 1024)
        try:
            value = json.loads(raw)
        except ValueError:
            value = {}
        return error.code, value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Prevent authentication headers from leaving the selected canary target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wallet(account: dict) -> list[str]:
    """Read only the exact staging fixture balances and task/log counts."""
    uid, tid = int(account['user_id']), int(account['token_id'])
    return sql('SELECT quota,used_quota,request_count FROM users WHERE id=' + str(uid) + '; '
               'SELECT remain_quota,used_quota FROM tokens WHERE id=' + str(tid) + '; '
               'SELECT count(*) FROM public_video_tasks WHERE user_id=' + str(uid) + '; '
               'SELECT count(*) FROM logs WHERE user_id=' + str(uid) + ' AND type=2;').splitlines()


def reference_body(request_id: str) -> dict:
    """Reuse the first exact source PNG and its content hash, never upload anew."""
    assets = json.loads((EVIDENCE / 'assets.json').read_text())
    require(isinstance(assets, list) and len(assets) == 2, 'Expected the two existing private source fixtures')
    asset = assets[0]
    parsed = urllib.parse.urlsplit(asset['url'])
    require(parsed.scheme == 'https' and parsed.hostname == 'upload.aixingtuyun.com' and not parsed.username,
            'Fixture origin differs from approved media host')
    require(re.fullmatch(r'[0-9a-f]{64}', asset['identity']) is not None, 'Fixture content SHA256 missing')
    return {'request_id': request_id, 'model': 'grok-imagine-video-official', 'prompt': 'A simple colored ball moves gently on a plain white background, minimal motion.',
            'resolution': '480p', 'duration': 1, 'aspect_ratio': '16:9', 'generate_audio': True,
            'mode': 'reference', 'images': [asset['url']], 'image_roles': ['reference'], 'image_identities': [asset['identity']]}


def verify_free() -> None:
    """Require seven text IDs, only six image tuples, and invalid SHA zero debit."""
    base = isolated_base()
    accounts = json.loads((PRIVATE / 'accounts.json').read_text())
    before = wallet(accounts[0])
    require(fetch(base + '/v1/models')[0] == 401, 'Unauthenticated discovery must be rejected')
    observed = {}
    for endpoint in ('/ready', '/v1/models', '/v1/capabilities', '/v1/video-prices'):
        code, value = fetch(base + endpoint, token=accounts[0]['key'])
        require(code == 200, 'Canary catalog/readiness not ready: ' + endpoint)
        observed[endpoint] = value
    ids = {row['id'] for row in observed['/v1/models']['data']}
    require(set(MODELS).issubset(ids), 'One or more existing text-video IDs disappeared')
    rows = observed['/v1/capabilities']['capabilities']['video']['models']
    public_profiles = observed['/v1/video-prices']['image_reference_pricing']['models']
    require(len(public_profiles) == 6, 'Expected exactly six public image price tuples')
    expected = {(m, 'reference' if c == 1 else 'all_reference', c, r, d)
                for m, (r, d) in IMAGE_MODELS.items() for c in (1, 2)}
    prices = {(p['model'], p['operation_mode'], p['image_count'], p['resolution'], p['duration']) for p in public_profiles}
    require(prices == expected, 'Public image pricing tuple set differs')
    require(not any(key in p for p in public_profiles for key in ('actual_cost_cny_exact', 'reference_cost_cny_exact', 'evidence_task_id', 'image_contract_evidence')),
            'Private provider billing leaked into public catalog')
    capability_profiles = set()
    for row in rows:
        if row['id'] not in MODELS:
            continue
        require(row.get('available') is True and 'text' in row.get('operation_modes', []), 'Existing text mode is unavailable')
        image = row.get('image_reference') or {}
        require(image.get('available') is (row['id'] in IMAGE_MODELS), 'Undocumented image capability advertised or documented one missing')
        for item in image.get('specifications') or []:
            capability_profiles.add((item['model'], item['operation_mode'], item['image_count'], item['resolution'], item['duration']))
        require(set(row.get('operation_modes') or []) == ({'text', 'reference', 'all_reference'} if row['id'] in IMAGE_MODELS else {'text'}),
                'Capability advertised an unapproved operation mode')
        require(not any((row.get(field) or {}).get('available') for field in ('reference_video', 'reference_audio', 'reference_video_audio')),
                'Undocumented video/audio-reference capability advertised')
        if row['id'] not in IMAGE_MODELS:
            require(row.get('operation_modes') == ['text'], 'Undocumented model must remain text-only')
    require(capability_profiles == expected, 'Capability and price image tuples differ')
    body = reference_body('issue186-invalid-sha-free')
    body['image_identities'] = ['0' * 64]
    code, rejected = fetch(base + '/v1/videos', token=accounts[0]['key'], body=body)
    require(code == 409, 'Actual content SHA mismatch must conflict before reservation')
    require(wallet(accounts[0]) == before, 'Invalid asset changed canary wallet/task/log accounting')
    private_json(PRIVATE / 'free-verification.json', {'at': int(time.time()), 'catalogs': observed, 'rejection': rejected, 'wallet': before, 'passed': True})
    print(json.dumps({'free_checks_passed': True, 'text_models': 7, 'image_profiles': 6, 'invalid_asset_status': code, 'wallet_delta': 0}))


def submit() -> None:
    """Allocate conservative budget durably; never repeat any uncertain intent."""
    import fcntl

    base = isolated_base()
    require((PRIVATE / 'free-verification.json').exists(), 'Free acceptance must complete before paid submission')
    account = json.loads((PRIVATE / 'accounts.json').read_text())[0]
    body = reference_body('issue186-canary-public-wallet-one')
    record = {'state': 'submitting', 'at': int(time.time()), 'request': body,
              'wallet_before': wallet(account), 'budget_exposure_cny': str(EXPOSURE), 'upstream_task_count_max': 1}
    lock = os.open(EVIDENCE / 'budget.lock', os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(not INTENT.exists(), 'Paid intent already exists; poll it, never resubmit')
        used = sum((Decimal(str(json.loads(p.read_text()).get('budget_exposure_cny', CAP)))
                    for p in EVIDENCE.glob('grok-*.json')), Decimal(0))
        require(used + EXPOSURE <= CAP, 'Approved CNY50 test budget would be exceeded')
        record['total_budget_exposure_cny'] = str(used + EXPOSURE)
        private_json(INTENT, record, exclusive=True)
    finally:
        os.close(lock)
    try:
        code, response = fetch(base + '/v1/videos', token=account['key'], body=body)
        record.update(http_status=code, response=response, state='accepted' if code in (200, 202) and response.get('id') else 'rejected_or_uncertain')
    except Exception as error:
        record.update(state='outcome_unconfirmed', error_type=type(error).__name__)
    private_json(INTENT, record)
    print(json.dumps({'state': record['state'], 'http_status': record.get('http_status'),
                      'id': (record.get('response') or {}).get('id'), 'budget_exposure_cny': str(EXPOSURE), 'resubmit_allowed': False}))


def probe_media(video_path: Path) -> dict:
    """Probe the exact private MP4 in a bounded, offline candidate container."""
    require(video_path == PRIVATE / 'public-wallet-result.mp4' and video_path.is_file()
            and not video_path.is_symlink(), 'Only the exact private canary MP4 may be probed')
    operation = secrets.token_hex(16)
    name = 'xtai-issue186-ffprobe-' + operation
    args = ['docker', 'run', '--rm', '--pull', 'never', '--name', name,
            '--label', 'com.aixingtuyun.task=issue186-canary-media-proof',
            '--label', 'com.aixingtuyun.probe-operation=' + operation,
            '--network', 'none', '--read-only', '--cap-drop', 'ALL',
            '--security-opt', 'no-new-privileges', '--memory', '128m',
            '--cpus', '1', '--pids-limit', '64', '--user', '0:0',
            '--mount', 'type=bind,source=' + str(video_path) + ',target=/probe.mp4,readonly',
            '--entrypoint', 'ffprobe', 'xtai/video-job-gateway:issue186',
            '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams',
            '-show_format', '-of', 'json', '/probe.mp4']
    try:
        result = subprocess.run(args, text=True, capture_output=True, timeout=45)
        require(result.returncode == 0 and len(result.stdout) <= 1024 * 1024,
                'Offline candidate media probe failed; inspect preserved MP4')
        return json.loads(result.stdout)
    finally:
        # --rm handles normal exit. A timed-out Docker client can leave its
        # container behind; remove only our exact name AND unique ownership tag.
        checked = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True, timeout=10)
        if checked.returncode == 0:
            info = json.loads(checked.stdout)[0]
            labels = info['Config'].get('Labels') or {}
            require(labels.get('com.aixingtuyun.task') == 'issue186-canary-media-proof'
                    and labels.get('com.aixingtuyun.probe-operation') == operation,
                    'Probe container ownership differs; do not clean it up')
            removed = subprocess.run(['docker', 'rm', '-f', name], text=True, capture_output=True, timeout=10)
            require(removed.returncode == 0, 'Owned probe container cleanup needs attention')
        else:
            absent = subprocess.run(['docker', 'ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}'], text=True, capture_output=True, timeout=10)
            require(absent.returncode == 0 and not absent.stdout.strip(), 'Owned probe absence could not be verified')


def poll() -> None:
    """Observe one task, verify exact fixture ledger and authenticated AV download."""
    base = isolated_base()
    accounts = json.loads((PRIVATE / 'accounts.json').read_text())
    intent = json.loads(INTENT.read_text())
    task_id = (intent.get('response') or {}).get('id')
    if not task_id:
        # Read the durable owner/request ledger after a timed-out original POST.
        task_id = sql("SELECT id FROM public_video_tasks WHERE user_id=" + str(int(accounts[0]['user_id']))
                      + " AND client_request_id='issue186-canary-public-wallet-one';")
        require(bool(task_id) and '\n' not in task_id, 'Unconfirmed submission has no unique local task; do not resubmit')
    require(re.fullmatch(r'vjob_[0-9a-f]{32}', task_id) is not None, 'Unexpected public task identity')
    code, result = fetch(base + '/v1/videos/' + task_id, token=accounts[0]['key'])
    require(code == 200, 'Owner task observation failed')
    cross, _ = fetch(base + '/v1/videos/' + task_id, token=accounts[1]['key'])
    cross_content, _ = fetch(base + '/v1/videos/' + task_id + '/content', token=accounts[1]['key'])
    require(cross == 404 and cross_content == 404, 'Cross-user task/content isolation failed')
    private_json(PRIVATE / 'last-poll.json', result)
    billing = result.get('billing') or {}
    if billing.get('status') != 'settled' or result.get('result_delivery') != 'ready':
        print(json.dumps({'id': task_id, 'status': result.get('status'), 'billing_status': billing.get('status'),
                          'result_delivery': result.get('result_delivery'), 'cross_user': 404, 'resubmit_allowed': False}))
        return
    require(isinstance(billing.get('charged_amount'), str), 'Settled charged amount must be an exact string')
    charged = Decimal(billing['charged_amount'])
    require(charged.is_finite() and charged > 0, 'Successful media requires positive settled actual charge')
    quota = int((charged * RATE).to_integral_value(rounding=ROUND_CEILING))
    expected = [str(QUOTA - quota) + '|' + str(quota) + '|1', str(QUOTA - quota) + '|' + str(quota), '1', '1']
    require(wallet(accounts[0]) == expected, 'Settled user/token quota/count delta differs from exact charged amount')
    require(wallet(accounts[1])[:2] == [str(QUOTA) + '|0|0', str(QUOTA) + '|0'], 'Other user wallet was changed')
    rows = sql("SELECT charged_quota,charged_cny,state FROM public_video_tasks WHERE id='" + task_id + "'; SELECT quota FROM logs WHERE request_id='" + task_id + "' AND type=2;").splitlines()
    require(rows == [str(quota) + '|' + format(charged, '.6f') + '|settled', str(quota)], 'Task ledger and unique consume log differ')
    request = urllib.request.Request(base + '/v1/videos/' + task_id + '/content', headers={'Authorization': 'Bearer ' + accounts[0]['key']})
    video_path = PRIVATE / 'public-wallet-result.mp4'
    with urllib.request.build_opener(NoRedirect).open(request, timeout=60) as response:
        require(response.status == 200, 'Authenticated media download failed')
        media = response.read(64 * 1024 * 1024 + 1)
        require(0 < len(media) <= 64 * 1024 * 1024, 'Media response outside bounded size')
    fd = os.open(video_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(media)
    probe = probe_media(video_path)
    streams = probe.get('streams') or []
    require({'video', 'audio'}.issubset({s.get('codec_type') for s in streams}), 'Downloaded result lacks required video/audio streams')
    proof = {'bytes': len(media), 'sha256': hashlib.sha256(media).hexdigest(), 'streams': streams,
             'has_video': True, 'has_audio': True, 'charged_cny': format(charged, '.6f'), 'quota_delta': quota,
             'wallet_delta_exact': True, 'cross_user': 404, 'task_id': task_id}
    private_json(PRIVATE / 'settlement-acceptance.json', proof)
    print(json.dumps({key: proof[key] for key in ('task_id', 'bytes', 'has_video', 'has_audio', 'charged_cny', 'quota_delta', 'wallet_delta_exact', 'cross_user')}))


def main() -> None:
    """Expose only scoped task actions; no cleanup or promotion side effects."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'verify-free', 'submit', 'poll'))
    args = parser.parse_args()
    require(sys.platform.startswith('linux'), 'Server-local Linux helper only; do not run against a workstation')
    {'prepare': prepare, 'verify-free': verify_free, 'submit': submit, 'poll': poll}[args.action]()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Source values/HTTP bodies/DSNs never appear in exception output.
        print(json.dumps({'needs_attention': True, 'error_type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'Private server-local evidence needs inspection',
                          'preserved_private_state': str(PRIVATE), 'resubmit_allowed': False}))
        raise SystemExit(1)
