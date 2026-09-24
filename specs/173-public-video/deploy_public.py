"""Scoped deployment helpers. Secrets remain server-local and are never printed."""
from pathlib import Path
import argparse,datetime,hashlib,json,os,secrets,shutil,subprocess,time,urllib.request,urllib.error,urllib.parse

ROOT=Path('/opt/ai-api-stack')
BACKUP=ROOT/'backups/issue173-public-video-20260924'
RELEASE=ROOT/'releases/issue173-db-authority'
NATIVE='ai-api-stack-new-api-1'
CANARY='xtai-native173-canary'
PUBLIC='xtai-public-video'
EXECUTION='xtai-video-public-execution'
PG='ai-api-stack-postgres-1'
DBTEST='xtai_video173_canary'
NGINX=ROOT/'nginx/conf.d/default.conf'

def inspect(name):return json.loads(subprocess.check_output(['docker','inspect',name]))[0]
def env(info):return dict(x.split('=',1) for x in info['Config']['Env'])
def private(path,value):
 path.parent.mkdir(parents=True,exist_ok=True)
 with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:json.dump(value,stream)
 os.chmod(path,0o600)
def envfile(name,values):
 path=BACKUP/(name+'.env')
 with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as stream:
  for key,value in values.items():
   assert '\n' not in value and '\r' not in value
   stream.write(key+'='+value+'\n')
 os.chmod(path,0o600);return path
def sql(command,db='new-api'):
 p=subprocess.run(['docker','exec','-i',PG,'psql','-v','ON_ERROR_STOP=1','-U','newapi','-d',db,'-At'],input=command,text=True,capture_output=True)
 if p.returncode:raise RuntimeError('database command failed: '+p.stderr.splitlines()[0][:100])
 return p.stdout.strip()
def address(name,port):return 'http://'+inspect(name)['NetworkSettings']['Networks']['app-net']['IPAddress']+':'+str(port)
def fetch(url,token=None,body=None):
 headers={'Content-Type':'application/json','X-XingTu-Contract-Version':'xtai-video-billing-v2.2'}
 if token:headers['Authorization']='Bearer '+token
 if body and body.get('request_id'):headers['Idempotency-Key']=body['request_id']
 req=urllib.request.Request(url,data=json.dumps(body).encode() if body else None,headers=headers)
 try:
  with urllib.request.urlopen(req,timeout=45) as r:return r.status,json.load(r)
 except urllib.error.HTTPError as e:
  try:return e.code,json.load(e)
  except Exception:return e.code,{}
def run(name,image,values,binds=(),net='app-net',extra=()):
 command=['docker','create','--name',name,'--network',net,'--restart','unless-stopped','--env-file',str(envfile(name,values))]
 for b in binds:command+=['-v',b]
 command+=list(extra)+[image]
 p=subprocess.run(command,capture_output=True,text=True)
 if p.returncode:raise RuntimeError('container start failed '+name+': '+p.stderr[:200])
 if 'SQL_DSN' in values and net!='ai-api-stack_stack-internal':subprocess.run(['docker','network','connect','ai-api-stack_stack-internal',name],check=True)
 subprocess.run(['docker','start',name],check=True,stdout=subprocess.DEVNULL)
def prepare():
 BACKUP.mkdir(parents=True,exist_ok=True);os.chmod(BACKUP,0o700)
 for name in [NATIVE,'xtai-video-job-gateway-v2-production','ai-api-stack-nginx-1']:
  private(BACKUP/(name+'.before.json'),inspect(name))
 shutil.copy2(NGINX,BACKUP/'nginx.before.conf')
 for filename in ['docker-compose.yml','docker-compose.override.yml']:
  if (ROOT/filename).exists():shutil.copy2(ROOT/filename,BACKUP/(filename+'.before'));os.chmod(BACKUP/(filename+'.before'),0o600)
 dump=BACKUP/'database.before.dump'
 with dump.open('wb') as stream:subprocess.run(['docker','exec',PG,'pg_dump','-U','newapi','-d','new-api','-Fc'],stdout=stream,check=True)
 os.chmod(dump,0o600)
 exists=sql("SELECT 1 FROM pg_database WHERE datname='"+DBTEST+"';")
 if exists:raise RuntimeError('canary database already exists')
 sql('CREATE DATABASE '+DBTEST+' OWNER newapi;')
 with dump.open('rb') as stream:subprocess.run(['docker','exec','-i',PG,'pg_restore','-U','newapi','-d',DBTEST,'--no-owner'],stdin=stream,check=True,stdout=subprocess.DEVNULL)
 print(json.dumps({'backup':str(BACKUP),'canary_database':DBTEST}))
