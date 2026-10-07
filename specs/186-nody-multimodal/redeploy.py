"""Resume only the already-tested issue186 candidates after verified rollback.

Execute in an independent server process. No generation, database restoration,
fresh container creation, or unowned drain deletion is performed.
"""
import fcntl
import hashlib
import json
import os
import pathlib
import subprocess
import time
import traceback

import canary as c
import rollout as r


def reload_routes():
    """Retry only transient Docker-DNS startup propagation, with bounded calls."""
    for attempt in range(15):
        check = subprocess.run(['docker', 'exec', r.NGINX, 'timeout', '15s', 'nginx', '-t'],
                               capture_output=True, text=True, timeout=20)
        c.private_json(r.PRIVATE / 'retry-nginx-test.json', {'attempt': attempt, 'returncode': check.returncode,
                                                           'diagnostic': check.stderr[-2000:]})
        if check.returncode == 0:
            break
        c.require('host not found in upstream' in check.stderr.lower(), 'Nginx validation needs review')
        c.require(attempt < 14, 'Docker-DNS startup propagation did not recover')
        time.sleep(2)
    reloaded = subprocess.run(['docker', 'exec', r.NGINX, 'timeout', '15s', 'nginx', '-s', 'reload'],
                              capture_output=True, text=True, timeout=20)
    c.require(reloaded.returncode == 0, 'Nginx route refresh failed')


def run():
    """Bind immutable old/new identities and preserve current accounting."""
    lock = os.open(r.PRIVATE / 'rollout.lock', os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads(r.STATE.read_text())
        c.require(not state['drains'], 'Previous drain recovery is incomplete')
        c.require(hashlib.sha256(r.PROFILE.read_bytes()).hexdigest() == state['profile_sha256'],
                  'Published exact-cost profile changed')
        native = c.inspect(c.NATIVE_PROD)
        c.require(native['Id'] == state['native']['id'] and native['Image'] == state['native']['image'],
                  'Native runtime identity changed')
        acceptance = json.loads((c.PRIVATE / 'settlement-acceptance.json').read_text())
        c.require(acceptance.get('wallet_delta_exact') is True and acceptance.get('charged_cny') == '0.675000'
                  and acceptance.get('quota_delta') == 337500 and acceptance.get('has_audio') is True
                  and acceptance.get('has_video') is True, 'Accepted canary receipt differs')
        c.require(c.evidence_profiles() == json.loads((c.PRIVATE / 'profiles.json').read_text()),
                  'Exact tested input profiles changed')
        for name in r.TARGETS:
            old = c.inspect(name)
            target = state['targets'][name]
            c.require(old['Id'] == target['before_id'] and old['State']['Running'],
                      'Original healthy service is not restored')
            candidate = c.inspect(target['new_id'])
            c.require(candidate['Name'] == '/' + name + '-failed186' and not candidate['State']['Running']
                      and candidate['Image'] == target['candidate_image']
                      and not candidate['NetworkSettings']['Networks']
                      and candidate['Config']['Labels'].get('com.aixingtuyun.rollout-operation') == state['operation_id'],
                      'Retained candidate ownership or state differs')
        # Verify the restored public pricing semantically before another switch.
        r.verify(rollback=True)
        state = json.loads(r.STATE.read_text())
        state['retry_started_at'] = int(time.time())
        state['phase'] = 'retry_draining'
        r.save(state)
        r.drains(state)
        r.inflight_safe()
        r.docker('/containers/' + state['targets'][r.PUBLIC]['before_id'] + '/stop?t=15', method='POST')
        r.inflight_safe()
        for name in r.TARGETS:
            target = state['targets'][name]
            old = c.inspect(target['before_id'])
            saved = json.loads((r.PRIVATE / (name + '.before.json')).read_text())
            state['retry_target'] = name
            r.save(state)  # intent precedes every old/new identity boundary
            r.docker('/containers/' + old['Id'] + '/stop?t=15', method='POST')
            r.docker('/containers/' + old['Id'] + '/rename?name=' + name + '-rollback186', method='POST')
            for network in old['NetworkSettings']['Networks']:
                r.docker('/networks/' + network + '/disconnect', method='POST', body={'Container': old['Id'], 'Force': False})
            candidate = c.inspect(target['new_id'])
            r.docker('/containers/' + candidate['Id'] + '/rename?name=' + name, method='POST')
            endpoints = r.create_config(saved, saved['Config']['Image'], gateway=False)['NetworkingConfig']['EndpointsConfig']
            for network, endpoint in endpoints.items():
                r.docker('/networks/' + network + '/connect', method='POST',
                         body={'Container': candidate['Id'], 'EndpointConfig': endpoint})
            r.docker('/containers/' + candidate['Id'] + '/start', method='POST')
            r.await_health(name)
        state['phase'] = 'retry_replaced_drained'
        r.save(state)
        reload_routes()
        r.drains(state, remove=True)
        r.verify()
        print(json.dumps({'retry_deployment_verified': True, 'paid_requests': 0}), flush=True)
    except Exception as error:
        c.private_json(r.PRIVATE / 'retry-error.json', {'type': type(error).__name__,
                     'frames': [{'file': pathlib.Path(f.filename).name, 'line': f.lineno}
                                for f in traceback.extract_tb(error.__traceback__)[-6:]]})
        state = json.loads(r.STATE.read_text())
        if state.get('drains') or state.get('phase') in ('retry_draining', 'retry_replaced_drained'):
            r.rollback()
        raise
    finally:
        os.close(lock)


if __name__ == '__main__':
    run()
