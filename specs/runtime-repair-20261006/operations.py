"""Scoped issue183 backups, isolated verification and guarded deployment."""
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import time
import secrets
import urllib.parse
import requests
import yaml

ROOT = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
BACKUP = pathlib.Path('/opt/ai-api-stack/backups/issue183-runtime-20261006')
MONITOR = pathlib.Path('/opt/ai-api-stack/channel-monitor')
LIVE_SOURCE = pathlib.Path('/opt/ai-api-stack/releases/issue180-p1/source')
IMAGE = 'new-api-fixed:issue183-runtime'
OLD_IMAGE = 'new-api-fixed:issue180-token-default'
DB = 'xtai_issue183_canary'
NAME = 'xtai-issue183-canary'
FAKE = 'xtai-issue183-fake'
GPT61_EXPR = 'len <= 272000 ? tier("base", p * 3 + c * 15 + cr * 0.15 + cc * 3.75) : tier("over_272000", p * 6 + c * 22.5 + cr * 0.3 + cc * 7.5)'
spec = importlib.util.spec_from_file_location('h', '/opt/ai-api-stack/releases/issue173-db-authority/deploy_public.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
h.BACKUP = BACKUP


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def rows(query, db='new-api'):
    return json.loads(h.sql("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM (" + query + ') t;', db))


def prepare():
    marker = BACKUP / 'prepared.json'
    if marker.exists():
        print('issue183 backup already verified')
        return
    BACKUP.mkdir(mode=0o700, exist_ok=True)
    os.chmod(BACKUP, 0o700)
    before = h.inspect(h.NATIVE)
    assert before['Config']['Image'] == OLD_IMAGE, 'unexpected production image'
    dump = BACKUP / 'database.before.dump'
    with os.fdopen(os.open(dump, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as handle:
        subprocess.run(['docker', 'exec', h.PG, 'pg_dump', '-U', 'newapi', '-d', 'new-api', '-Fc'], stdout=handle, check=True)
    h.private(BACKUP / 'native.before.json', before)
    for name in ['docker-compose.yml', 'docker-compose.override.yml']:
        shutil.copy2('/opt/ai-api-stack/' + name, BACKUP / name)
        os.chmod(BACKUP / name, 0o600)
    shutil.copytree(LIVE_SOURCE, ROOT / 'source', ignore=shutil.ignore_patterns('.git', '__pycache__'))
    stage = ROOT / 'monitor-stage'
    for name in ['scripts', 'config', 'data', 'secrets']:
        if (MONITOR / name).exists():
            shutil.copytree(MONITOR / name, stage / name, ignore=shutil.ignore_patterns('__pycache__'))
    for name in ['upstream-credentials.json', 'upstreams.json'] + [p.name for p in MONITOR.glob('*.py')]:
        if (MONITOR / name).exists():
            shutil.copy2(MONITOR / name, stage / name)
            os.chmod(stage / name, 0o600)
    gateway = pathlib.Path('/opt/xtai-image-job-gateway')
    shutil.copy2(gateway / 'app.py', BACKUP / 'image-gateway.before.py')
    data = next((gateway / 'data').glob('*.sqlite*'))
    with sqlite3.connect('file:' + str(data) + '?mode=ro', uri=True) as source:
        with sqlite3.connect(str(BACKUP / 'image-jobs.before.sqlite3')) as destination:
            source.backup(destination)
    os.chmod(BACKUP / 'image-jobs.before.sqlite3', 0o600)
    h.private(BACKUP / 'image-gateway.before.json', h.inspect('xtai-image-job-gateway-image-job-gateway-1'))
    h.private(BACKUP / 'channels-options.before.json', {
        'channels': rows('SELECT * FROM channels WHERE id IN (38,39,53,54)'),
        'abilities': rows('SELECT * FROM abilities WHERE channel_id IN (38,39,53,54)'),
        'options': rows("SELECT * FROM options WHERE key IN ('GroupRatio','ModelPrice','ModelRatio','CompletionRatio','CacheRatio','CreateCacheRatio') OR key LIKE 'billing_setting.%'"),
    })
    digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    h.private(marker, {'prepared_at': int(time.time()), 'database_bytes': dump.stat().st_size, 'database_sha256': digest, 'native_image': OLD_IMAGE})
    print(json.dumps({'private_backup_verified': True, 'database_bytes': dump.stat().st_size}))


def insert_channel(row, db, **changes):
    channel = dict(row)
    channel.update(changes)
    sql = 'INSERT INTO channels SELECT * FROM jsonb_populate_record(NULL::channels,' + quote(json.dumps(channel)) + '::jsonb);'
    h.sql(sql, db)
    for group in channel['group'].split(','):
        for model in channel['models'].split(','):
            h.sql('INSERT INTO abilities("group",model,channel_id,enabled,priority,weight) VALUES(' + quote(group) + ',' + quote(model) + ',' + str(channel['id']) + ',true,' + str(channel['priority']) + ',100);', db)
    return channel['id']


def set_option(key, value, db):
    h.sql('INSERT INTO options(key,value) VALUES(' + quote(key) + ',' + quote(json.dumps(value, ensure_ascii=False)) + ') ON CONFLICT(key) DO UPDATE SET value=excluded.value;', db)


def merge_option(key, extra, db):
    old = json.loads(h.sql('SELECT value FROM options WHERE key=' + quote(key) + ';', db) or '{}')
    set_option(key, old | extra, db)


def canary():
    assert (BACKUP / 'prepared.json').exists()
    if not h.sql("SELECT 1 FROM pg_database WHERE datname=" + quote(DB)):
        h.sql('CREATE DATABASE ' + DB + ' OWNER newapi;')
        with (BACKUP / 'database.before.dump').open('rb') as stream:
            subprocess.run(['docker', 'exec', '-i', h.PG, 'pg_restore', '-U', 'newapi', '-d', DB, '--no-owner'], stdin=stream, capture_output=True, check=True)
    else:
        assert not rows("SELECT id FROM channels WHERE name LIKE 'issue183-%'", DB), 'partial native setup must be inspected before resuming'
    fake = subprocess.run(['docker', 'inspect', FAKE], capture_output=True, text=True)
    if fake.returncode == 0:
        previous = json.loads(fake.stdout)[0]
        assert not previous['State']['Running'] and any(m.get('Source') == str(ROOT / 'canary_upstream.py') for m in previous['Mounts'])
        subprocess.run(['docker', 'rm', FAKE], check=True, stdout=subprocess.DEVNULL)
    fake_image = h.inspect('xtai-image-job-gateway-image-job-gateway-1')['Config']['Image']
    subprocess.run(['docker', 'run', '-d', '--name', FAKE, '--network', 'app-net', '--memory', '64m', '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=1m', '-v', str(ROOT / 'canary_upstream.py') + ':/app.py:ro', '--entrypoint', 'python', fake_image, '/app.py'], check=True, stdout=subprocess.DEVNULL)
    values = h.env(h.inspect(h.NATIVE))
    dsn = urllib.parse.urlsplit(values['SQL_DSN'])
    values['SQL_DSN'] = urllib.parse.urlunsplit(dsn._replace(path='/' + DB))
    values.update(NODE_TYPE='slave', REDIS_CONN_STRING='', MEMORY_CACHE_ENABLED='false', SQL_MAX_OPEN_CONNS='5', SQL_MAX_IDLE_CONNS='1', SQL_MAX_LIFETIME='60')
    merge_option('billing_setting.billing_mode', {'gpt-6.1-sol': 'tiered_expr'}, DB)
    merge_option('billing_setting.billing_expr', {'gpt-6.1-sol': GPT61_EXPR}, DB)
    template = rows('SELECT * FROM channels WHERE id=23', DB)[0]
    fake_base = 'http://' + FAKE + ':19000'
    for model, primary, backup in [('canary-image-unsafe', 'uncertain', 'success'), ('canary-image-safe', 'quota', 'success'), ('canary-image-edit', 'unsupported', 'success')]:
        for path, priority in [(primary, 20), (backup, 10)]:
            channel_id = int(h.sql('SELECT coalesce(max(id),0)+1 FROM channels;', DB))
            setting = {'image_endpoints': ['/v1/images/generations']} if path == 'unsupported' else {}
            insert_channel(template, DB, id=channel_id, name='issue183-' + model + '-' + path, models=model, group='图', key='private-test-only', base_url=fake_base + '/' + path, priority=priority, model_mapping='', setting=json.dumps(setting), used_quota=0, status=1)
    merge_option('ModelPrice', {model: 0.01 for model in ['canary-image-unsafe', 'canary-image-safe', 'canary-image-edit']}, DB)
    key = json.loads((BACKUP / 'gpt61-key.json').read_text())
    channel_id = int(h.sql('SELECT coalesce(max(id),0)+1 FROM channels;', DB))
    insert_channel(template, DB, id=channel_id, name='Code Plan GPT6.1 verified', models='gpt-6.1-sol', group='文', key=key['key'], base_url='https://oh-code.me', priority=10, model_mapping='', setting='{}', used_quota=0, status=1)
    banana_template = rows('SELECT * FROM channels WHERE id=53', DB)[0]
    banana_id = channel_id + 1
    insert_channel(banana_template, DB, id=banana_id, name='Banana Pro · Rolldek 已验证1K', models='banana-pro', group='图香蕉', priority=10, model_mapping='', setting=json.dumps({'image_endpoints': ['/v1/images/generations'], 'image_sizes': ['1024x1024']}), used_quota=0, status=1)
    merge_option('ModelPrice', {'banana-pro': 0.8}, DB)
    h.run(NAME, IMAGE, values, extra=['--memory', '1g'])
    deadline = time.monotonic()+40
    base = h.address(NAME, 3000)
    while time.monotonic() < deadline:
        try:
            response = requests.get(base + '/api/status', timeout=2).json()
            if response.get('success') and response['data'].get('quota_db_authoritative') is True:
                break
        except requests.RequestException:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('isolated native not ready')
    create_canary_account()
    print('isolated production-source native ready; no customer balance changed')


def create_canary_account():
    assert h.inspect(NAME)['Config']['Image'] == IMAGE
    assert not (BACKUP / 'canary-account.json').exists()
    gpt_channel = rows("SELECT id FROM channels WHERE name='Code Plan GPT6.1 verified'", DB)
    banana_channel = rows("SELECT id FROM channels WHERE name='Banana Pro · Rolldek 已验证1K'", DB)
    assert len(gpt_channel) == len(banana_channel) == 1
    access = secrets.token_hex(16)
    uid = int(h.sql('INSERT INTO users(username,password,role,status,quota,used_quota,request_count,"group",aff_code,access_token,created_at) VALUES(' + quote('issue183-canary') + ',\'!nonlogin\',1,1,1000000,0,0,\'default\',' + quote(secrets.token_hex(4)) + ',' + quote(access) + ',' + str(int(time.time())) + ') RETURNING id;', DB).splitlines()[0])
    token = secrets.token_hex(24)
    tid = int(h.sql('INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) VALUES(' + str(uid) + ',' + quote(token) + ',1,\'issue183-canary\',' + str(int(time.time())) + ',0,-1,1000000,false,false,\'\',\'\',0,\'auto\',false) RETURNING id;', DB).splitlines()[0])
    h.private(BACKUP / 'canary-account.json', {'user_id': uid, 'token_id': tid, 'key': 'sk-' + token, 'access_token': access, 'gpt61_channel_id': gpt_channel[0]['id'], 'banana_channel_id': banana_channel[0]['id']})
    print('isolated non-login test account created')


def verify():
    account = json.loads((BACKUP / 'canary-account.json').read_text())
    headers = {'Authorization': 'Bearer ' + account['key']}
    base = h.address(NAME, 3000)
    initial = rows('SELECT quota,used_quota,request_count FROM users WHERE id=' + str(account['user_id']), DB)[0]
    response = requests.post(base + '/v1/images/generations', headers=headers, json={'model': 'canary-image-unsafe', 'prompt': 'local test', 'size': '1024x1024', 'n': 1}, timeout=20)
    assert response.status_code == 504, 'unknown image submission did not retain HTTP status'
    assert response.json()['error']['code'] == 'image_submit_uncertain'
    counts = requests.get(h.address(FAKE, 19000), timeout=3).json()
    assert counts.get('uncertain') == 1 and not counts.get('success'), 'ambiguous image request replayed'
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        wallet = rows('SELECT quota,used_quota,request_count FROM users WHERE id=' + str(account['user_id']), DB)[0]
        token = rows('SELECT remain_quota,used_quota FROM tokens WHERE id=' + str(account['token_id']), DB)[0]
        if wallet == initial and token == {'remain_quota': initial['quota'], 'used_quota': 0}:
            break
        time.sleep(.2)
    else:
        raise RuntimeError('failed image precharge was not refunded exactly once')
    outcome = [{'case': 'ambiguous image', 'status': 504, 'upstream_posts': 1, 'backup_posts': 0, 'refund_exact': True}]
    response = requests.post(base + '/v1/images/generations', headers=headers, json={'model': 'canary-image-safe', 'prompt': 'local test', 'size': '1024x1024', 'n': 1}, timeout=20)
    assert response.status_code == 200 and response.json().get('data'), 'definite quota rejection did not use a backup'
    assert response.headers['X-XingTu-Relay-Request-ID'] != 'fake-provider-id', 'upstream replaced native correlation ID'
    response = requests.post(base + '/v1/images/edits', headers=headers, data={'model': 'canary-image-edit', 'prompt': 'local test', 'size': '1024x1024', 'n': '1'}, files={'image': ('image.png', __import__('base64').b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='), 'image/png')}, timeout=20)
    assert response.status_code == 200 and response.json().get('data'), 'image edits compatible-route filter failed'
    counts = requests.get(h.address(FAKE, 19000), timeout=3).json()
    assert counts.get('quota') == 1 and counts.get('success') == 2 and not counts.get('unsupported'), 'wrong endpoint was submitted'
    outcome += [{'case': 'explicit quota rejection', 'backup_success': True}, {'case': 'unsupported edits', 'wrong_route_posts': 0}]
    catalog = requests.get(base + '/v1/models', headers=headers, timeout=10).json()
    ids = {row['id'] for row in catalog['data']}
    assert {'gpt-6.1-sol', 'gpt-6', 'banana-pro', 'banana-flash', 'claude-sonnet-5'} <= ids
    prices = requests.get(base + '/api/pricing', timeout=10).json()
    h.private(BACKUP / 'canary-pricing.json', prices)
    row = next(row for row in prices['data'] if row['model_name'] == 'gpt-6.1-sol')
    assert row.get('billing_mode') == 'tiered_expr' and row.get('billing_expr') == GPT61_EXPR, 'tariff was not published with its model'
    marker = BACKUP / 'gpt61-canary-response.json'
    assert not marker.exists(), 'a previous funded native probe must be reconciled, never repeated'
    h.private(marker, {'status': 'submitted_once', 'time': int(time.time())})
    start = time.monotonic()
    response = requests.post(base + '/v1/responses', headers=headers, json={'model': 'gpt-6.1-sol', 'input': 'Reply only OK.', 'max_output_tokens': 32, 'stream': False}, timeout=(8,90))
    body = response.json()
    probe = {'status': response.status_code, 'model': body.get('model'), 'usage': body.get('usage'), 'request_id': response.headers.get('X-XingTu-Relay-Request-ID'), 'seconds': round(time.monotonic()-start,2)}
    h.private(marker, probe)
    assert response.status_code == 200 and body.get('model') == 'gpt-6.1-sol' and body.get('output')
    usage = body['usage']; details = usage.get('input_tokens_details') or {}
    cached, write = details.get('cached_tokens', 0), max(details.get('cache_write_tokens', 0), details.get('cached_creation_tokens', 0))
    prompt = max(0, usage['input_tokens'] - cached - write)
    coef = [3,15,.15,3.75] if usage['input_tokens'] <= 272000 else [6,22.5,.3,7.5]
    from decimal import Decimal, ROUND_HALF_UP
    amount = Decimal(str(prompt*coef[0] + usage['output_tokens']*coef[1] + cached*coef[2] + write*coef[3])) * Decimal('.15') * Decimal('.5')
    expected = int(amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        bill = rows('SELECT quota,other FROM logs WHERE type=2 AND request_id=' + quote(probe['request_id']), DB)
        if bill:
            break
        time.sleep(.2)
    assert len(bill) == 1 and bill[0]['quota'] == expected, 'GPT6.1 actual native settlement differed from frozen expression'
    outcome.append({'case': 'GPT6.1 end-to-end', **probe, 'native_quota': expected, 'billing_exact': True, 'catalog_visible': True})
    h.private(BACKUP / 'canary-results.json', outcome)
    print(json.dumps(outcome, ensure_ascii=False))


if __name__ == '__main__':
    {'prepare': prepare, 'canary': canary, 'account': create_canary_account, 'verify': verify}[__import__('sys').argv[1]]()