def accounts(db,prefix):
 users=[]
 for suffix in ['owner','other']:
  name=prefix+suffix;key=secrets.token_hex(24);aff=secrets.token_hex(4)
  uid=int(sql("INSERT INTO users(username,password,role,status,quota,used_quota,request_count,\"group\",aff_code,created_at) VALUES ('"+name+"','!nonlogin-test-account',1,1,2500000,0,0,'default','"+aff+"',extract(epoch from now())::bigint) RETURNING id;",db).splitlines()[0])
  tid=int(sql("INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,\"group\",cross_group_retry) VALUES ("+str(uid)+",'"+key+"',1,'issue173-test',extract(epoch from now())::bigint,0,-1,2500000,false,false,'','',0,'',false) RETURNING id;",db).splitlines()[0])
  users.append({'user_id':uid,'token_id':tid,'key':'sk-'+key,'initial_quota':2500000})
 private(BACKUP/(prefix+'accounts.json'),users);return users
def canary():
 original=inspect(NATIVE);values=env(original)
 dsn=urllib.parse.urlsplit(values['SQL_DSN']);values['SQL_DSN']=urllib.parse.urlunsplit(dsn._replace(path='/'+DBTEST))
 values.update(QUOTA_DB_AUTHORITATIVE='true',BATCH_UPDATE_ENABLED='false',NODE_TYPE='slave',REDIS_CONN_STRING='',MEMORY_CACHE_ENABLED='false')
 run(CANARY,'new-api-fixed:issue173-quota-authority',values,extra=['--memory','1g'])
 gateway=inspect('xtai-video-job-gateway-v2-production');ge=env(gateway)
 ge.update(VIDEO_JOB_GATEWAY_ENABLED_PROVIDERS='nodyhub',VIDEO_JOB_GATEWAY_V21_APPROVED_PROVIDERS='nodyhub',VIDEO_JOB_PAISIO_BILLING_ENABLED='0',VIDEO_JOB_ROLLDEK_BILLING_ENABLED='0',VIDEO_JOB_TOONFLOW_BILLING_ENABLED='0',VIDEO_JOB_GATEWAY_WEBHOOK_ENABLED='0',VIDEO_JOB_GATEWAY_PUBLIC_BASE_URL='https://api.aixingtuyun.com')
 data=RELEASE/'public-execution-data';data.mkdir(exist_ok=True);os.chown(data,10002,10002)
 run(EXECUTION,gateway['Config']['Image'],ge,[str(data)+':/data','/opt/xtai/secrets/video-billing:/run/secrets/video-billing:ro'])
 pe={k:v for k,v in values.items() if k in ['SQL_DSN','SESSION_SECRET','CRYPTO_SECRET','TZ']}
 nginx_ip=inspect('ai-api-stack-nginx-1')['NetworkSettings']['Networks']['app-net']['IPAddress']
 pe.update(QUOTA_DB_AUTHORITATIVE='true',BATCH_UPDATE_ENABLED='false',NODE_TYPE='slave',VIDEO_JOB_GATEWAY_TOKEN=ge['VIDEO_JOB_GATEWAY_TOKEN'],PUBLIC_VIDEO_NATIVE='http://'+CANARY+':3000',PUBLIC_VIDEO_BACKEND='http://'+EXECUTION+':8091',PUBLIC_VIDEO_LEGACY='http://video-job-gateway-v2-production:8091',PUBLIC_VIDEO_BASE_URL='https://api.aixingtuyun.com',PUBLIC_VIDEO_QUOTA_PER_CNY='500000',PUBLIC_VIDEO_TRUSTED_PROXIES=nginx_ip)
 run(PUBLIC+'-canary','xtai/public-video:issue173',pe,extra=['--memory','512m'])
 accounts(DBTEST,'v173c-')
 print(json.dumps({'native':CANARY,'public':PUBLIC+'-canary','execution':EXECUTION}))
