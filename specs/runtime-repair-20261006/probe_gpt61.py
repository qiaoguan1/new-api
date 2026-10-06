"""Account-funded GPT6.1 probe with a dedicated 1 CNY budget; secrets stay private."""
import importlib.util,json,os,pathlib,sys,time,requests

MONITOR=pathlib.Path('/opt/ai-api-stack/channel-monitor')
OUT=pathlib.Path('/opt/ai-api-stack/backups/issue183-runtime-20261006')
OUT.mkdir(exist_ok=True,mode=0o700);OUT.chmod(0o700)
sys.path[:0]=[str(MONITOR),str(MONITOR/'scripts')]
sp=importlib.util.spec_from_file_location('balance',MONITOR/'scripts/fetch-upstream-balance.py')
b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)
ORIGIN='https://oh-code.me';MODEL='gpt-6.1-sol';GROUP='gpt-额度计费';NAME='xtai-gpt61-issue183-20261006'

def private(name,value):
 with os.fdopen(os.open(OUT/name,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f:json.dump(value,f,ensure_ascii=False,indent=2)

def tokens(session):
 response=session.get(ORIGIN+'/api/token/',params={'p':1,'size':100},timeout=15).json();assert response.get('success')
 data=response['data'];return data['items'] if isinstance(data,dict) else data

def main(mode):
 credential=json.loads((MONITOR/'upstream-credentials.json').read_text())['codeplan']
 s=requests.Session()
 try:
  b.standard_login(s,ORIGIN,credential['username'],credential['password'])
  if mode=='promote':
   stored=json.loads((OUT/'gpt61-key.json').read_text());matches=[x for x in tokens(s) if x['id']==stored['id']]
   assert len(matches)==1 and matches[0]['name']==NAME and matches[0]['group']==GROUP
   body={'id':stored['id'],'name':NAME,'status':1,'expired_time':-1,'remain_quota':0,'unlimited_quota':True,'model_limits_enabled':True,'model_limits':MODEL,'allow_ips':'156.239.3.210','group':GROUP,'cross_group_retry':False}
   response=s.put(ORIGIN+'/api/token/',json=body,timeout=15).json();assert response.get('success')
   token=next(x for x in tokens(s) if x['id']==stored['id'])
   assert token['expired_time']==-1 and token['unlimited_quota'] is True and token['group']==GROUP and token['model_limits']==MODEL and token['model_limits_enabled'] is True and token['allow_ips']=='156.239.3.210' and token['status']==1
   private('gpt61-production-key-policy.json',{k:token[k] for k in ['id','name','status','expired_time','unlimited_quota','model_limits_enabled','model_limits','allow_ips','group','cross_group_retry']})
   print('existing dedicated key promoted: permanent, exact-model and server-IP restricted; no recharge',flush=True)
   return
  metadata=b.standard_pricing_metadata(s,ORIGIN);private('gpt61-pricing.json',metadata)
  rows=[x for x in metadata['models'] if x['model_name']==MODEL and GROUP in x.get('enable_groups',[])]
  assert len(rows)==1 and rows[0]['billing_mode']=='tiered_expr'
  assert metadata['group_ratio'][GROUP]==.15 and float(credential['rate'])==1
  matches=[x for x in tokens(s) if x['name']==NAME]
  if not matches:
   body={'name':NAME,'expired_time':int(time.time())+86400,'remain_quota':500000,'unlimited_quota':False,'model_limits_enabled':True,'model_limits':MODEL,'allow_ips':'156.239.3.210','group':GROUP,'cross_group_retry':False}
   data=s.post(ORIGIN+'/api/token/',json=body,timeout=15).json();assert data.get('success')
   matches=[x for x in tokens(s) if x['name']==NAME]
  assert len(matches)==1
  token=matches[0];key=token.get('key')
  if not key or '*' in key:
   body=s.post(ORIGIN+'/api/token/'+str(token['id'])+'/key',timeout=15).json();assert body.get('success');key=body['data'];key=key.get('key') if isinstance(key,dict) else key
  assert isinstance(key,str) and key and '*' not in key
  key=key if key.startswith('sk-') else 'sk-'+key
  private('gpt61-key.json',{'id':token['id'],'key':key,'group':GROUP,'origin':ORIGIN})
  print(json.dumps({'provider':'codeplan','model':MODEL,'group':GROUP,'probe_key_id':token['id'],'maximum_budget_cny':1,'billing_expr':rows[0]['billing_expr']}),flush=True)
  if mode=='prepare':return
  marker=OUT/'gpt61-probe.json';assert not marker.exists(),'reconcile previous submission instead of repeating'
  private(marker.name,{'started':int(time.time()),'status':'submitted_once'})
  started=time.monotonic()
  try:
   r=requests.post(ORIGIN+'/v1/responses',headers={'Authorization':'Bearer '+key},json={'model':MODEL,'input':'Reply only OK.','max_output_tokens':32,'stream':False},timeout=(8,90))
   data=r.json();output=data.get('output') or [];text=''.join(c.get('text','') for item in output for c in item.get('content',[]) if isinstance(c,dict))
   result={'status':r.status_code,'success':r.status_code==200 and bool(text),'response_model':data.get('model'),'response_id':data.get('id'),'usage':data.get('usage'),'seconds':round(time.monotonic()-started,2),'request_id':r.headers.get('X-Request-Id') or r.headers.get('X-Oneapi-Request-Id'),'error':data.get('error') if r.status_code!=200 else None}
  except Exception as e:result={'success':False,'uncertain':True,'error_type':type(e).__name__}
  private(marker.name,result);print(json.dumps({k:v for k,v in result.items() if k!='error'},ensure_ascii=False),flush=True)
  body=s.get(ORIGIN+'/api/log/self',params={'p':1,'page_size':100,'token_name':NAME},timeout=20).json();assert body.get('success');private('gpt61-bills.json',body)
  print('actual billing evidence stored privately',flush=True)
 finally:
  try:b.standard_logout(s,ORIGIN)
  except Exception:pass

if __name__=='__main__':main(sys.argv[1] if len(sys.argv)>1 else 'prepare')
