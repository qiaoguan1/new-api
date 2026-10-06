"""Inspect the existing Pro route; one funded probe only, never replay unclear outcomes."""
import importlib.util
import json
import os
import pathlib
import sys
import time
import requests

ROOT = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
OUT = pathlib.Path('/opt/ai-api-stack/backups/issue183-runtime-20261006')
MONITOR = pathlib.Path('/opt/ai-api-stack/channel-monitor')
sys.path.insert(0, str(MONITOR / 'scripts'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


b = load('banana_balance', MONITOR / 'scripts/fetch-upstream-balance.py')
h = load('banana_ops', ROOT / 'operations.py')
a = load('banana_adapter', pathlib.Path('/opt/xtai-banana-adapter/app.py'))


def private(name, value):
    h.h.private(OUT / name, value)


def main(mode):
    info = h.h.inspect('xtai-banana-chat-adapter')
    env = h.h.env(info)
    mounts = {m['Destination']: m['Source'] for m in info['Mounts']}
    for name in ['BANANA_ROLLDEK_KEY_FILE', 'BANANA_HAINA_KEY_FILE', 'BANANA_ADAPTER_TOKEN_FILE']:
        if env.get(name) in mounts:
            env[name] = mounts[env[name]]
    config = a.Config.from_env(env)
    route = config.routes['banana-pro'][0]
    credential = json.loads((MONITOR / 'upstream-credentials.json').read_text())['rolldek']
    origin = b.origin_of(route.base_url)
    session = requests.Session()
    try:
        group = b.standard_login(session, origin, credential['username'], credential['password'])
        account = b.standard_self(session, origin)
        pricing = b.standard_pricing_metadata(session, origin)
        private('banana-pro-pricing.json', pricing)
        rate = b.validated_exchange_rate(credential['rate'])
        balance = b.q2usd(account['quota']) * rate
        catalog = requests.get(route.base_url + '/models', headers={'Authorization': 'Bearer ' + route.api_key}, timeout=15).json()
        available = route.upstream_model in {row['id'] for row in catalog.get('data', [])}
        prices = [row for row in pricing['models'] if row['model_name'] == route.upstream_model]
        private('banana-pro-inspect.json', {'provider': route.provider, 'model': route.upstream_model, 'available': available, 'balance_cny': balance, 'rate': rate, 'account_group': group, 'prices': prices})
        print(json.dumps({'provider': route.provider, 'model': route.upstream_model, 'available': available, 'balance_cny': balance, 'rate': rate, 'account_group': group, 'prices': prices}, ensure_ascii=False), flush=True)
        if mode == 'inspect':
            return
        # Every advertised group must be affordable; do not assume the API key
        # uses the account's UI group. The actual bill verifies its exact group.
        costs = [float(row['model_price']) * float(pricing['group_ratio'][g]) * rate for row in prices if row.get('quota_type') == 1 for g in row.get('enable_groups', []) if g in pricing['group_ratio']]
        assert available and costs and max(costs) <= 0.5 and balance >= max(costs), 'cost/balance evidence insufficient for a bounded probe'
        marker = OUT / 'banana-pro-probe.json'
        assert not marker.exists(), 'reconcile prior request; do not repeat'
        private(marker.name, {'status': 'submitted_once', 'time': int(time.time())})
        prompt = 'A simple blue circle on white. Return one image.\n\nRequested output canvas: 1024x1024 pixels. Return one image.'
        started = time.monotonic()
        try:
            response = requests.post(route.base_url + '/chat/completions', headers={'Authorization': 'Bearer ' + route.api_key}, json={'model': route.upstream_model, 'messages': [{'role': 'user', 'content': prompt}], 'stream': False}, timeout=(8,190))
            payload = response.json()
            references = a.extract_image_references(payload['choices'][0]['message']['content']) if response.status_code == 200 else []
            rid = response.headers.get('X-Oneapi-Request-Id') or response.headers.get('X-Request-Id') or ''
            result = {'status': response.status_code, 'success': bool(references), 'request_id': rid, 'model': payload.get('model'), 'seconds': round(time.monotonic()-started,2), 'usage': payload.get('usage'), 'error': payload.get('error') if not references else None}
        except Exception as error:
            result = {'success': False, 'uncertain': True, 'error_type': type(error).__name__}
        private(marker.name, result)
        print(json.dumps({k:v for k,v in result.items() if k != 'error'}, ensure_ascii=False), flush=True)
        log = session.get(origin + '/api/log/self', params={'p':1,'page_size':100,'model_name':route.upstream_model}, timeout=20).json()
        assert log.get('success')
        private('banana-pro-bills.json', log)
        print('Pro probe actual bill stored privately', flush=True)
    finally:
        b.standard_logout(session, origin)


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv)>1 else 'inspect')