def probe(name,db,prefix):
 users=json.loads((BACKUP/(prefix+'accounts.json')).read_text());base=address(name,8098)
 status,ready=fetch(base+'/ready');assert status==200,(status,ready)
 status,models=fetch(base+'/v1/models',users[0]['key']);assert status==200
 available=[m['id'] for m in models['data'] if m['id'] in ['wan3.0-video','wan3.0-video-prime','grok-imagine-1.5-video','grok-video-3','grok-imagine-video-official','omni-flash','flux-3-video']];assert len(available)==7,available
 print(json.dumps({'ready':True,'available_models':available,'database':db}))
def submit(name,prefix):
 users=json.loads((BACKUP/(prefix+'accounts.json')).read_text());base=address(name,8098)
 body={'request_id':prefix+'native-wallet-proof','model':'grok-imagine-video-official','prompt':'A small yellow ball rolls on a wooden table with a soft rolling sound. One continuous shot.','duration':1,'resolution':'480p','generate_audio':True}
 first_code,first=fetch(base+'/v1/videos',users[0]['key'],body);assert first_code in [200,202],(first_code,first)
 second_code,second=fetch(base+'/v1/videos',users[0]['key'],body);assert second_code==200 and first['id']==second['id']
 cross,_=fetch(base+'/v1/videos/'+first['id'],users[1]['key']);assert cross==404
 private(BACKUP/(prefix+'submit.json'),first)
 print(json.dumps({'id':first['id'],'reserved':first['billing']['reserved_amount'],'replay_same':True,'cross_user':cross}))
def result(name,db,prefix):
 users=json.loads((BACKUP/(prefix+'accounts.json')).read_text());job=json.loads((BACKUP/(prefix+'submit.json')).read_text());base=address(name,8098)
 status,data=fetch(base+'/v1/videos/'+job['id'],users[0]['key']);assert status==200
 private(BACKUP/(prefix+'result.json'),data)
 rows=sql('SELECT quota,used_quota,request_count FROM users WHERE id='+str(users[0]['user_id'])+'; SELECT remain_quota,used_quota FROM tokens WHERE id='+str(users[0]['token_id'])+';',db)
 print(json.dumps({'task':data,'account_rows':rows},ensure_ascii=False))
 if data.get('billing',{}).get('status')=='settled':
  assert data['billing']['charged_amount']=='0.675000'
  assert rows.splitlines()==['2162500|337500|1','2162500|337500'],rows
  req=urllib.request.Request(base+'/v1/videos/'+job['id']+'/content',headers={'Authorization':'Bearer '+users[0]['key']})
  with urllib.request.urlopen(req,timeout=60) as response:
   video=response.read(5000000);assert response.status==200 and b'soun' in video and b'vide' in video
  print(json.dumps({'wallet_delta_exact':True,'content_audio_video':True,'bytes':len(video)}))

def nginx_reload(text):
 NGINX.write_text(text)
 subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-t'],check=True,capture_output=True)
 subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)

def drain():
 current=NGINX.read_text();marker='location / {\n        proxy_pass http://new-api:3000;'
 assert current.count(marker)==1
 nginx_reload(current.replace(marker,'location / {\n        return 503; # issue173 controlled native drain\n        proxy_pass http://new-api:3000;'))
 private(BACKUP/'drain.json',{'started_at':int(time.time())})
 print('New native requests paused; existing nginx worker connections may finish.')

