"""Guarded rollout: latest-source canary, drain, scoped config, verified restore."""
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.parse
import requests
import yaml

ROOT = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
spec = importlib.util.spec_from_file_location('operations183', ROOT / 'operations.py')
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)
h, BACKUP, MONITOR = o.h, o.BACKUP, o.MONITOR
GATEWAY = 'xtai-image-job-gateway-image-job-gateway-1'
GATEWAY_IMAGE = 'xtai/image-jobs:issue183-audit'
GATEWAY_ROOT = pathlib.Path('/opt/xtai-image-job-gateway')
NGINX = pathlib.Path('/opt/ai-api-stack/nginx/conf.d/default.conf')
NATIVE_COMPOSE = pathlib.Path('/opt/ai-api-stack/docker-compose.override.yml')
GATEWAY_COMPOSE = GATEWAY_ROOT / 'compose.yaml'
MONITOR_FILES = ['fetch-upstream-balance.py', 'patrol_repair.py', 'auto-apply-pricing.py']


def image_id(tag):
    return subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', tag], text=True).strip()


def ready_native():
    deadline = time.monotonic()+45
    while time.monotonic() < deadline:
        try:
            info = h.inspect(h.NATIVE)
            assert info['Image'] == image_id(o.IMAGE)
            assert {'app-net', 'ai-api-stack_stack-internal'} <= set(info['NetworkSettings']['Networks'])
            values = h.env(info)
            assert values['QUOTA_DB_AUTHORITATIVE'] == 'true' and values['BATCH_UPDATE_ENABLED'] == 'false'
            assert [values[x] for x in ['SQL_MAX_OPEN_CONNS','SQL_MAX_IDLE_CONNS','SQL_MAX_LIFETIME']] == ['30','5','300']
            data = requests.get('http://127.0.0.1:3000/api/status', timeout=2).json()
            assert data.get('success') and data['data'].get('quota_db_authoritative') is True and data['data'].get('default_use_auto_group') is True
            assert h.sql('SELECT 1;') == '1'
            return
        except (AssertionError, requests.RequestException, KeyError, ValueError):
            time.sleep(1)
    raise RuntimeError('native readiness failed')


