"""Scoped Claude configuration rollout; all credentials/backups remain server-side."""
import importlib.util,json,os,pathlib,re,secrets,subprocess,sys,time,urllib.parse
import requests

ROOT=pathlib.Path('/opt/ai-api-stack/backups/claude178-20260926')
DBTEST='xtai_claude178_canary'
CANARY='xtai-claude178-canary'
sp=importlib.util.spec_from_file_location('helpers','/opt/ai-api-stack/releases/issue173-db-authority/deploy_public.py')
h=importlib.util.module_from_spec(sp);sp.loader.exec_module(h);h.BACKUP=ROOT
PLAN=json.loads((ROOT/'plan.json').read_text())

def q(value):return "'"+str(value).replace("'","''")+"'"
def rows(sql,db):return json.loads(h.sql('SELECT coalesce(jsonb_agg(to_jsonb(t)),\'[]\'::jsonb) FROM ('+sql+') t;',db))

def apply(db):
 assert db in [DBTEST,'new-api']
 marker=ROOT/(db+'-applied.json');assert not marker.exists(),'already applied'
 options=rows('SELECT key,value FROM options',db);current={r['key']:r['value'] for r in options}
 assert float(json.loads(current['GroupRatio'])['文'])==.15
 model_names=[r['model'] for r in PLAN['models']];names=','.join(map(q,model_names))
 assert not rows('SELECT id FROM channels WHERE name LIKE \'Claude178:%\'',db)
 assert not rows('SELECT id FROM models WHERE model_name IN ('+names+') AND deleted_at IS NULL',db)
 h.private(ROOT/(db+'-options-before.json'),options)
 statements=['BEGIN;','SET LOCAL lock_timeout=\'5s\';','LOCK TABLE options IN SHARE ROW EXCLUSIVE MODE;']
 statements.append('DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM options WHERE key=\'GroupRatio\' AND value='+q(current['GroupRatio'])+') THEN RAISE EXCEPTION \'concurrent group ratio change\'; END IF; END $$;')
 enabled=db==DBTEST
 for key in ['ModelRatio','CompletionRatio','CacheRatio','CreateCacheRatio']:
  if key in current:merged=json.loads(current[key])
  else:
   assert key=='CreateCacheRatio'
   source=pathlib.Path('/opt/ai-api-stack/releases/issue173-db-authority/source/setting/ratio_setting/cache_ratio.go').read_text()
   body=source.split('var defaultCreateCacheRatio = map[string]float64{',1)[1].split('}',1)[0]
   merged={name:float(value) for name,value in re.findall(r'"([^"]+)":\s*([0-9.]+)',body)};assert len(merged)>20
  # Optimistic guard prevents overwriting a concurrent pricing update.
  if key in current:statements.append('DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM options WHERE key='+q(key)+' AND value='+q(current[key])+') THEN RAISE EXCEPTION \'concurrent option update\'; END IF; END $$;')
  else:statements.append('DO $$ BEGIN IF EXISTS(SELECT 1 FROM options WHERE key='+q(key)+') THEN RAISE EXCEPTION \'concurrent option insertion\'; END IF; END $$;')
  for model in PLAN['models']:merged[model['model']]=model['options'][key]
  statements.append('INSERT INTO options(key,value) VALUES('+q(key)+','+q(json.dumps(merged))+') ON CONFLICT(key) DO UPDATE SET value=excluded.value;')
 for model in PLAN['models']:
  name=model['model']
  statements.append('INSERT INTO models(model_name,description,icon,tags,vendor_id,endpoints,status,sync_official,created_time,updated_time,name_rule) VALUES('+','.join([q(name),q('Claude upstream model label; verified OpenAI chat; cost evidence 2026-09-26'),q('Claude'),q('text'), '38',q(json.dumps({'openai':{'path':'/v1/chat/completions','method':'POST'}})), '1' if enabled else '0','0',str(int(time.time())),str(int(time.time())),'0'])+');')
  for provider in model['candidates']:
   slug=provider['source'];secret=json.loads((ROOT/(slug+'-probe-secret.json')).read_text());url='https://api.maolaoapi.com' if slug=='maolao' else secret['origin']
   setting={'force_format':False,'thinking_to_content':False,'proxy':'','pass_through_body_enabled':False,'system_prompt':'','system_prompt_override':False}
   values=[1,q(secret['key']),1 if enabled else 2,q('Claude178:'+slug+':'+name),0,int(time.time()),q(url),q(name),q('文'),provider['priority'],1,q(json.dumps(setting)),q(json.dumps({'is_multi_key':False,'multi_key_size':0,'multi_key_polling_index':0,'multi_key_mode':''})),q(json.dumps({'upstream_model_update_check_enabled':False,'upstream_model_update_auto_sync_enabled':False})),q('issue178 verified isolated model route')]
   statements.append('WITH added AS (INSERT INTO channels(type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,setting,channel_info,settings,remark) VALUES('+','.join(map(str,values))+') RETURNING id) INSERT INTO abilities("group",model,channel_id,enabled,priority,weight) SELECT \'文\','+q(name)+',id,'+('true' if enabled else 'false')+','+str(provider['priority'])+',0 FROM added;')
 statements.append('COMMIT;');h.sql('\n'.join(statements),db)
 ids=rows('SELECT id,name,models,priority FROM channels WHERE name LIKE \'Claude178:%\' ORDER BY id',db)
 h.private(marker,ids);print(json.dumps({'database':db,'routes':len(ids),'models':len(model_names)}),flush=True)

