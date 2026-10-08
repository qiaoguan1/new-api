"""Prepare approved estimated-policy release; GET-only upstream, no promotion."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request

TARGETS = ('xtai-video-public-execution', 'xtai-video-job-gateway-v2-production', 'xtai-public-video-catalog176')
SECRETS = Path('/opt/xtai/secrets/video-billing')
EVIDENCE = Path('/opt/ai-api-stack/backups/nody-multimodal-186-20261007/expanded-inputs/wan3.0-video.json')
DIAGNOSTICS_ROOT: Path | None = None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(args, *, timeout=30):
    value = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if value.returncode != 0 and DIAGNOSTICS_ROOT is not None:
        atomic(DIAGNOSTICS_ROOT / ('command-error-' + str(time.time_ns()) + '.json'),
               {'argv': args, 'returncode': value.returncode, 'stdout': value.stdout[-16000:], 'stderr': value.stderr[-16000:]})
    require(value.returncode == 0, 'Scoped local command failed; private diagnostics need review')
    return value.stdout.strip()


def inspect(name):
    require(name in TARGETS, 'Unknown release target')
    return json.loads(run(['docker', 'inspect', name]))[0]


def values(name):
    return dict(row.split('=', 1) for row in inspect(name)['Config']['Env'])


def atomic(path, value, *, gateway=False):
    """Never overwrite another operation's protected configuration."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()
    if path.exists():
        require(not path.is_symlink() and path.read_bytes() == encoded, 'Existing operation artifact differs; do not overwrite')
        require(path.stat().st_mode & 0o777 == 0o600 and path.stat().st_uid == (10002 if gateway else os.geteuid()), 'Existing artifact ownership/mode differs; do not modify it')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(encoded); output.flush(); os.fsync(output.fileno())
    if gateway:
        os.chown(path, 10002, 999)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(path, headers):
    """Only these non-mutating, previously observed provider endpoints exist."""
    require(path in ('/api/pricing', '/api/user/self', '/api/token/?p=1&size=100'), 'No generation endpoint is allowed')
    request = urllib.request.Request('https://nodyhub.com' + path, headers=headers, method='GET')
    with urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect()).open(request, timeout=25) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
        require(response.status == 200 and len(raw) <= 2 * 1024 * 1024, 'Bounded upstream read unavailable')
    return json.loads(raw)


