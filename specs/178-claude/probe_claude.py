"""Bounded dedicated-key probes. Credentials and full provider responses stay private."""
import importlib.util,json,os,pathlib,sys,time,requests
ROOT=pathlib.Path('/opt/ai-api-stack/channel-monitor')
OUT=pathlib.Path('/opt/ai-api-stack/backups/claude178-20260926')
OUT.mkdir(exist_ok=True);OUT.chmod(0o700)
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
sp=importlib.util.spec_from_file_location('balance',ROOT/'scripts/fetch-upstream-balance.py');b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)
CREDS=json.loads((ROOT/'upstream-credentials.json').read_text())
MODELS=['claude-haiku-4-5-20251001','claude-sonnet-4-6','claude-sonnet-5','claude-opus-4-6','claude-opus-4-7','claude-opus-4-8','claude-opus-5','claude-opus-5-5']
SOURCES={'codeplan':('https://oh-code.me','claude-额度计费'),'paisio':('https://api.paisio.online','claude-0.18x'),'maolao':('https://maolaoapi.com','CC-KIRO')}
def save(name,data):
 p=OUT/name
 with os.fdopen(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f:json.dump(data,f,ensure_ascii=False)
def list_tokens(s,origin):
 d=s.get(origin+'/api/token/',params={'p':1,'size':100},timeout=15).json();assert d.get('success') is True
 data=d.get('data',{});return data.get('items',[]) if isinstance(data,dict) else data
def main(slug,mode):
 origin,group=SOURCES[slug];s=requests.Session();record={'source':slug,'group':group,'tests':[]}
 try:
  c=CREDS[slug];b.standard_login(s,origin,c['username'],c['password'])
  pricing=s.get(origin+'/api/pricing',timeout=20).json();assert pricing.get('success') is True
  eligible=[m for m in MODELS if any(r.get('model_name')==m and group in r.get('enable_groups',[]) for r in pricing.get('data',[]))]
  save(slug+'-pricing.json',pricing);record.update(models=eligible,cny_per_credit=c['rate'])
  name='xtai-claude178-'+slug+'-20260926'
  candidates=[x for x in list_tokens(s,origin) if x.get('name')==name]
  if not candidates:
   quota=int(500000/float(c['rate']))
   body={'name':name,'expired_time':int(time.time())+86400,'remain_quota':quota,'unlimited_quota':False,'model_limits_enabled':True,'model_limits':','.join(eligible),'allow_ips':'156.239.3.210','group':group,'cross_group_retry':False}
   d=s.post(origin+'/api/token/',json=body,timeout=15).json();assert d.get('success') is True,'token_creation_rejected'
   candidates=[x for x in list_tokens(s,origin) if x.get('name')==name]
  assert len(candidates)==1,'ambiguous_probe_key'
  token=candidates[0];key=token.get('key','')
  if not key or '*' in key:
   d=s.post(origin+'/api/token/'+str(token['id'])+'/key',timeout=15).json()
   raw=d.get('data');key=raw.get('key','') if isinstance(raw,dict) else raw
  assert isinstance(key,str) and key and '*' not in key,'key_not_revealed'
  key=key if key.startswith('sk-') else 'sk-'+key
  save(slug+'-probe-secret.json',{'token_id':token['id'],'key':key,'origin':origin,'group':group,'models':eligible})
  print(json.dumps({'source':slug,'key_created_or_reused':True,'token_id':token['id'],'group':group,'models':eligible,'maximum_token_budget_cny':1},ensure_ascii=False),flush=True)
  if mode=='prepare':return
  prior=OUT/(slug+'-probes.json')
  if prior.exists():raise RuntimeError('probe_results_exist_do_not_replay')
  # Write a durable started marker BEFORE calls: an interrupted run must be reconciled, not repeated.
  save(slug+'-probes.json',record)
  for model in eligible:
   start=time.time();req={'model':model,'messages':[{'role':'user','content':'Reply only OK.'}],'max_tokens':16,'stream':False}
   try:
    api_origin='https://api.maolaoapi.com' if slug=='maolao' else origin
    r=requests.post(api_origin+'/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=req,timeout=60)
    data=r.json();choices=data.get('choices') or [];message=(choices[0].get('message') or {}) if choices else {}
    success=r.status_code==200 and bool(message.get('content'))
    test={'model':model,'http':r.status_code,'success':success,'response_model':data.get('model'),'usage':data.get('usage'),'seconds':round(time.time()-start,2),'request_id':r.headers.get('X-Request-Id') or r.headers.get('X-Oneapi-Request-Id'),'error':data.get('error') if not success else None}
   except Exception as e:test={'model':model,'success':False,'error_type':type(e).__name__,'uncertain':True,'seconds':round(time.time()-start,2)}
   record['tests'].append(test);save(slug+'-probes.json',record)
   print(json.dumps({k:v for k,v in test.items() if k!='error'},ensure_ascii=False),flush=True)
  logs=s.get(origin+'/api/log/self',params={'p':1,'page_size':100,'token_name':name},timeout=20).json();save(slug+'-logs.json',logs)
  print(json.dumps({'source':slug,'logs_retrieved':logs.get('success') is True}),flush=True)
 finally:
  try:b.standard_logout(s,origin)
  except Exception:pass
if __name__=='__main__':main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else 'prepare')
