"""Scoped issue180 staging, isolated API verification, and transactional migration."""
import importlib.util,json,os,pathlib,secrets,shutil,subprocess,time,urllib.parse
import requests,yaml
ROOT=pathlib.Path('/opt/ai-api-stack/releases/issue180-p1')
BACKUP=pathlib.Path('/opt/ai-api-stack/backups/issue180-p1-20260926')
DB='xtai_p1180_canary';NAME='xtai-p1180-canary';IMAGE='new-api-fixed:issue180-token-default'
sp=importlib.util.spec_from_file_location('h','/opt/ai-api-stack/releases/issue173-db-authority/deploy_public.py');h=importlib.util.module_from_spec(sp);sp.loader.exec_module(h);h.BACKUP=BACKUP

def quote(v):return "'"+str(v).replace("'","''")+"'"
def rows(q,db='new-api'):return json.loads(h.sql("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]'::jsonb) FROM ("+q+") t;",db))

def ready(expected_image):
 deadline=time.monotonic()+40
 while time.monotonic()<deadline:
  try:
   info=h.inspect(h.NATIVE)
   assert info['State']['Running'] and info['Config']['Image']==expected_image
   assert {'app-net','ai-api-stack_stack-internal'}<=set(info['NetworkSettings']['Networks'])
   response=requests.get('http://127.0.0.1:3000/api/status',timeout=2)
   body=response.json();assert response.status_code==200 and body.get('success')
   assert body['data'].get('quota_db_authoritative') is True
   if expected_image==IMAGE:assert body['data'].get('default_use_auto_group') is True
   assert h.sql('SELECT 1;')=='1'
   return
  except (AssertionError,ValueError,requests.RequestException,subprocess.SubprocessError,KeyError):time.sleep(1)
 raise RuntimeError('native readiness failed')

