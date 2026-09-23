"""Server-side, scoped Nody deployment helper. No credentials are printed."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

PROD = 'xtai-video-job-gateway-v2-production'
CANARY = 'xtai-video-job-gateway-nody-canary'
ROOT = Path('/opt/ai-api-stack')
BACKUP = ROOT / 'backups/nodyhub-video-20260923'
STATE = Path('/opt/xtai/state/video-billing-v2-production/data')
SECRETS = Path('/opt/xtai/secrets/video-billing')

def inspect(name):
    return json.loads(subprocess.check_output(['docker','inspect',name]))[0]

def private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd=os.open(path, os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream)
    os.chmod(path,0o600)

def prepare_auth():
    BACKUP.mkdir(parents=True, exist_ok=True);os.chmod(BACKUP,0o700)
    config_path=ROOT/'channel-monitor/video-provider-auth-lifecycle.json'
    backup_path=BACKUP/'auth-lifecycle.before.json'
    if not backup_path.exists():shutil.copy2(config_path,backup_path)
    sys.path.insert(0,'/opt/xtai/releases/toonflow-drain-20260902-v2')
    spec=importlib.util.spec_from_file_location('refresh',ROOT/'channel-monitor/scripts/refresh-video-provider-auth.py')
    refresh=importlib.util.module_from_spec(spec);spec.loader.exec_module(refresh)
    config=json.loads(config_path.read_text())
    provider={'provider_id':'nodyhub','enabled':True,'refresh_mode':'scheduled_login',
              'credential_source_slug':'nodyhub','base_url':'https://nodyhub.com',
              'output_file':str(SECRETS/'nodyhub-session.json'),'lease_seconds':7200}
    existing=[p for p in config['providers'] if p.get('provider_id')=='nodyhub']
    if existing and existing != [provider]:raise RuntimeError('conflicting existing Nody refresh config')
    credentials=refresh.load_private_json(ROOT/'channel-monitor/upstream-credentials.json')
    document=refresh.refresh_newapi_session(provider,credentials['nodyhub'],now=int(time.time()))
    refresh.atomic_write_json(Path(provider['output_file']),document)
    os.chown(provider['output_file'],10002,10002)
    if not existing:config['providers'].append(provider)
    refresh.atomic_write_json(config_path,config)
    print(json.dumps({'auth':'verified','account':document['new_api_user'],'expires_at':document['expires_at'],'scheduled_refresh':'hourly-existing-cron'}))

def live_billing(release):
    sys.path.insert(0,str(release))
    from billing_collectors import NewAPITaskBillingCollector
    collector=NewAPITaskBillingCollector('nodyhub','https://nodyhub.com/api/task/self',credential_file=SECRETS/'nodyhub-session.json',rate_cny_per_usd='1.5')
    for task,amount in [('f38d6547-27af-4f3b-8945-8c643162c432','2.550000'),('098a134c-6eb8-475d-afd0-cc0d36fec8dc','7.125000'),('0ad9d1c7-83ed-410e-9f7b-77e05e8c2113','0.450000')]:
        record=collector.collect(task)
        assert record.actual_cost_cny_exact==amount
        print(json.dumps({'task_id':task,'cost_cny':record.actual_cost_cny_exact,'source':record.evidence_source}))

def env_from(info):return dict(row.split('=',1) for row in info['Config']['Env'])

def build_env(info):
    env=env_from(info)
    for key in ['VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS','VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS']:
        values=env.get(key,'').split(',')
        if 'nodyhub' not in values:values.append('nodyhub')
        env[key]=','.join(filter(None,values))
    env.update(VIDEO_JOB_NODYHUB_API_KEY=(ROOT/'secrets/upstreams/nodyhub-requested-20260923.key').read_text().strip(),
               VIDEO_JOB_NODYHUB_BASE_URL='https://nodyhub.com',
               VIDEO_JOB_NODYHUB_RESULT_HOSTS='getapib.org,webstatic.aiproxy.vip,nody-files.tos-cn-beijing.volces.com',
               VIDEO_JOB_NODYHUB_BILLING_ENABLED='1',
               VIDEO_JOB_NODYHUB_BILLING_CREDENTIAL_FILE='/run/secrets/video-billing/nodyhub-session.json',
               VIDEO_JOB_NODYHUB_BILLING_RATE_CNY_PER_USD='1.5')
    return env

def run_container(name,image,env,data,production=False):
    data.mkdir(parents=True,exist_ok=True);os.chown(data,10002,10002)
    env_path=BACKUP/(name+'.env')
    fd=os.open(env_path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as stream:
        for key,value in env.items():
            if '\n' in value or '\r' in value:raise RuntimeError('unsafe environment value')
            stream.write(key+'='+value+'\n')
    command=['docker','run','-d','--name',name,'--network','app-net','--restart','unless-stopped',
             '--env-file',str(env_path),'-v',str(SECRETS)+':/run/secrets/video-billing:ro','-v',str(data)+':/data']
    if production:command+=['--network-alias','video-job-gateway-v2-production']
    command+=[image]
    subprocess.run(command,check=True,stdout=subprocess.DEVNULL)

def request(name,path,body=None,authorized=True):
    info=inspect(name);env=env_from(info)
    ip=info['NetworkSettings']['Networks']['app-net']['IPAddress']
    headers={'X-XingTu-Contract-Version':'xtai-video-billing-v2.2'}
    if authorized:headers['Authorization']='Bearer '+env['VIDEO_JOB_GATEWAY_TOKEN']
    if body:headers.update({'Content-Type':'application/json','Idempotency-Key':body['request_id']})
    req=urllib.request.Request('http://'+ip+':8091'+path,data=json.dumps(body).encode() if body else None,headers=headers)
    with urllib.request.urlopen(req,timeout=30) as response:return json.load(response)

def canary(image):
    info=inspect(PROD)
    if not (BACKUP/'production.before.json').exists():private_json(BACKUP/'production.before.json',info)
    env=build_env(info);env['VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED']='0'
    run_container(CANARY,image,env,BACKUP/'canary-data')
    print(json.dumps({'canary_started':CANARY,'image':image}))

def probe(name):
    health=request(name,'/health')
    caps=request(name,'/v1/capabilities');prices=request(name,'/v1/video-prices')
    private_json(BACKUP/(name+'.capabilities.json'),caps)
    private_json(BACKUP/(name+'.prices.json'),prices)
    models=caps['capabilities']['video']['models']
    nody=[m for m in models if m['id'] in {'wan3.0-video','wan3.0-video-prime','grok-imagine-1.5-video','grok-video-3','grok-imagine-video-official','omni-flash','flux-3-video'}]
    assert len(nody)==7 and all(m['available'] for m in nody)
    try:request(name,'/v1/capabilities',authorized=False)
    except urllib.error.HTTPError as error:assert error.code==401
    else:raise RuntimeError('authentication missing')
    print(json.dumps({'health':health,'nody_available':len(nody),'pricing_rows':len(prices['pricing']['models']),'auth_negative':401},ensure_ascii=False))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['auth','billing','canary','probe']);parser.add_argument('--release',type=Path);parser.add_argument('--image');parser.add_argument('--name',default=CANARY)
    args=parser.parse_args()
    if args.action=='auth':prepare_auth()
    elif args.action=='billing':live_billing(args.release)
    elif args.action=='canary':canary(args.image)
    elif args.action=='probe':probe(args.name)

if __name__=='__main__':main()