def drain_status():
 # Native async jobs must be terminal; no direct image gateway work may still charge it.
 import sqlite3
 db=Path('/opt/xtai-image-job-gateway/data/image-jobs.sqlite3')
 c=sqlite3.connect('file:'+str(db)+'?mode=ro',uri=True)
 image=c.execute("select count(*) from image_jobs where status in ('queued','submitting','running')").fetchone()[0];c.close()
 tasks=int(sql("SELECT count(*) FROM tasks WHERE status NOT IN ('SUCCESS','FAILURE');"))
 raw=subprocess.check_output(['docker','exec',NATIVE,'sh','-c','cat /proc/net/tcp /proc/net/tcp6'],text=True)
 active=sum(1 for line in raw.splitlines() if len(line.split())>3 and line.split()[1].endswith(':0BB8') and line.split()[3]=='01')
 totals=sql('SELECT sum(quota),sum(used_quota),sum(request_count) FROM users; SELECT sum(remain_quota),sum(used_quota) FROM tokens; SELECT coalesce(max(created_at),0) FROM logs WHERE type=2;')
 evidence={'checked_at':int(time.time()),'active_native_connections':active,'active_images':image,'active_native_tasks':tasks,'accounting_totals':totals}
 stamp=BACKUP/('drain-'+str(evidence['checked_at'])+'.json');private(stamp,evidence)
 print(json.dumps(evidence));return evidence

def promote_native():
 samples=sorted(BACKUP.glob('drain-*.json'))
 if len(samples)<3:raise RuntimeError('need three quiescent accounting snapshots')
 recent=[json.loads(p.read_text()) for p in samples[-3:]]
 assert recent[-1]['checked_at']-recent[0]['checked_at']>=15
 assert int(time.time())-recent[-1]['checked_at']<20
 assert all(x['active_native_connections']==0 and x['active_images']==0 and x['active_native_tasks']==0 for x in recent)
 assert len({x['accounting_totals'] for x in recent})==1
 # Preserve the last native batch log as supporting evidence; any reported flush error blocks.
 logs=subprocess.run(['docker','logs','--since','2m',NATIVE],capture_output=True,text=True)
 relevant=[line for line in (logs.stdout+logs.stderr).splitlines() if 'batch update' in line]
 assert not any('failed' in line.lower() for line in relevant)
 private(BACKUP/'native-batch-drain-evidence.json',{'snapshots':recent,'batch_logs':relevant})
 with (BACKUP/'database.quiescent.dump').open('wb') as stream:subprocess.run(['docker','exec',PG,'pg_dump','-U','newapi','-d','new-api','-Fc'],stdout=stream,check=True)
 os.chmod(BACKUP/'database.quiescent.dump',0o600)
 old=inspect(NATIVE);values=env(old);values.update(QUOTA_DB_AUTHORITATIVE='true',BATCH_UPDATE_ENABLED='false')
 assert old['Config']['Image']=='new-api-fixed:issue157-stream-errors-72d5a71c'
 rollback=NATIVE+'-rollback-issue173'
 subprocess.run(['docker','stop','--time','60',NATIVE],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['docker','rename',NATIVE,rollback],check=True)
 try:
  flags=['--network-alias','new-api','-p','127.0.0.1:3000:3000']
  for key,value in (old['Config'].get('Labels') or {}).items():flags+=['--label',key+'='+value]
  run(NATIVE,'new-api-fixed:issue173-quota-authority',values,old['HostConfig']['Binds'],extra=flags)
  for retry in range(20):
   try:
    code,status=fetch(address(NATIVE,3000)+'/api/status')
    if code==200 and status['data']['quota_db_authoritative'] and not status['data']['enable_batch_update']:break
   except Exception:pass
   time.sleep(1)
  else:raise RuntimeError('new native not ready')
  # Persist only native image and two quota-mode environment values for compose recreation.
  import yaml
  p=ROOT/'docker-compose.override.yml';config=yaml.safe_load(p.read_text());service=config.setdefault('services',{}).setdefault('new-api',{});service['image']='new-api-fixed:issue173-quota-authority'
  settings=service.get('environment',{})
  if isinstance(settings,list):settings=dict(value.split('=',1) for value in settings)
  settings.update(QUOTA_DB_AUTHORITATIVE='true',BATCH_UPDATE_ENABLED='false');service['environment']=settings
  p.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False));os.chmod(p,0o600)
  subprocess.run(['docker','compose','-f',str(ROOT/'docker-compose.yml'),'-f',str(p),'config','-q'],check=True,capture_output=True)
  nginx_reload((BACKUP/'nginx.before.conf').read_text())
  private(BACKUP/'native-promotion.json',{'at':int(time.time()),'image':inspect(NATIVE)['Image'],'rollback':rollback})
  print('Native quota authority enabled; regular service restored. Public video routing still unchanged.')
 except Exception:
  subprocess.run(['docker','rm','-f',NATIVE],check=False,stdout=subprocess.DEVNULL)
  subprocess.run(['docker','rename',rollback,NATIVE],check=True);subprocess.run(['docker','start',NATIVE],check=True,stdout=subprocess.DEVNULL)
  shutil.copy2(BACKUP/'docker-compose.override.yml.before',ROOT/'docker-compose.override.yml')
  nginx_reload((BACKUP/'nginx.before.conf').read_text());raise