def account(db):
 marker=ROOT/(db+'-account.json');assert not marker.exists()
 key=secrets.token_hex(24);names=','.join(m['model'] for m in PLAN['models'])
 uid=int(h.sql('INSERT INTO users(username,password,role,status,quota,used_quota,request_count,"group",aff_code,created_at) VALUES (\'claude178-canary\',\'!nonlogin-test-account\',1,1,500000,0,0,\'default\','+q(secrets.token_hex(5))+','+str(int(time.time()))+') RETURNING id;',db).splitlines()[0])
 tid=int(h.sql('INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) VALUES('+str(uid)+','+q(key)+',1,\'claude178-canary\','+str(int(time.time()))+',0,'+str(int(time.time())+3600)+',500000,false,true,'+q(names)+',\'\',0,\'auto\',false) RETURNING id;',db).splitlines()[0])
 h.private(marker,{'user_id':uid,'token_id':tid,'key':'sk-'+key});return uid

def prepare():
 assert not h.sql("SELECT 1 FROM pg_database WHERE datname='"+DBTEST+"';")
 dump=ROOT/'database.before.dump';assert not dump.exists()
 with os.fdopen(os.open(dump,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as f:subprocess.run(['docker','exec',h.PG,'pg_dump','-U','newapi','-d','new-api','-Fc'],stdout=f,check=True)
 h.sql('CREATE DATABASE '+DBTEST+' OWNER newapi;')
 with dump.open('rb') as f:subprocess.run(['docker','exec','-i',h.PG,'pg_restore','-U','newapi','-d',DBTEST,'--no-owner'],stdin=f,check=True,capture_output=True)
 apply(DBTEST);account(DBTEST)
 values=h.env(h.inspect(h.NATIVE));dsn=urllib.parse.urlsplit(values['SQL_DSN']);values['SQL_DSN']=urllib.parse.urlunsplit(dsn._replace(path='/'+DBTEST))
 values.update(QUOTA_DB_AUTHORITATIVE='true',BATCH_UPDATE_ENABLED='false',NODE_TYPE='slave',REDIS_CONN_STRING='',MEMORY_CACHE_ENABLED='false',SQL_MAX_OPEN_CONNS='5',SQL_MAX_IDLE_CONNS='1',SQL_MAX_LIFETIME='60')
 h.run(CANARY,'new-api-fixed:issue173-quota-authority',values,extra=['--memory','768m'])
 print('isolated canary started',flush=True)

def probe(db):
 marker=ROOT/(db+'-native-probes.json');assert not marker.exists(),'reconcile existing probe instead of replay'
 account_data=json.loads((ROOT/(db+'-account.json')).read_text());result=[];h.private(marker,result)
 base=h.address(CANARY,3000) if db==DBTEST else 'https://api.aixingtuyun.com'
 headers={'Authorization':'Bearer '+account_data['key']}
 r=requests.get(base+'/v1/models',headers=headers,timeout=15);r.raise_for_status();available={x['id'] for x in r.json()['data']}
 assert all(m['model'] in available for m in PLAN['models'])
 for model in PLAN['models']:
  before=int(h.sql('SELECT quota FROM users WHERE id='+str(account_data['user_id']),db));start=time.time()
  try:
   r=requests.post(base+'/v1/chat/completions',headers=headers,json={'model':model['model'],'messages':[{'role':'user','content':'Reply only OK.'}],'max_tokens':16},timeout=60)
   data=r.json();success=r.status_code==200 and bool(data.get('choices',[{}])[0].get('message',{}).get('content'))
   after=int(h.sql('SELECT quota FROM users WHERE id='+str(account_data['user_id']),db))
   row={'model':model['model'],'http':r.status_code,'success':success,'response_model':data.get('model'),'usage':data.get('usage'),'seconds':round(time.time()-start,2),'delta':before-after,'error':data.get('error')}
  except Exception as e:row={'model':model['model'],'uncertain':True,'error_type':type(e).__name__}
  result.append(row);h.private(marker,result);print(json.dumps(row,ensure_ascii=False),flush=True)
 bills=rows('SELECT model_name,quota,prompt_tokens,completion_tokens,other,channel_id FROM logs WHERE user_id='+str(account_data['user_id'])+' AND type=2 ORDER BY id',db)
 h.private(ROOT/(db+'-native-bills.json'),bills)
 assert len(bills)==8 and all(x.get('success') for x in result),'canary failed; do not deploy'
 assert sum(x['quota'] for x in bills)==sum(x['delta'] for x in result),'quota mismatch'
 print('eight models and wallet/log deltas verified',flush=True)

if __name__=='__main__':
 mode=sys.argv[1]
 if mode=='prepare':prepare()
 elif mode=='probe':probe(sys.argv[2])
 elif mode=='apply':apply(sys.argv[2])
 elif mode=='account':print(account(sys.argv[2]))
