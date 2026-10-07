"""Scoped expansion acceptance: prepare fixtures, one durable submit, GET polls.

Current user authority: existing Nody wallet only, no recharge; originalCNY50
cumulative generation-test ceiling. Never repeat an existing/uncertain intent.
"""
from decimal import Decimal
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
import uuid

ROOT = Path('/opt/ai-api-stack/backups/nody-multimodal-186-20261007')
DATA = ROOT / 'expanded-inputs'
ORIGIN = 'https://nodyhub.com'
CAP = Decimal('50')
IDS = ('wan3.0-video', 'wan3.0-video-prime', 'omni-flash')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def save(path, value, exclusive=False):
    """Install protected evidence atomically, with exclusive pre-submit intent."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    target = path if exclusive else path.with_suffix('.tmp')
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC), 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    if not exclusive:
        os.replace(target, path)


def env(name):
    info = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
    return dict(value.split('=', 1) for value in info['Config']['Env'])


def session():
    value = json.loads(Path('/opt/xtai/secrets/video-billing/nodyhub-session.json').read_text())
    return {key: str(value[field]) for key, field in [('Cookie', 'cookie'), ('Authorization', 'authorization'),
            ('New-Api-User', 'new_api_user')] if value.get(field)}


def fetch(path, body=None, headers=None):
    req = urllib.request.Request(ORIGIN + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(req, timeout=35) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError('Provider response exceeds bounded size')
        return json.loads(raw)


def upload(path, mime):
    """Upload only the generated/previously accepted task-owned fixture."""
    values = env('media_upload-media-upload-1')
    raw = path.read_bytes()
    request = urllib.request.Request('https://upload.aixingtuyun.com/v1/upload', data=raw, method='POST',
             headers={'Authorization': 'Bearer ' + values['UPLOAD_TOKEN'], 'Content-Type': mime,
                      'X-File-Name': 'issue186-expansion' + path.suffix})
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
        value = json.load(response)
    return {'url': value['url'], 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw),
            'mime_type': mime, 'expires_in': value.get('expires_in')}


def prepare():
    """Offline media fixture preparation and uploads, never generation."""
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = DATA / 'assets.json'
    if target.exists():
        print(json.dumps({'fixtures_exist': True, 'paid_submissions': 0})); return
    source = ROOT / 'canary/public-wallet-result.mp4'
    if not source.is_file():
        raise RuntimeError('Accepted canary fixture is missing')
    image = json.loads((ROOT / 'assets.json').read_text())[0]
    video = DATA / 'reference.mp4'
    audio = DATA / 'reference.mp3'
    for output, arguments in [(video, ['-stream_loop', '-1', '-i', '/source.mp4', '-t', '2', '-c:v', 'libx264', '-c:a', 'aac', '-movflags', '+faststart']),
                              (audio, ['-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2', '-c:a', 'libmp3lame'])]:
        result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL',
                 '--security-opt', 'no-new-privileges', '--memory', '256m', '--cpus', '1',
                 '-v', str(source) + ':/source.mp4:ro', '-v', str(DATA) + ':/fixtures', '--entrypoint', 'ffmpeg',
                 'xtai/video-job-gateway:issue186', '-v', 'error', '-y', *arguments, '/fixtures/' + output.name],
                 capture_output=True, timeout=35)
        if result.returncode != 0:
            raise RuntimeError('Offline fixture conversion failed')
        os.chmod(output, 0o600)
    v = upload(video, 'video/mp4'); a = upload(audio, 'audio/mpeg')
    save(target, {'image': image, 'video': v, 'audio': a})
    print(json.dumps({'fixtures_prepared': True, 'paid_submissions': 0}))


def submit(model):
    """Reserve funded conservative exposure and submit exactly once."""
    if model not in IDS:
        raise RuntimeError('Unapproved model')
    path = DATA / (model + '.json')
    values = env('xtai-video-public-execution')
    assets = json.loads((DATA / 'assets.json').read_text())
    # Omni's input-vs-output billing basis is not established by the installed
    # duration transform. Until a current exact tariff proves otherwise, cover
    # the larger requested4s output at the public maximum1.6 credits/s x CNY1.5.
    ceiling = {'wan3.0-video': Decimal('4.8'), 'wan3.0-video-prime': Decimal('7.2'), 'omni-flash': Decimal('9.6')}[model]
    body = {'model': model, 'prompt': 'Keep the colored ball recognizable; gentle motion and retain natural sound.',
            'duration': 2 if model.startswith('wan') else 4, 'resolution': '480P' if model.startswith('wan') else '720p'}
    if model.startswith('wan'):
        body.update(generation_type='reference', size='16:9', audio=True, watermark=False,
                    image_urls=[assets['image']['url']], video_urls=[assets['video']['url']], audio_urls=[assets['audio']['url']])
    else:
        body.update(aspect_ratio='16:9', video_urls=[assets['video']['url']])
    lock = os.open(ROOT / 'budget.lock', os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            raise RuntimeError('Intent exists; query original task, never resubmit')
        prior = sum((Decimal(str(json.loads(p.read_text()).get('budget_exposure_cny', CAP))) for p in ROOT.glob('grok-*.json')), Decimal('0.45'))
        prior += sum((Decimal(str(json.loads(p.read_text()).get('budget_exposure_cny', CAP))) for p in DATA.glob('*.json') if p.name != 'assets.json'), Decimal(0))
        wallet = fetch('/api/user/self', headers=session())
        if wallet.get('success') is not True:
            raise RuntimeError('Current wallet authorization unavailable')
        funded = Decimal(wallet['data']['quota']) / Decimal(500000) * Decimal(values['VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD'])
        if prior + ceiling > CAP or funded < ceiling:
            raise RuntimeError('Funded wallet or cumulative authorized ceiling is insufficient for conservative test exposure')
        record = {'model': model, 'request': body, 'state': 'submitting', 'at': int(time.time()),
                  'budget_exposure_cny': str(ceiling), 'funded_before_cny': str(funded)}
        save(path, record, exclusive=True)
    finally:
        os.close(lock)
    try:
        record['response'] = fetch('/v2/videos/generations', body=body,
                            headers={'Authorization': 'Bearer ' + values['VIDEO_JOB_NODYHUB_API_KEY']})
        record['state'] = 'attempt_recorded'
    except urllib.error.HTTPError as error:
        record.update(state='rejected_or_uncertain', http_status=error.code)
        try: record['response'] = json.loads(error.read())
        except ValueError: record['error_type'] = 'non_json_response'
    except Exception as error:
        record.update(state='outcome_unconfirmed', error_type=type(error).__name__)
    save(path, record)
    response = record.get('response') or {}
    print(json.dumps({'model': model, 'state': record['state'], 'id': response.get('id') or response.get('task_id'),
                      'http_status': record.get('http_status'), 'resubmit_allowed': False}))


def poll(model):
    """Read original task and exact authenticated cost, no resubmission."""
    path = DATA / (model + '.json'); record = json.loads(path.read_text())
    raw = record.get('response') or {}; task = raw.get('id') or raw.get('task_id') or (raw.get('data') or {}).get('id')
    if not task:
        raise RuntimeError('No confirmed upstream task; preserve uncertainty')
    import re
    if not isinstance(task, str) or not re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', task):
        raise RuntimeError('Unexpected stored upstream identity; do not query a guessed task')
    values = env('xtai-video-public-execution')
    result = fetch('/v1/videos/' + task, headers={'Authorization': 'Bearer ' + values['VIDEO_JOB_NODYHUB_API_KEY']})
    query = fetch('/api/task/self?task_id=' + task + '&p=1&size=10', headers=session())
    items = (query.get('data') or {}).get('items') or []
    exact = [item for item in items if item.get('task_id') == task]
    record.update(last_poll=result, billing_query=query)
    cost = None
    if query.get('success') is True and len(exact) == 1 and exact[0].get('status') == 'SUCCESS':
        cost = Decimal(exact[0]['quota']) / Decimal(500000) * Decimal(values['VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD'])
        record.update(actual_cost_cny_exact=format(cost, '.6f'), budget_exposure_cny=format(cost, '.6f'))
    save(path, record)
    print(json.dumps({'model': model, 'id': task, 'status': result.get('status') or (result.get('data') or {}).get('status'),
                      'actual_cost_cny': str(cost) if cost is not None else None, 'resubmit_allowed': False}))


def prices():
    """Read current public per-model metadata and funded wallet without generating."""
    raw = fetch('/api/pricing')
    rows = raw.get('data') or []
    if isinstance(rows, dict):
        rows = rows.get('items') or rows.get('models') or []
    selected = [row for row in rows if isinstance(row, dict) and row.get('model_name') in IDS]
    wallet = fetch('/api/user/self', headers=session())
    if wallet.get('success') is not True:
        raise RuntimeError('Wallet authorization unavailable')
    values = env('xtai-video-public-execution')
    funded = Decimal(wallet['data']['quota']) / Decimal(500000) * Decimal(values['VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD'])
    print(json.dumps({'models': selected, 'funded_cny_exact': str(funded), 'paid_submissions': 0}, ensure_ascii=False))


def media_probe(path, task):
    """Probe task-owned files in a constrained offline decoder container."""
    name = 'xtai-issue186-expand-proof-' + uuid.uuid4().hex
    labels = {'xtai.task.issue': '186', 'xtai.task.role': 'expansion-proof', 'xtai.task.original-id': task}
    command = ['docker', 'run', '--rm', '--pull', 'never', '--name', name, '--network', 'none', '--read-only',
               '--user', '0:0', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '64',
               '--memory', '256m', '--cpus', '1', '--mount', 'type=bind,src=' + str(path) + ',dst=/media/input,readonly']
    for key, value in labels.items():
        command.extend(['--label', key + '=' + value])
    command.extend(['--entrypoint', 'ffprobe', 'xtai/video-job-gateway:issue186', '-v', 'error',
                    '-protocol_whitelist', 'file,pipe', '-show_entries',
                    'format=format_name,duration:stream=codec_type,codec_name,width,height,sample_rate,channels',
                    '-of', 'json', '/media/input'])
    try:
        return json.loads(subprocess.check_output(command, timeout=30, stderr=subprocess.DEVNULL))
    finally:
        checked = subprocess.run(['docker', 'inspect', name], capture_output=True, timeout=10)
        if checked.returncode == 0:
            rows = json.loads(checked.stdout)
            if (len(rows) != 1 or rows[0].get('Name') != '/' + name
                    or any((rows[0]['Config'].get('Labels') or {}).get(key) != value for key, value in labels.items())):
                raise RuntimeError('Offline sandbox cleanup identity mismatch')
            subprocess.run(['docker', 'rm', '--force', name], capture_output=True, check=True, timeout=10)
        else:
            absent = subprocess.check_output(['docker', 'ps', '-a', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}'], timeout=10)
            if absent.strip():
                raise RuntimeError('Offline sandbox cleanup not confirmed')


def verify(model):
    """Verify the original paid task's matching bill and bounded MP4 delivery."""
    path = DATA / (model + '.json')
    record = json.loads(path.read_text())
    task = (record.get('response') or {}).get('id')
    bills = (record.get('billing_query') or {}).get('data') or {}
    exact = [row for row in bills.get('items', []) if row.get('task_id') == task and row.get('status') == 'SUCCESS']
    if not task or len(exact) != 1 or not record.get('actual_cost_cny_exact'):
        raise RuntimeError('Original successful task and exact authenticated bill are required')
    sys.path.insert(0, '/opt/ai-api-stack/releases/issue186-nody-multimodal/source/ops/video-job-gateway')
    from nodyhub import result_url
    from reference_contract import ReferenceMediaVerifier
    values = env('xtai-video-public-execution')
    hosts = tuple(host.strip().lower() for host in values['VIDEO_JOB_NODYHUB_RESULT_HOSTS'].split(',') if host.strip())
    payload = record['last_poll']
    url = result_url(payload)
    if not url and isinstance(payload.get('output'), str):
        url = payload['output'].strip()
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    if (parsed.scheme != 'https' or not host or parsed.username or parsed.password or parsed.fragment
            or parsed.port not in (None, 443) or not any(host == allowed or host.endswith('.' + allowed) for allowed in hosts)):
        raise RuntimeError('Original result URL is not a permitted public HTTPS destination')
    verifier = ReferenceMediaVerifier(hosts, timeout_seconds=45)
    addresses = verifier._public_dns_addresses(host, 'delivery')
    deadline = time.monotonic() + 45
    maximum = 64 * 1024 * 1024
    digest = hashlib.sha256()
    total = 0
    with tempfile.NamedTemporaryFile(prefix='xtai-issue186-expansion-', suffix='.mp4') as target:
        with verifier._open_pinned(url, host, addresses, {'Accept': 'video/mp4,application/octet-stream'}, 'delivery', deadline=deadline) as response:
            declared = response.headers.get('Content-Length')
            mime = response.headers.get_content_type()
            if response.status != 200 or mime not in ('video/mp4', 'application/octet-stream'):
                raise RuntimeError('Original result is not a successful MP4 response')
            if declared is not None and not 0 < int(declared) <= maximum:
                raise RuntimeError('Original result exceeds the bounded delivery size')
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError('Original result delivery deadline exceeded')
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    sock.settimeout(min(15, remaining))
                chunk = response.read1(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise RuntimeError('Original result exceeds the bounded delivery size')
                target.write(chunk)
                digest.update(chunk)
            if not total or (declared is not None and total != int(declared)):
                raise RuntimeError('Original result body length does not match')
        target.flush()
        target.seek(0)
        magic = target.read(16)
        if len(magic) < 12 or magic[4:8] != b'ftyp':
            raise RuntimeError('Original result MP4 signature missing')
        result = media_probe(target.name, task)
    streams = result.get('streams') or []
    seconds = Decimal(str((result.get('format') or {}).get('duration') or '0'))
    if (not any(item.get('codec_type') == 'video' for item in streams)
            or not any(item.get('codec_type') == 'audio' for item in streams)
            or abs(seconds - Decimal(record['request']['duration'])) > Decimal('.2')):
        raise RuntimeError('Output tracks or requested duration do not match the acceptance tuple')
    inputs = {}
    for kind, suffix in (('video', 'mp4'), ('audio', 'mp3')):
        if record['request'].get(kind + '_urls'):
            inputs[kind] = media_probe(DATA / ('reference.' + suffix), task)
    record.update(state='accepted', media_probe=result, input_probe=inputs,
                  delivery_check={'http_status': 200, 'bytes': total, 'sha256': digest.hexdigest(),
                                  'audio_video': True, 'duration_matches': True, 'checked_at': int(time.time())})
    save(path, record)
    print(json.dumps({'model': model, 'id': task, 'status': 'accepted', 'actual_cost_cny_exact': record['actual_cost_cny_exact'],
                      'duration_seconds': str(seconds), 'input_probe': inputs, 'bytes': total, 'paid_submissions': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['prepare', 'submit', 'poll', 'verify', 'prices']); parser.add_argument('--model', choices=IDS)
    args = parser.parse_args()
    try:
        {'prepare': prepare, 'prices': prices, 'submit': lambda: submit(args.model), 'poll': lambda: poll(args.model), 'verify': lambda: verify(args.model)}[args.action]()
    except Exception as error:
        print(json.dumps({'needs_attention': True, 'type': type(error).__name__,
                          'reason': str(error) if isinstance(error, RuntimeError) else 'Private test evidence needs inspection',
                          'resubmit_allowed': False})); raise SystemExit(1)
