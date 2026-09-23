import json,pathlib,secrets,subprocess,time,requests
def sql(q):return subprocess.check_output(['docker','exec','-i','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAt','-v','ON_ERROR_STOP=1'],input=q,text=True).strip()
models=['gpt-image-2.5-flare','gpt-image-2.5-sunburst'];key=secrets.token_hex(24);now=int(time.time())
tid=int(sql(f'''INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) VALUES(1,'{key}',1,'ops171-canary',{now},{now},{now+600},600000,false,true,'{','.join(models)}','',0,'图',false) RETURNING id;''').splitlines()[0])
h={'Authorization':'Bearer sk-'+key};base='https://api.aixingtuyun.com';results=[]
try:
 d=requests.get(base+'/v1/models',headers=h,timeout=15).json();names={x.get('id') for x in d.get('data',[])};assert set(models)<=names,'catalog not ready'
 for model in models:
  before=int(sql(f'SELECT used_quota FROM tokens WHERE id={tid};'));t=time.monotonic()
  r=requests.post(base+'/v1/images/generations',headers=h,json={'model':model,'prompt':'A plain green circle on a white background.','size':'1024x1024','n':1,'response_format':'b64_json'},timeout=(10,210))
  d=r.json();rows=d.get('data') or [];ok=bool(rows and rows[0].get('b64_json'));elapsed=round(time.monotonic()-t,2)
  after=int(sql(f'SELECT used_quota FROM tokens WHERE id={tid};'))
  out={'model':model,'http':r.status_code,'has_image':ok,'seconds':elapsed,'charged_quota':after-before}
  if not ok:out['error']=d.get('error')
  results.append(out);print(json.dumps(out,ensure_ascii=False),flush=True)
  assert r.status_code==200 and ok and after-before==225000,'stop: generation or price mismatch'
finally:
 sql(f'UPDATE tokens SET status=2,expired_time={int(time.time())-1} WHERE id={tid};')
 print(json.dumps({'revoked_token':tid}),flush=True)
 pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence/public-verification.json').write_text(json.dumps(results))
