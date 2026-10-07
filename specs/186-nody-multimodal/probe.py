"""Scoped server-only research/paid probes; default action never generates video."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import urllib.error
import urllib.request
import zlib
from decimal import Decimal

ROOT = Path('/opt/ai-api-stack/backups/nody-multimodal-186-20261007')
KEY_FILE = Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-issue186.key')
ORIGIN = 'https://nodyhub.com'
MODELS = ('grok-video-3', 'grok-imagine-1.5-video', 'grok-imagine-video-official')
CAP_CNY = 50


def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def session_headers():
    value = json.loads(Path('/opt/xtai/secrets/video-billing/nodyhub-session.json').read_text())
    return {key: str(value[name]) for key, name in [('Cookie', 'cookie'), ('Authorization', 'authorization'), ('New-Api-User', 'new_api_user')] if value.get(name)}


def request(path, *, body=None, headers=None, method=None):
    req = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})}, method=method)
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.load(response)


def fixture(color):
    width, height = 1024, 576
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            rows.extend(color if (x - 512) ** 2 + (y - 288) ** 2 < 100 ** 2 else (245, 245, 245))
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')


def prepare():
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    headers = session_headers()
    user = request('/api/user/self', headers=headers)
    if user.get('success') is not True:
        raise RuntimeError('Existing Nody session is not valid')
    quota = int(user['data']['quota'])
    funded_cap = min(16_666_666, quota - 5000)
    if funded_cap <= 0:
        raise RuntimeError('No funded quota for approved tests')
    listing = request('/api/token/?p=1&size=100', headers=headers)
    if listing.get('success') is not True:
        raise RuntimeError('Token listing rejected')
    items = listing.get('data') or {}
    items = items.get('items', []) if isinstance(items, dict) else items
    found = [t for t in items if t.get('name') == 'xtai-issue186-image-tests']
    if not found:
        created = request('/api/token/', headers=headers, body={
            'name': 'xtai-issue186-image-tests', 'expired_time': int(time.time()) + 7200,
            'remain_quota': funded_cap, 'unlimited_quota': False, 'model_limits_enabled': True,
            'model_limits': ','.join(MODELS), 'allow_ips': '156.239.3.210', 'group': '默认通道', 'cross_group_retry': False,
        })
        if created.get('success') is not True:
            raise RuntimeError('Dedicated test-key creation rejected')
        listing = request('/api/token/?p=1&size=100', headers=headers)
        items = listing.get('data') or {}
        items = items.get('items', []) if isinstance(items, dict) else items
        found = [t for t in items if t.get('name') == 'xtai-issue186-image-tests']
    if len(found) != 1:
        raise RuntimeError('Ambiguous test key identity')
    token = found[0]
    key = token['key']
    if '*' in key:
        raise RuntimeError('API returned masked test key')
    key = key if key.startswith('sk-') else 'sk-' + key
    if KEY_FILE.exists():
        if KEY_FILE.read_text().strip() != key:
            raise RuntimeError('Existing private test key differs')
    else:
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(key)
    private_json(ROOT / 'test-key-metadata.json', {k: token.get(k) for k in ['id', 'name', 'status', 'expired_time', 'remain_quota', 'unlimited_quota', 'allow_ips', 'model_limits']})
    asset_file = ROOT / 'assets.json'
    if not asset_file.exists():
        info = json.loads(subprocess.check_output(['docker', 'inspect', 'media_upload-media-upload-1']))[0]
        env = dict(item.split('=', 1) for item in info['Config']['Env'])
        assets = []
        for color in [(35, 100, 220), (210, 65, 35)]:
            data = fixture(color)
            req = urllib.request.Request('https://upload.aixingtuyun.com/v1/upload', data=data, method='POST', headers={
                'Authorization': 'Bearer ' + env['UPLOAD_TOKEN'], 'Content-Type': 'image/png', 'X-File-Name': 'issue186-fixture.png',
            })
            with urllib.request.urlopen(req, timeout=30) as response:
                result = json.load(response)
            assets.append({'url': result['url'], 'identity': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'expires_in': result.get('expires_in')})
        private_json(asset_file, assets)
    print(json.dumps({'prepared': True, 'test_key_id': token['id'], 'funded_credit_quota': funded_cap,
                      'maximum_test_cost_cny': CAP_CNY, 'asset_count': len(json.loads(asset_file.read_text())), 'paid_submissions': 0}))


def submit(model, image_count):
    label = model + '-' + str(image_count)
    record_path = ROOT / (label + '.json')
    if record_path.exists():
        raise RuntimeError('This case already has an attempt record; poll it, never resubmit')
    key = KEY_FILE.read_text().strip()
    assets = json.loads((ROOT / 'assets.json').read_text())[:image_count]
    if image_count not in (1, 2) or len(assets) != image_count or model not in MODELS:
        raise RuntimeError('Unapproved test shape')
    # Conservative catalog ceiling including a possible2x group factor; track
    # unresolved exposure at that ceiling rather than treating missing cost as0.
    ceiling = Decimal('2.4') if model == 'grok-imagine-video-official' else Decimal('3.6')
    body = {'model': model, 'prompt': 'A simple colored ball moves gently on a plain white background, minimal motion.', 'duration': 1 if model == 'grok-imagine-video-official' else 6}
    urls = [a['url'] for a in assets]
    if model == 'grok-video-3':
        body.update(resolution='720P', images=urls)
        if image_count == 2: body['ratio'] = '16:9'
    elif model == 'grok-imagine-1.5-video':
        body.update(quality='720p', image_urls=urls)
        if image_count == 2: body['size'] = '16:9'
    else:
        body['resolution'] = '480p'
        if image_count == 1: body['image'] = {'url': urls[0]}
        else: body.update(reference_images=[{'url': url} for url in urls], aspect_ratio='16:9')
    record = {'model': model, 'image_count': image_count, 'request': body, 'started_at': int(time.time()),
              'state': 'submitting', 'budget_exposure_cny': str(ceiling)}
    # Serialize budget allocation, then exclusively create the attempt intent.
    # Network submission occurs only after this durable record is installed.
    lock_fd = os.open(ROOT / 'budget.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        exposure = sum((Decimal(str(json.loads(p.read_text()).get('budget_exposure_cny', CAP_CNY))) for p in ROOT.glob('grok-*.json')), Decimal(0))
        if exposure + ceiling > CAP_CNY:
            raise RuntimeError('CNY50 test budget exhausted')
        fd = os.open(record_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(lock_fd)
    try:
        record['response'] = request('/v2/videos/generations', body=body, headers={'Authorization': 'Bearer ' + key})
        record['state'] = 'attempt_recorded'
    except urllib.error.HTTPError as error:
        record['http_status'] = error.code
        try: record['response'] = json.loads(error.read())
        except Exception: record['error_type'] = 'non_json_http_error'
        record['state'] = 'rejected_or_uncertain'
    except Exception as error:
        record.update(state='outcome_unconfirmed', error_type=type(error).__name__)
    private_json(record_path, record)
    response = record.get('response') or {}
    print(json.dumps({'case': label, 'state': record['state'], 'http_status': record.get('http_status'),
                      'id': response.get('id') or response.get('task_id'), 'status': response.get('status'),
                      'error': str(response.get('error') or '').replace(key, '[redacted]')[:400], 'budget_exposure_cny': str(ceiling)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'submit'])
    parser.add_argument('--model', choices=MODELS)
    parser.add_argument('--image-count', type=int, default=1)
    args = parser.parse_args()
    if args.action == 'prepare': prepare()
    else: submit(args.model, args.image_count)