def configure(op, root, release):
    """Use fresh rate/group facts and the already-accepted original Wan receipt."""
    if (root / 'manifest.json').exists():
        print(json.dumps({'configuration_exists': True, 'operation_id': op, 'paid_requests': 0})); return
    env = values(TARGETS[0])
    legacy = values(TARGETS[1])
    require(all(legacy.get(key) == env.get(key) for key in ('VIDEO_JOB_NODYHUB_API_KEY', 'VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD', 'VIDEO_JOB_NODYHUB_BILLING_CREDENTIAL_FILE')), 'Two gateway upstream account/currency bindings differ')
    credentials = json.loads((SECRETS / 'nodyhub-session.json').read_text())
    headers = {key: str(credentials[field]) for key, field in [('Cookie', 'cookie'), ('Authorization', 'authorization'), ('New-Api-User', 'new_api_user')] if credentials.get(field)}
    require(fetch('/api/user/self', headers).get('success') is True, 'Existing upstream authorization unavailable')
    raw = fetch('/api/pricing', {})
    listing = fetch('/api/token/?p=1&size=100', headers)
    require(listing.get('success') is True, 'Current production-key metadata unavailable')
    data = listing.get('data') or {}
    rows = data.get('items', []) if isinstance(data, dict) else data
    matched = [row for row in rows if isinstance(row, dict) and str(row.get('key', '')).removeprefix('sk-') == env['VIDEO_JOB_NODYHUB_API_KEY'].removeprefix('sk-')]
    require(len(matched) == 1 and matched[0].get('status') == 1, 'Production-key group binding is not unique and active')
    group = matched[0].get('group')
    factor = Decimal(str((raw.get('group_ratio') or {}).get(group, 'NaN')))
    require(factor.is_finite() and factor > 0 and env['VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD'] == '1.5', 'Verified currency/group conversion unavailable')
    from nodyhub import NODY_MODELS, UUID
    from nody_media_contracts import NodyMediaContracts
    from nody_operator_testing import NodyOperatorTesting
    rates = []
    for model in NODY_MODELS:
        selected = [row for row in raw.get('data', []) if row.get('model_name') == model]
        require(len(selected) == 1, 'A required current upstream model price is missing')
        tariff = (selected[0].get('video_pricing') or {}).get(group)
        require(isinstance(tariff, dict) and tariff.get('mode') == 'per_second', 'Current model price unit is not the approved source')
        rate = Decimal(str(tariff.get('max_price')))
        require(rate.is_finite() and 0 < rate <= 100, 'Current model maximum rate invalid')
        rates.append({'model': model, 'max_source_rate_exact': format(rate, '.6f'), 'source_row_sha256': hashlib.sha256(json.dumps(selected[0], sort_keys=True, separators=(',', ':')).encode()).hexdigest()})
    snapshot = json.dumps(raw, sort_keys=True, separators=(',', ':')).encode()
    atomic(root / 'public-upstream-pricing.json', raw)
    policy = {'schema_version': 'xtai-nody-operator-testing-v1', 'revision': 'nody-operator-20261008.' + op[:12], 'enabled': True,
              'source_snapshot': {'endpoint': 'https://nodyhub.com/api/pricing', 'observed_at': datetime.now(timezone.utc).isoformat(),
                                  'sha256': hashlib.sha256(snapshot).hexdigest(), 'source_unit': 'display_credit', 'billing_unit': 'per_second'},
              'currency_factor_exact': '1.500000', 'group_factor_exact': format(factor, '.6f'), 'markup_exact': '1.500000', 'max_reserve_cny_exact': '150.000000', 'models': rates}
    operator = SECRETS / ('nody-operator-testing-' + op + '.json')
    atomic(operator, policy, gateway=True)
    accepted = json.loads(EVIDENCE.read_text())
    task = (accepted.get('response') or {}).get('id')
    bills = [row for row in (accepted.get('billing_query') or {}).get('data', {}).get('items', []) if row.get('task_id') == task]
    require(accepted.get('state') == 'accepted' and isinstance(task, str) and UUID.fullmatch(task) and len(bills) == 1 and bills[0].get('status') == 'SUCCESS'
            and (accepted.get('delivery_check') or {}).get('audio_video') is True, 'Accepted original Wan bill/delivery proof unavailable')
    actual = Decimal(str(bills[0]['quota'])) / Decimal(500000) * Decimal('1.5')
    require(format(actual, '.6f') == accepted.get('actual_cost_cny_exact') == '1.200000', 'Original actual billing amount changed')
    request = accepted['request']
    require(request['model'] == 'wan3.0-video' and request['duration'] == 2 and request['resolution'] == '480P' and request['audio'] is True
            and all(len(request[field]) == 1 for field in ('image_urls', 'video_urls', 'audio_urls')), 'Original acceptance tuple differs')
    profile = {'model': 'wan3.0-video', 'mode': 'all_reference', 'resolution': '480p', 'duration': 2, 'image_count': 1, 'video_count': 1, 'audio_count': 1,
               'generate_audio': True, 'aspect_ratio': '16:9', 'input_video_seconds_exact': accepted['input_probe']['video']['format']['duration'],
               'input_audio_seconds_exact': accepted['input_probe']['audio']['format']['duration'], 'actual_cost_cny_exact': '1.200000',
               'evidence_task_id': task, 'evidence_source': 'nodyhub_authenticated_video_task', 'status': 'succeeded', 'cost_status': 'actual'}
    media = SECRETS / ('nody-media-input-' + op + '.json')
    atomic(media, {'schema_version': 'xtai-nody-media-input-v1', 'revision': 'nody-accepted-20261008.' + op[:12], 'profiles': [profile]}, gateway=True)
    image = SECRETS / 'nody-image-input-186.json'
    require(image.is_file(), 'Existing live image profile missing')
    descriptors = lambda path: {'host_path': str(path), 'container_path': '/run/secrets/video-billing/' + path.name, 'sha256': digest(path)}
    atomic(root / 'configuration.json', {'image_profile': descriptors(image), 'media_profile': descriptors(media), 'operator_testing_profile': descriptors(operator),
           'expected_media_prices': NodyMediaContracts.load(media).public_rows(), 'expected_operator_rules': NodyOperatorTesting.load(operator).public_rules(),
           'gateway_env': {'VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE': descriptors(media)['container_path'], 'VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE': descriptors(operator)['container_path']},
           'allow_untested': True})
    print(json.dumps({'configured': True, 'operation_id': op, 'operator_rules': 7, 'accepted_media_profiles': 1, 'paid_requests': 0}))