def prepare():
 assert not BACKUP.exists();BACKUP.mkdir(mode=0o700)
 dump=BACKUP/'database.before.dump'
 with os.fdopen(os.open(dump,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:subprocess.run(['docker','exec',h.PG,'pg_dump','-U','newapi','-d','new-api','-Fc'],stdout=f,check=True)
 h.private(BACKUP/'native.before.json',h.inspect(h.NATIVE))
 shutil.copy2('/opt/ai-api-stack/docker-compose.override.yml',BACKUP/'compose.before.yml');os.chmod(BACKUP/'compose.before.yml',0o600)
 monitor=pathlib.Path('/opt/ai-api-stack/channel-monitor');stage=ROOT/'monitor-stage';assert not stage.exists();stage.mkdir(mode=0o700)
 for name in ['scripts','config']:
  shutil.copytree(monitor/name,stage/name,ignore=shutil.ignore_patterns('__pycache__'))
 for name in ['upstream-credentials.json','upstreams.json']+[p.name for p in monitor.glob('*.py')]:
  if (monitor/name).exists():shutil.copy2(monitor/name,stage/name);os.chmod(stage/name,0o600)
 (stage/'data').mkdir(mode=0o700)
 for name in ['upstream-balance-ledger.json','daily-upstream-audit.json','daily-cost-history.json','monitor-data.json','auto-pricing-log.json','daily-ops-digest-state.json','upstream-balance-live.json','upstream-recharge-summary.json']:
  if (monitor/'data'/name).exists():shutil.copy2(monitor/'data'/name,stage/'data'/name);os.chmod(stage/'data'/name,0o600)
 for p in (ROOT/'monitor-overlay').iterdir():shutil.copy2(p,stage/'scripts'/p.name)
 assert not h.sql("SELECT 1 FROM pg_database WHERE datname='"+DB+"'")
 h.sql('CREATE DATABASE '+DB+' OWNER newapi;')
 with dump.open('rb') as f:subprocess.run(['docker','exec','-i',h.PG,'pg_restore','-U','newapi','-d',DB,'--no-owner'],stdin=f,check=True,capture_output=True)
 for key,value in [('DefaultUseAutoGroup','true'),('EmailVerificationEnabled','false'),('TurnstileCheckEnabled','false'),('RegisterEnabled','true'),('PasswordRegisterEnabled','true')]:h.sql('INSERT INTO options(key,value) VALUES('+quote(key)+','+quote(value)+') ON CONFLICT(key) DO UPDATE SET value=excluded.value;',DB)
 values=h.env(h.inspect(h.NATIVE));dsn=urllib.parse.urlsplit(values['SQL_DSN']);values['SQL_DSN']=urllib.parse.urlunsplit(dsn._replace(path='/'+DB))
 values.update(NODE_TYPE='slave',REDIS_CONN_STRING='',MEMORY_CACHE_ENABLED='false',SQL_MAX_OPEN_CONNS='5',SQL_MAX_IDLE_CONNS='1',SQL_MAX_LIFETIME='60',GENERATE_DEFAULT_TOKEN='true')
 h.run(NAME,IMAGE,values,extra=['--memory','768m'])
 print('private backup and isolated native/monitor staging prepared')

def verify():
 marker=BACKUP/'native-tests.json';assert not marker.exists()
 access=secrets.token_hex(16);uid=int(h.sql('INSERT INTO users(username,password,role,status,quota,used_quota,request_count,"group",aff_code,access_token,created_at) VALUES('+quote('p1180-canary-'+secrets.token_hex(3))+',\'!nonlogin\',1,1,0,0,0,\'default\','+quote(secrets.token_hex(5))+','+quote(access)+','+str(int(time.time()))+') RETURNING id;',DB).splitlines()[0])
 base=h.address(NAME,3000);headers={'Authorization':'Bearer '+access,'New-Api-User':str(uid)};result=[]
 for name,group,expected in [('omitted',None,'auto'),('blank','','auto'),('text','文','文'),('explicit-default','default','default')]:
  body={'name':'p1180-'+name,'remain_quota':5000,'unlimited_quota':False,'expired_time':-1,'model_limits_enabled':True,'model_limits':'gpt-6,claude-sonnet-5','allow_ips':'','cross_group_retry':False}
  if group is not None:body['group']=group
  response=requests.post(base+'/api/token/',headers=headers,json=body,timeout=15).json();assert response.get('success'),name
  token=rows('SELECT id,key,"group",remain_quota,model_limits,model_limits_enabled FROM tokens WHERE user_id='+str(uid)+' AND name='+quote(body['name']),DB)[0]
  assert token['group']==expected and token['remain_quota']==5000 and token['model_limits_enabled'] and token['model_limits']==body['model_limits']
  if expected=='auto':
   models=requests.get(base+'/v1/models',headers={'Authorization':'Bearer sk-'+token['key']},timeout=15).json();ids={m['id'] for m in models.get('data',[])};assert {'gpt-6','claude-sonnet-5'}<=ids
  result.append({'case':name,'group':expected,'constraints_preserved':True})
 # Registration must use the same default, not only explicit API creation.
 registered_name='p1180reg'+secrets.token_hex(3)
 r=requests.post(base+'/api/user/register',json={'username':registered_name,'password':'Test-'+secrets.token_hex(6),'email':''},timeout=15).json();assert r.get('success'),str(r.get('message'))[:150]
 registered=rows('SELECT t."group" FROM tokens t JOIN users u ON u.id=t.user_id WHERE u.username='+quote(registered_name),DB);assert registered and all(t['group']=='auto' for t in registered)
 result.append({'case':'registration','group':'auto'})
 h.private(marker,result);print(json.dumps(result,ensure_ascii=False))

def deploy():
 assert (BACKUP/'native-tests.json').exists()
 assert not (BACKUP/'deployed.json').exists()
 compose=pathlib.Path('/opt/ai-api-stack/docker-compose.override.yml');before=compose.read_text();config=yaml.safe_load(before)
 service=config['services']['new-api'];assert service['image']=='new-api-fixed:issue173-quota-authority'
 env=service['environment'];assert env['QUOTA_DB_AUTHORITATIVE']=='true' and env['BATCH_UPDATE_ENABLED']=='false'
 assert str(env['SQL_MAX_OPEN_CONNS'])=='30' and str(env['SQL_MAX_IDLE_CONNS'])=='5'
 labelled=subprocess.check_output(['docker','ps','-a','--filter','label=com.docker.compose.service=new-api','--format','{{.Names}}'],text=True).splitlines()
 assert labelled==[h.NATIVE],'unexpected compose-owned rollback; preserve before recreation'
 monitor=pathlib.Path('/opt/ai-api-stack/channel-monitor')
 originals=BACKUP/'monitor-scripts';originals.mkdir(mode=0o700)
 for candidate in (ROOT/'monitor-overlay').glob('*.py'):
  target=monitor/'scripts'/candidate.name;assert target.exists()
  shutil.copy2(target,originals/candidate.name)
 original_option=rows("SELECT key,value FROM options WHERE key='DefaultUseAutoGroup'")
 h.private(BACKUP/'default-group-option-before.json',original_option)
 h.sql("INSERT INTO options(key,value) VALUES('DefaultUseAutoGroup','true') ON CONFLICT(key) DO UPDATE SET value=excluded.value;")
 service['image']=IMAGE;compose.write_text(yaml.safe_dump(config,allow_unicode=True,sort_keys=False))
 try:
  subprocess.run(['docker','compose','config','--quiet'],cwd='/opt/ai-api-stack',check=True,capture_output=True)
  subprocess.run(['docker','compose','up','-d','--no-deps','--pull','never','--timeout','60','new-api'],cwd='/opt/ai-api-stack',check=True)
  ready(IMAGE)
  subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-t'],check=True,capture_output=True)
  subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
 except Exception:
  compose.write_text(before)
  if original_option:h.sql('UPDATE options SET value='+quote(original_option[0]['value'])+" WHERE key='DefaultUseAutoGroup';")
  else:h.sql("DELETE FROM options WHERE key='DefaultUseAutoGroup';")
  subprocess.run(['docker','compose','up','-d','--no-deps','--pull','never','--timeout','60','new-api'],cwd='/opt/ai-api-stack',check=True)
  ready('new-api-fixed:issue173-quota-authority')
  subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
  raise RuntimeError('deployment failed; prior image/config/default option restored and readiness verified') from None
 for candidate in (ROOT/'monitor-overlay').glob('*.py'):
  target=monitor/'scripts'/candidate.name;temp=target.with_suffix('.p1180.tmp');shutil.copy2(candidate,temp);os.chmod(temp,target.stat().st_mode);temp.replace(target)
 h.private(BACKUP/'deployed.json',{'time':int(time.time()),'native_image':IMAGE,'scripts':[p.name for p in (ROOT/'monitor-overlay').glob('*.py')]})
 print('native and pricing/health workers deployed; only default-group option changed')

def migrate():
 assert (BACKUP/'deployed.json').exists() and not (BACKUP/'token-migration-result.json').exists()
 p=subprocess.Popen(['docker','exec','-i',h.PG,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','newapi','-d','new-api'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  p.stdin.write("BEGIN; SET LOCAL lock_timeout='5s'; SET LOCAL idle_in_transaction_session_timeout='60s'; CREATE TEMP TABLE p1180_targets ON COMMIT DROP AS SELECT id,to_jsonb(t) AS original FROM tokens t WHERE status=1 AND deleted_at IS NULL AND coalesce(\"group\",'')='' FOR UPDATE; SELECT coalesce(jsonb_agg(original),'[]'::jsonb) FROM p1180_targets;\n");p.stdin.flush()
  line=p.stdout.readline();assert line,'snapshot query failed'
  original=json.loads(line);h.private(BACKUP/'tokens-before-migration.json',original)
  p.stdin.write("UPDATE tokens SET \"group\"='auto' WHERE id IN (SELECT id FROM p1180_targets); DO $$ BEGIN IF EXISTS(SELECT 1 FROM tokens t JOIN p1180_targets b ON t.id=b.id WHERE (to_jsonb(t)-'group')<>(b.original-'group') OR t.\"group\"<>'auto') THEN RAISE EXCEPTION 'unexpected token field mutation'; END IF; END $$; SELECT count(*) FROM p1180_targets; COMMIT;\n");p.stdin.flush();p.stdin.close()
  output=p.stdout.read();error=p.stderr.read();assert p.wait()==0,'migration rolled back; inspect private logs'
  changed=int(output.strip());assert changed==len(original)
  h.private(BACKUP/'token-migration-result.json',{'changed':changed,'non_group_fields_unchanged':True,'time':int(time.time())})
  print(json.dumps({'changed_blank_keys':changed,'non_group_fields_unchanged':True}))
 finally:
  if p.poll() is None:p.terminate();p.wait(timeout=10)

if __name__=='__main__':{'prepare':prepare,'verify':verify,'deploy':deploy,'migrate':migrate}[__import__('sys').argv[1]]()