def start_public():
 # Canary execution is already settled; use a separate job database for real users.
 old=inspect(EXECUTION);ge=env(old)
 subprocess.run(['docker','stop','--time','5',EXECUTION],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['docker','rename',EXECUTION,EXECUTION+'-canary'],check=True)
 data=Path('/opt/xtai/state/public-video-execution/data');data.mkdir(parents=True,exist_ok=True);os.chown(data,10002,10002)
 run(EXECUTION,old['Config']['Image'],ge,[str(data)+':/data','/opt/xtai/secrets/video-billing:/run/secrets/video-billing:ro'])
 original=env(inspect(NATIVE));values=env(inspect(PUBLIC+'-canary'));values['SQL_DSN']=original['SQL_DSN'];values['PUBLIC_VIDEO_NATIVE']='http://new-api:3000'
 run(PUBLIC,'xtai/public-video:issue173',values,extra=['--memory','512m'])
 accounts('new-api','v173p-')
 print('Production public access layer started internally; not exposed yet.')

def enable_public():
 status,ready=fetch(address(PUBLIC,8098)+'/ready');assert status==200
 current=NGINX.read_text();boundary=current.index('# 2. ');api=current[:boundary];rest=current[boundary:]
 assert api.count('http://video-job-gateway-v2-production:8091')==4
 api=api.replace('http://video-job-gateway-v2-production:8091','http://xtai-public-video:8098')
 # All forwarded client IPs are replaced by nginx's actual peer address, not user-supplied headers.
 begin=api.index('    # Public XingTu video');end=api.index('    location / {',begin)
 section=api[begin:end].replace('proxy_set_header Host $host;','proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-For $remote_addr;')
 api=api[:begin]+section+api[end:]
 models='    location = /v1/models {\n        proxy_pass http://xtai-public-video:8098/v1/models;\n        proxy_set_header Host $host;\n        proxy_set_header X-Forwarded-For $remote_addr;\n        proxy_read_timeout 30s;\n    }\n\n'
 api=api.replace('    # Public XingTu video',models+'    # Public XingTu video',1)
 private(BACKUP/'nginx-public.before.json',{'sha256':hashlib.sha256(current.encode()).hexdigest()})
 nginx_reload(api+rest);print('Public API-key video routes and model discovery enabled.')

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','canary','probe','submit','result','drain','drain-status','promote-native','start-public','enable-public']);parser.add_argument('--name',default=PUBLIC+'-canary');parser.add_argument('--db',default=DBTEST);parser.add_argument('--prefix',default='v173c-');args=parser.parse_args()
 if args.action=='prepare':prepare()
 elif args.action=='canary':canary()
 elif args.action=='probe':probe(args.name,args.db,args.prefix)
 elif args.action=='submit':submit(args.name,args.prefix)
 elif args.action=='result':result(args.name,args.db,args.prefix)
 elif args.action=='drain':drain()
 elif args.action=='drain-status':drain_status()
 elif args.action=='promote-native':promote_native()
 elif args.action=='start-public':start_public()
 elif args.action=='enable-public':enable_public()