def build(op, root, release, vcs):
    """Build inert candidates from exact owned source; never replace containers."""
    from release_integrity import gateway_source_sha256
    runtime = release / 'runtime'
    source = gateway_source_sha256(runtime)
    catalog = digest(runtime / 'catalog.json')
    gateway_tag = 'xtai/video-job-gateway:nody-operator-' + op[:12]
    public_tag = 'xtai/public-video:nody-operator-' + op[:12]
    base = inspect(TARGETS[0])['Image']
    binary = release / 'public/public-video-linux'
    if not binary.exists():
        with gzip.open(str(binary) + '.gz', 'rb') as incoming, binary.open('xb') as output:
            import shutil
            shutil.copyfileobj(incoming, output)
    expected = json.loads((release / 'artifact.json').read_text())
    require(source == expected['gateway_source_sha256'] and catalog == expected['catalog_sha256'], 'Copied gateway source differs from reviewed artifact')
    require(digest(binary) == expected['binary_sha256'], 'Release binary differs from reviewed build')
    for tag, context, dockerfile, arguments in (
        (gateway_tag, runtime, runtime / 'Dockerfile.nody', ['BASE_IMAGE=' + base, 'XTAI_VCS_REF=' + vcs, 'XTAI_CATALOG_SHA256=' + catalog, 'XTAI_SOURCE_SHA256=' + source]),
        (public_tag, release / 'public', release / 'public/Dockerfile.operator-public', ['BASE_IMAGE=' + inspect(TARGETS[2])['Image'], 'XTAI_VCS_REF=' + vcs, 'XTAI_SOURCE_SHA256=' + expected['binary_sha256']]),
    ):
        args = ['docker', 'build', '--network', 'none', '-t', tag, '-f', str(dockerfile)]
        for argument in arguments:
            args += ['--build-arg', argument]
        args.append(str(context))
        run(args, timeout=300)
    image_ids = {tag: json.loads(run(['docker', 'image', 'inspect', tag]))[0]['Id'] for tag in (gateway_tag, public_tag)}
    config = json.loads((root / 'configuration.json').read_text())
    manifest = {'schema_version': 'xtai-nody-media-rollout-v1', 'operation_id': op, 'private_root': str(root),
                'candidate_images': {name: image_ids[public_tag if name == TARGETS[2] else gateway_tag] for name in TARGETS},
                'candidate_sources': {name: expected['binary_sha256'] if name == TARGETS[2] else source for name in TARGETS}, **config}
    from rollout_media import validate_manifest
    atomic(root / 'manifest.json', validate_manifest(manifest))
    print(json.dumps({'built': True, 'operation_id': op, 'gateway_source_sha256': source, 'public_binary_sha256': expected['binary_sha256'], 'paid_requests': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('action', choices=['configure', 'build']); parser.add_argument('--operation', required=True); parser.add_argument('--vcs', required=True)
    args = parser.parse_args()
    try:
        require(sys.platform.startswith('linux'), 'Server-local Linux release preparation only')
        require(re.fullmatch('[0-9a-f]{32}', args.operation) and re.fullmatch('[0-9a-f]{40}', args.vcs), 'Immutable operation/source identities required')
        root = Path('/opt/ai-api-stack/backups/nody-media-rollout-' + args.operation)
        release = Path('/opt/ai-api-stack/releases/nody-operator-testing-' + args.operation)
        require(release.is_dir() and not release.is_symlink(), 'Owned release source directory unavailable')
        root.mkdir(mode=0o700, exist_ok=True)
        require(not root.is_symlink() and root.stat().st_mode & 0o077 == 0 and root.stat().st_uid == os.geteuid(), 'Private operation ownership/mode differs')
        DIAGNOSTICS_ROOT = root
        sys.path.insert(0, str(release / 'runtime')); sys.path.insert(0, str(release / 'helpers'))
        {'configure': lambda: configure(args.operation, root, release), 'build': lambda: build(args.operation, root, release, args.vcs)}[args.action]()
    except Exception as error:
        print(json.dumps({'needs_attention': True, 'type': type(error).__name__, 'reason': str(error) if isinstance(error, RuntimeError) else 'Protected release diagnostics need review', 'paid_requests': 0})); raise SystemExit(1)
