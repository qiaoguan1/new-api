"""One explicitly authorized alternate-entry test; cap spend and disable key finally."""
import importlib.util,json,os,pathlib,re,requests,sys,time
os.umask(0o077)
ROOT=pathlib.Path('/opt/ai-api-stack/channel-monitor');OUT=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence/grok-image-chat-retest.json')
assert not OUT.exists(),'Already attempted: inspect saved evidence instead of resubmitting'
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
spec=importlib.util.spec_from_file_location('b',ROOT/'scripts/fetch-upstream-balance.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
c=json.loads((ROOT/'upstream-credentials.json').read_text())['nodyhub'];origin='https://nodyhub.com'
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
def change_key(enable):
 s=requests.Session()
 try:
  b.standard_login(s,origin,c['username'],c['password'])
  if enable:
   t=s.get(origin+'/api/token/2553',timeout=15).json()['data'];assert t['expired_time']>time.time();u=b.standard_self(s,origin)
   t['remain_quota']=min(500000,int(u['quota'])-5000)
   assert t['remain_quota']>=225000,'Insufficient funded quota'
   fields=['id','name','expired_time','remain_quota','unlimited_quota','allow_ips','group','cross_group_retry','model_limits_enabled','model_limits']
   d=s.put(origin+'/api/token/',json={k:t.get(k) for k in fields},timeout=15).json();assert d.get('success')
  d=s.put(origin+'/api/token/',params={'status_only':'true'},json={'id':2553,'status':1 if enable else 2},timeout=15).json();assert d.get('success')
 finally:b.standard_logout(s,origin)

out={'model':'grok-imagine-image-2.0','endpoint':'/v1/chat/completions','started_at':int(time.time()),'state':'preparing'}
try:
 change_key(True);out['state']='submitting';OUT.write_text(json.dumps(out))
 started=time.monotonic();body={'model':out['model'],'messages':[{'role':'user','content':'Generate one image: a plain blue circle centered on a white background. No text.'}],'stream':False}
 with requests.post(origin+out['endpoint'],headers={'Authorization':'Bearer '+key},json=body,stream=True,timeout=(10,150)) as r:
  out['http']=r.status_code;out['content_type']=r.headers.get('Content-Type');chunks=[];total=0
  for part in r.iter_content(8192):
   total+=len(part)
   if total>16*1024*1024 or time.monotonic()-started>180:raise TimeoutError('bounded observation ended')
   chunks.append(part)
  text=b''.join(chunks).decode('utf-8','replace')
  try:out['response']=json.loads(text)
  except ValueError:out['response_text']=text[:200000]
 out['seconds']=round(time.monotonic()-started,2);out['state']='response_received'
except Exception as e:out['observation_error_type']=type(e).__name__
finally:
 OUT.write_text(json.dumps(out,ensure_ascii=False))
 try:change_key(False);out['test_key_disabled']=True
 except Exception as e:out['key_cleanup_error_type']=type(e).__name__
 OUT.write_text(json.dumps(out,ensure_ascii=False))
 response=out.get('response',{})
 print(json.dumps({k:v for k,v in out.items() if k not in ['response','response_text']},ensure_ascii=False))
 if isinstance(response,dict):print(json.dumps({'response_keys':list(response),'error':response.get('error'),'message':response.get('message'),'data_type':type(response.get('data')).__name__},ensure_ascii=False))
 else:print('non-object response')
