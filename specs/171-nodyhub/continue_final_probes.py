"""Continue the two remaining authorized video tests after verified recharge."""
import importlib.util,json,os,pathlib,requests,sys,time
os.umask(0o077)
ROOT=pathlib.Path('/opt/ai-api-stack/channel-monitor');EVIDENCE=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence')
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
sp=importlib.util.spec_from_file_location('b',ROOT/'scripts/fetch-upstream-balance.py');b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)
c=json.loads((ROOT/'upstream-credentials.json').read_text())['nodyhub'];session=requests.Session();origin='https://nodyhub.com'
try:
 b.standard_login(session,origin,c['username'],c['password']);u=b.standard_self(session,origin)
 token=session.get(origin+'/api/token/2553',timeout=15).json()['data']
 assert token['expired_time']>time.time(),'test key expired; stop'
 assert token['allow_ips']=='156.239.3.210' and token['unlimited_quota'] is False
 # Never authorize beyond the currently funded wallet, and reserve a small margin.
 token['remain_quota']=max(0,int(u['quota'])-5000)
 fields=['id','name','expired_time','remain_quota','unlimited_quota','allow_ips','group','cross_group_retry','model_limits_enabled','model_limits']
 result=session.put(origin+'/api/token/',json={k:token.get(k) for k in fields},timeout=15).json();assert result.get('success') is True
 result=session.put(origin+'/api/token/',params={'status_only':'true'},json={'id':2553,'status':1},timeout=15).json();assert result.get('success') is True
 print(json.dumps({'test_key_enabled':2553,'wallet_credit_before':u['quota']/500000,'test_credit_cap':token['remain_quota']/500000}),flush=True)
finally:b.standard_logout(session,origin)
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
cases=[('omni-flash',4,'omni-flash-4sec-test.json'),('flux-3-video',5,'flux-3-video-v2-test.json')]
for model,duration,name in cases:
 p=EVIDENCE/name
 if p.exists():print(json.dumps({'model':model,'existing_attempt':True}),flush=True);continue
 if model=='omni-flash':assert json.loads((EVIDENCE/'omni-flash-v2-test.json').read_text()).get('last_poll',{}).get('status')=='failed'
 usage=requests.get(origin+'/api/usage/token',headers={'Authorization':'Bearer '+key},timeout=15).json()['data']
 # Guard against concurrent depletion using the published maximum per-second rate.
 assert usage['total_available']>=int(duration*1.6*500000),'insufficient budget for maximum catalog rate'
 body={'model':model,'prompt':'A small blue ball rolling gently on a plain white table.','duration':duration,'resolution':'720p','aspect_ratio':'16:9'}
 record={'model':model,'request':body,'endpoint':'/v2/videos/generations','started_at':int(time.time()),'state':'submitting'}
 p.write_text(json.dumps(record))
 try:
  r=requests.post(origin+'/v2/videos/generations',headers={'Authorization':'Bearer '+key},json=body,timeout=(10,90))
  record.update(http=r.status_code,response=r.json(),state='attempt_recorded')
 except Exception as e:record.update(observation_error_type=type(e).__name__,state='outcome_unconfirmed')
 p.write_text(json.dumps(record,ensure_ascii=False));print(json.dumps(record,ensure_ascii=False),flush=True)
