"""Create the operator-requested short-lived restricted upstream test key."""
import importlib.util,json,os,pathlib,requests,sys,time
ROOT=pathlib.Path('/opt/ai-api-stack/channel-monitor')
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
spec=importlib.util.spec_from_file_location('balance',ROOT/'scripts/fetch-upstream-balance.py');b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
c=json.loads((ROOT/'upstream-credentials.json').read_text())['nodyhub']
MODELS=['grok-imagine-image-2.0','wan3.0-video','wan3.0-video-prime','flux-3-video','grok-video-3','grok-imagine-1.5-video','grok-imagine-video-official','omni-flash']
NAME='xtai-remaining-tests-20260923'
session=requests.Session();origin='https://nodyhub.com'
def tokens():
    d=session.get(origin+'/api/token/',params={'p':1,'size':100},timeout=15).json()
    assert d.get('success') is True
    a=d.get('data') or {};return a.get('items',[]) if isinstance(a,dict) else a
try:
    b.standard_login(session,origin,c['username'],c['password'])
    found=[x for x in tokens() if x.get('name')==NAME]
    if not found:
        body={'name':NAME,'expired_time':int(time.time())+86400,'remain_quota':1000000,'unlimited_quota':False,'model_limits_enabled':True,'model_limits':','.join(MODELS),'allow_ips':'156.239.3.210','group':'默认通道','cross_group_retry':False}
        response=session.post(origin+'/api/token/',json=body,timeout=15).json()
        if response.get('success') is not True:raise RuntimeError('upstream rejected key creation')
        found=[x for x in tokens() if x.get('name')==NAME]
    assert len(found)==1,'Ambiguous key identity'
    token=found[0];key=token['key'];key=key if key.startswith('sk-') else 'sk-'+key
    assert '*' not in key
    target=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key')
    if target.exists():assert target.read_text().strip()==key,'Existing secret differs'
    else:
        fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(key)
    metadata={k:token.get(k) for k in ['id','name','status','expired_time','remain_quota','unlimited_quota','model_limits_enabled','model_limits','allow_ips','group']}
    target.with_suffix('.meta.json').write_text(json.dumps(metadata,ensure_ascii=False));target.with_suffix('.meta.json').chmod(0o600)
    print(json.dumps({'test_key':metadata,'cost_cap_cny':3.0,'secret_value_printed':False},ensure_ascii=False))
    rr=requests.get(origin+'/v1/models',headers={'Authorization':'Bearer '+key,'User-Agent':'Mozilla/5.0'},timeout=15)
    data=rr.json();names=[x.get('id') for x in data.get('data',[]) if isinstance(x,dict)]
    print(json.dumps({'catalog_http':rr.status_code,'catalog':names,'error':data.get('error') if rr.status_code!=200 else None},ensure_ascii=False))
finally:b.standard_logout(session,origin)