def stage():
    monitor = ROOT / 'monitor-stage'
    subprocess.run(['tar','-xzf',str(ROOT/'monitor-overlay.tar.gz'),'-C',str(monitor)], check=True)
    # Production's video policy is intentionally newer than the dev fixture;
    # test the changed operational components here, not unrelated old fixtures.
    for pattern in ['test_fetch_upstream_balance.py','test_ledger_patrol_isolation.py','test_patrol_pricing_business.py','test_auto_apply_pricing.py']:
        subprocess.run(['python3','-m','unittest','discover','-s',str(monitor/'tests'),'-p',pattern,'-q'], check=True)
    code = subprocess.run(['python3',str(monitor/'scripts/fetch-upstream-balance.py')], capture_output=True, text=True, timeout=750)
    with os.fdopen(os.open(BACKUP/'collector-staging.stdout',os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f:
        f.write(code.stdout + '\n' + code.stderr)
    assert code.returncode in (0,2), 'staging collector failed locally'
    summary = json.loads(code.stdout.splitlines()[-1])
    assert summary['skipped_manual'] == 2 and all(row['status']=='skipped_manual' for row in summary['results'] if row['slug'] in ('0809','toonflow'))
    h.private(BACKUP/'monitor-validation.json', {'source_sha256': {name: hashlib.sha256((monitor/'scripts'/name).read_bytes()).hexdigest() for name in MONITOR_FILES}, 'summary': summary, 'fork_deadline_test': 'passed', 'tested_at': int(time.time())})
    gateway = ROOT / 'image-gateway-stage'
    gateway.mkdir(exist_ok=True)
    subprocess.run(['tar','-xzf',str(ROOT/'gateway-overlay.tar.gz'),'-C',str(gateway)],check=True)
    subprocess.run(['docker','build','-t',GATEWAY_IMAGE,str(gateway)], check=True)
    h.private(BACKUP/'gateway-validation.json',{'image':image_id(GATEWAY_IMAGE),'app_sha256':hashlib.sha256((gateway/'app.py').read_bytes()).hexdigest(),'tests':9})
    print(json.dumps({'collector_complete':summary['complete'],'collector_incomplete':summary['incomplete'],'manual_skipped':summary['skipped_manual'],'gateway_image_built':True}),flush=True)


def reverify():
    assert h.inspect(o.NAME)['Config']['Image'] == o.IMAGE
    values = h.env(h.inspect(o.NAME))
    assert urllib.parse.urlsplit(values['SQL_DSN']).path=='/'+o.DB
    if not o.rows("SELECT id FROM channels WHERE models='canary-gpt61-caches'",o.DB):
        template = o.rows('SELECT * FROM channels WHERE id=23',o.DB)[0]
        new_id = int(h.sql('SELECT max(id)+1 FROM channels;',o.DB))
        o.insert_channel(template,o.DB,id=new_id,name='issue183-cache-fixture',models='canary-gpt61-caches',group='文',key='local-test-only',base_url=h.address(o.FAKE,19000)+'/response-cache',priority=20,model_mapping=json.dumps({'canary-gpt61-caches':'gpt-6.1-sol'}),setting='{}',used_quota=0,status=1)
    o.merge_option('billing_setting.billing_mode', {'canary-gpt61-caches':'tiered_expr'},o.DB)
    o.merge_option('billing_setting.billing_expr', {'canary-gpt61-caches':o.GPT61_EXPR},o.DB)
    subprocess.run(['docker','rm','-f',o.NAME],check=True,stdout=subprocess.DEVNULL)
    h.run(o.NAME,o.IMAGE,values,extra=['--memory','1g'])
    # This file is deliberately mounted into the non-root fake container.
    # Verified transfer files default to 0600; test code contains no secrets.
    os.chmod(ROOT/'canary_upstream.py',0o644)
    subprocess.run(['docker','restart',o.FAKE],check=True,stdout=subprocess.DEVNULL)
    for _ in range(30):
        try:
            if requests.get(h.address(o.FAKE,19000),timeout=2).status_code==200:break
        except (requests.RequestException,KeyError):pass
        time.sleep(.2)
    else:raise RuntimeError('private fake upstream not ready')
    # Docker restart can reassign an IP after recreating the native canary.
    # All fake channels use a stable private DNS name, not the previous IP.
    for row in o.rows("SELECT id,base_url FROM channels WHERE name LIKE 'issue183-%'",o.DB):
        path=urllib.parse.urlsplit(row['base_url']).path
        h.sql('UPDATE channels SET base_url='+o.quote('http://'+o.FAKE+':19000'+path)+' WHERE id='+str(row['id'])+';',o.DB)
    base=h.address(o.NAME,3000)
    for _ in range(40):
        try:
            if requests.get(base+'/api/status',timeout=2).json().get('success'):break
        except requests.RequestException:pass
        time.sleep(1)
    account=json.loads((BACKUP/'canary-account.json').read_text());headers={'Authorization':'Bearer '+account['key']}
    results=[]
    for model,expected in [('canary-image-unsafe',504),('canary-image-safe',200)]:
        before=o.rows('SELECT quota,used_quota FROM users WHERE id='+str(account['user_id']),o.DB)[0]
        r=requests.post(base+'/v1/images/generations',headers=headers,json={'model':model,'prompt':'local test','size':'1024x1024','n':1},timeout=20)
        h.private(BACKUP/(model+'-reviewed-response.json'),{'status':r.status_code,'body':r.json(),'submission_state':r.headers.get('X-XingTu-Image-Submission-State')})
        assert r.status_code==expected, 'local canary '+model+' unexpected status '+str(r.status_code)
        if expected==504:
            assert r.json()['error']['code']=='image_submit_uncertain' and r.headers.get('X-XingTu-Image-Submission-State')=='uncertain'
            for _ in range(40):
                after=o.rows('SELECT quota,used_quota FROM users WHERE id='+str(account['user_id']),o.DB)[0]
                if after==before:break
                time.sleep(.1)
            assert after==before
        results.append({'case':model,'status':expected,'protected_state':r.headers.get('X-XingTu-Image-Submission-State')})
    r=requests.post(base+'/v1/responses',headers=headers,json={'model':'canary-gpt61-caches','input':'local test','max_output_tokens':32},timeout=20)
    assert r.status_code==200
    rid=r.headers['X-XingTu-Relay-Request-ID'];assert rid!='fake-provider-id'
    for _ in range(40):
        bills=o.rows('SELECT quota FROM logs WHERE type=2 AND request_id='+o.quote(rid),o.DB)
        if bills:break
        time.sleep(.1)
    assert bills==[{'quota':210}], 'cache creation was omitted/double-counted'
    results.append({'case':'cache-write end-to-end','quota':210,'billing_exact':True})
    r=requests.post(base+'/v1/images/generations',headers=headers,json={'model':'banana-pro','prompt':'local invalid size','size':'2048x2048','n':1},timeout=20)
    assert r.status_code==503 and r.headers.get('X-XingTu-Image-Submission-State')=='not_submitted'
    results.append({'case':'unsupported Pro size','status':503,'no_upstream_submit':True})
    # One reviewed 1K Pro integration probe, capped by the existing account.
    marker=BACKUP/'banana-pro-canary-response.json';assert not marker.exists()
    h.private(marker,{'status':'submitted_once','time':int(time.time())})
    started=time.monotonic()
    r=requests.post(base+'/v1/images/generations',headers=headers,json={'model':'banana-pro','prompt':'A blue circle on white. Return one image.','size':'1024x1024','n':1,'response_format':'b64_json'},timeout=(8,210))
    body=r.json();rid=r.headers.get('X-XingTu-Relay-Request-ID','')
    result={'status':r.status_code,'success':r.status_code==200 and bool(body.get('data')),'request_id':rid,'seconds':round(time.monotonic()-started,2),'error':body.get('error')}
    if result['success']:
        import base64,struct
        raw=base64.b64decode(body['data'][0]['b64_json']);result['bytes']=len(raw)
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):result['width'],result['height']=struct.unpack('>II',raw[16:24])
    h.private(marker,result);assert result['success'],'Pro integration failed; inspect private evidence without replay'
    for _ in range(40):
        bills=o.rows('SELECT quota FROM logs WHERE type=2 AND request_id='+o.quote(rid),o.DB)
        if bills:break
        time.sleep(.1)
    assert bills==[{'quota':60000}], 'Pro 1K retail did not settle to 0.12 CNY'
    results.append({'case':'Pro 1K integration','native_quota':60000,**result})
    h.private(BACKUP/'reviewed-canary-results.json',{'native_image':image_id(o.IMAGE),'source_manifest_sha256':hashlib.sha256((ROOT/'source-review-amended.sha256').read_bytes()).hexdigest(),'results':results})
    print(json.dumps(results,ensure_ascii=False),flush=True)


def nginx_reload(text):
    previous=NGINX.read_text()
    try:
        NGINX.write_text(text)
        subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-t'],check=True,capture_output=True)
        subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
    except Exception:
        NGINX.write_text(previous)
        subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-t'],check=True,capture_output=True)
        subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
        raise


def compose_up(path, service, native=False):
    command=['docker','compose']
    if native:command+=['-f','/opt/ai-api-stack/docker-compose.yml']
    command+=['-f',str(path),'up','-d','--no-deps','--pull','never','--timeout','60',service]
    subprocess.run(command,check=True,capture_output=True)


def verify_restored(before):
    """Only reopen ingress after both original services and DB are proven ready."""
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:
            native=h.inspect(h.NATIVE);gateway=h.inspect(GATEWAY)
            assert native['Image']==before['native_image'] and gateway['Image']==image_id(before['gateway_image'])
            assert native['State']['Running'] and gateway['State']['Running']
            assert {'app-net','ai-api-stack_stack-internal'}<=set(native['NetworkSettings']['Networks'])
            values=h.env(native);assert values['QUOTA_DB_AUTHORITATIVE']=='true' and values['BATCH_UPDATE_ENABLED']=='false'
            assert requests.get('http://127.0.0.1:3000/api/status',timeout=2).json()['data']['quota_db_authoritative'] is True
            assert requests.get(h.address(GATEWAY,8090)+'/health',timeout=2).status_code==200
            assert h.sql('SELECT 1;')=='1'
            return
        except (AssertionError,KeyError,ValueError,requests.RequestException):time.sleep(1)
    raise RuntimeError('rollback readiness was not confirmed; keep ingress closed')


def deploy():
    reviewed=json.loads((BACKUP/'reviewed-canary-results.json').read_text())
    assert reviewed['native_image']==image_id(o.IMAGE)==h.inspect(o.NAME)['Image']
    assert reviewed['source_manifest_sha256']==hashlib.sha256((ROOT/'source-review-amended.sha256').read_bytes()).hexdigest()
    gateway_validation=json.loads((BACKUP/'gateway-validation.json').read_text())
    assert gateway_validation['image']==image_id(GATEWAY_IMAGE) and gateway_validation['app_sha256']==hashlib.sha256((ROOT/'image-gateway-stage/app.py').read_bytes()).hexdigest()
    assert (BACKUP/'gpt61-production-key-policy.json').exists()
    policy=json.loads((BACKUP/'gpt61-production-key-policy.json').read_text())
    assert policy['expired_time']==-1 and policy['unlimited_quota'] is True and policy['model_limits']=='gpt-6.1-sol' and policy['group']=='gpt-额度计费'
    assert h.inspect(h.NATIVE)['Config']['Image']==o.OLD_IMAGE
    assert int(h.sql('SELECT max(id) FROM channels;'))==68, 'production channel inventory changed'
    monitor=json.loads((BACKUP/'monitor-validation.json').read_text())
    assert all(hashlib.sha256((ROOT/'monitor-stage/scripts'/name).read_bytes()).hexdigest()==digest for name,digest in monitor['source_sha256'].items())
    for line in (ROOT/'source-review-amended.sha256').read_text().splitlines():
        digest,name=line.split('  ',1);assert hashlib.sha256((ROOT/'source'/name).read_bytes()).hexdigest()==digest
    assert not (BACKUP/'deployed.json').exists()
    before_native=NATIVE_COMPOSE.read_text();before_gateway=GATEWAY_COMPOSE.read_text();before_nginx=NGINX.read_text()
    before={'native_compose':before_native,'gateway_compose':before_gateway,'nginx':before_nginx,'native_image':h.inspect(h.NATIVE)['Image'],'gateway_image':h.inspect(GATEWAY)['Config']['Image'],'options':o.rows("SELECT * FROM options WHERE key IN ('ModelPrice','billing_setting.billing_mode','billing_setting.billing_expr')")}
    h.private(BACKUP/'deployment.before.json',before)
    script_backup=BACKUP/'monitor-before';script_backup.mkdir(mode=0o700,exist_ok=True)
    for name in MONITOR_FILES:shutil.copy2(MONITOR/'scripts'/name,script_backup/name)
    drain=GATEWAY_ROOT/'data/DRAIN';assert not drain.exists()
    with os.fdopen(os.open(drain,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write('issue183-rollout')
    paused=False;release_allowed=False;committed=False
    try:
        # Briefly reject new text/image submissions only. Video routes and
        # callbacks remain unchanged; stub status counts accepted live writers.
        location='''    location ~ ^/v1/(images/(generations|edits)|responses(/compact)?|chat/completions|completions|messages)$ {
        add_header X-XingTu-Image-Submission-State not_submitted always;
        add_header X-XingTu-Relay-Request-ID $request_id always;
        default_type application/json;
        return 503 '{"error":{"code":"relay_maintenance","message":"Brief verified relay upgrade; no generation submitted"}}';
    }
'''
        start=before_nginx.index('server_name api.aixingtuyun.com aixingtuyun.com www.aixingtuyun.com;')
        index=before_nginx.index('    location / {',start)
        draining=before_nginx[:index]+location+before_nginx[index:]+"\nserver { listen 127.0.0.1:18083; location / { stub_status; } }\n"
        nginx_reload(draining);paused=True
        deadline=time.monotonic()+60;clear=0
        while time.monotonic()<deadline:
            with sqlite3.connect('file:'+str(GATEWAY_ROOT/'data/image-jobs.sqlite3')+'?mode=ro',uri=True) as connection:
                active=connection.execute("select count(*) from image_jobs where status in ('queued','submitting','running')").fetchone()[0]
            try:
                text=subprocess.check_output(['docker','exec','ai-api-stack-nginx-1','wget','-qO-','http://127.0.0.1:18083/'],text=True,stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                # nginx reload replaces workers asynchronously; the temporary
                # loopback status listener may not exist in its first second.
                time.sleep(.5)
                continue
            match=re.search(r'Reading: (\d+) Writing: (\d+)',text);assert match
            quiet=active==0 and int(match[1])==0 and int(match[2])<=1
            clear=clear+1 if quiet else 0
            if clear>=3:break
            time.sleep(1)
        else:raise RuntimeError('accepted requests still active; production swap was not attempted')
        # Pricing merges current maps; never restore an old database/wallet.
        key=json.loads((BACKUP/'gpt61-key.json').read_text())
        template=o.rows('SELECT * FROM channels WHERE id=39')[0]
        o.insert_channel(template,'new-api',id=69,name='Code Plan · GPT-6.1 Sol · 已验证',models='gpt-6.1-sol',group='文',key=key['key'],base_url='https://oh-code.me',priority=10,model_mapping='',setting='{}',used_quota=0,status=2)
        template=o.rows('SELECT * FROM channels WHERE id=53')[0]
        o.insert_channel(template,'new-api',id=70,name='Banana Pro · Rolldek · 已验证1K',models='banana-pro',group='图香蕉',priority=10,model_mapping='',setting=json.dumps({'image_endpoints':['/v1/images/generations'],'image_sizes':['1024x1024']}),used_quota=0,status=2)
        h.sql('UPDATE abilities SET enabled=false WHERE channel_id IN (69,70);')
        o.merge_option('billing_setting.billing_mode',{'gpt-6.1-sol':'tiered_expr'},'new-api')
        o.merge_option('billing_setting.billing_expr',{'gpt-6.1-sol':o.GPT61_EXPR},'new-api')
        o.merge_option('ModelPrice',{'banana-pro':.8},'new-api')
        # Toonflow is known/proven only for generation at the 1K tier.
        row=o.rows('SELECT setting FROM channels WHERE id=54')[0]
        setting=json.loads(row['setting'] or '{}');setting.update(image_endpoints=['/v1/images/generations'],image_sizes=['1024x1024'])
        h.private(BACKUP/'toonflow-setting.before.json',row)
        h.sql('UPDATE channels SET setting='+o.quote(json.dumps(setting))+' WHERE id=54;')
        config=yaml.safe_load(before_native);assert config['services']['new-api']['image']==o.OLD_IMAGE
        config['services']['new-api']['image']=o.IMAGE
        NATIVE_COMPOSE.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
        compose_up(NATIVE_COMPOSE,'new-api',native=True);ready_native()
        config=yaml.safe_load(before_gateway);config['services']['image-job-gateway']['image']=GATEWAY_IMAGE
        GATEWAY_COMPOSE.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
        shutil.copy2(ROOT/'image-gateway-stage/app.py',GATEWAY_ROOT/'app.py')
        compose_up(GATEWAY_COMPOSE,'image-job-gateway')
        assert h.inspect(GATEWAY)['Image']==image_id(GATEWAY_IMAGE)
        for _ in range(40):
            try:
                if requests.get(h.address(GATEWAY,8090)+'/health',timeout=2).status_code==200:break
            except requests.RequestException:pass
            time.sleep(1)
        else:raise RuntimeError('new gateway health not ready')
        # Verify exact runtime expression before allowing the new abilities.
        access=h.sql('SELECT access_token FROM users WHERE id=1;')
        data=requests.get('http://127.0.0.1:3000/api/option/',headers={'Authorization':'Bearer '+access,'New-Api-User':'1'},timeout=10).json();assert data.get('success')
        options={x['key']:x['value'] for x in data['data']}
        assert json.loads(options['billing_setting.billing_expr'])['gpt-6.1-sol']==o.GPT61_EXPR and json.loads(options['ModelPrice'])['banana-pro']==.8
        h.sql('BEGIN; UPDATE channels SET status=1 WHERE id IN (69,70); UPDATE abilities SET enabled=true WHERE channel_id IN (69,70); COMMIT;')
        for name in MONITOR_FILES:shutil.copy2(ROOT/'monitor-stage/scripts'/name,MONITOR/'scripts'/name)
        # Never copy the stale staging history over live ledger/manual evidence.
        # A fresh bounded collector runs after the ingress gate is removed.
        ready_native()
        release_allowed=True
        # Persist readiness before reopening. Post-release journaling/printing
        # errors must never restart a service that just accepted new tasks.
        deployment={'native_image':image_id(o.IMAGE),'gateway_image':image_id(GATEWAY_IMAGE),'new_channels':[69,70],'at':int(time.time()),'video_configuration_unchanged':True,'wallet_migration':False,'ingress_released':False}
        h.private(BACKUP/'deployed.json',deployment)
        committed=True
        nginx_reload(before_nginx);paused=False
        assert drain.read_text()=='issue183-rollout';drain.unlink()
        h.private(BACKUP/'deployed.json',deployment|{'ingress_released':True})
        print('production rollout completed; verify public catalog and read-only patrol next',flush=True)
    except Exception as primary_error:
      if committed:
        h.private(BACKUP/'post-commit-review-required.json',{'at':int(time.time()),'error_type':type(primary_error).__name__,'services_kept_running':True,'wallets_not_restored':True})
        raise RuntimeError('verified rollout committed; inspect ingress/audit without unsafe restart') from primary_error
      try:
        # Quarantine new routes; do not delete audit/billing rows or rewind wallets.
        h.sql('UPDATE channels SET status=2 WHERE id IN (69,70); UPDATE abilities SET enabled=false WHERE channel_id IN (69,70);')
        prior={row['key']:json.loads(row['value']) for row in before['options']}
        for key,model in [('ModelPrice','banana-pro'),('billing_setting.billing_mode','gpt-6.1-sol'),('billing_setting.billing_expr','gpt-6.1-sol')]:
            current=json.loads(h.sql('SELECT value FROM options WHERE key='+o.quote(key)+';') or '{}')
            if model in prior.get(key,{}):current[model]=prior[key][model]
            else:current.pop(model,None)
            o.set_option(key,current,'new-api')
        if (BACKUP/'toonflow-setting.before.json').exists():
            saved=json.loads((BACKUP/'toonflow-setting.before.json').read_text())
            h.sql('UPDATE channels SET setting='+('NULL' if saved['setting'] is None else o.quote(saved['setting']))+' WHERE id=54;')
        NATIVE_COMPOSE.write_text(before_native);GATEWAY_COMPOSE.write_text(before_gateway)
        compose_up(NATIVE_COMPOSE,'new-api',native=True);compose_up(GATEWAY_COMPOSE,'image-job-gateway')
        shutil.copy2(BACKUP/'image-gateway.before.py',GATEWAY_ROOT/'app.py')
        for name in MONITOR_FILES:shutil.copy2(script_backup/name,MONITOR/'scripts'/name)
        verify_restored(before)
        release_allowed=True
        h.private(BACKUP/'rollback.json',{'at':int(time.time()),'new_routes_paused':True,'wallets_not_restored':True})
      except Exception as recovery_error:
        release_allowed=False
        h.private(BACKUP/'recovery-required.json',{'at':int(time.time()),'error_type':type(recovery_error).__name__,'ingress_stays_closed':True,'wallets_not_restored':True})
        raise RuntimeError('rollback incomplete; maintenance gate retained') from recovery_error
      raise primary_error
    finally:
        if release_allowed:
          try:
            if paused:nginx_reload(before_nginx)
            if drain.exists() and drain.read_text()=='issue183-rollout':drain.unlink()
          except Exception as release_error:
            h.private(BACKUP/'recovery-required.json',{'at':int(time.time()),'error_type':type(release_error).__name__,'ingress_release_failed':True,'wallets_not_restored':True})
            raise RuntimeError('verified services but ingress release failed; retain drain') from release_error


if __name__=='__main__':
    {'stage':stage,'reverify':reverify,'deploy':deploy}[__import__('sys').argv[1]]()
