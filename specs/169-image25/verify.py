"""Limited public-path canary; revoke the operator token even on failure."""
import json,pathlib,secrets,subprocess,time,requests

def sql(q):return subprocess.check_output(['docker','exec','-i','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAt','-v','ON_ERROR_STOP=1'],input=q,text=True).strip()
key=secrets.token_hex(24);now=int(time.time())
tid=int(sql(f'''INSERT INTO tokens(user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry) VALUES(1,'{key}',1,'ops169-canary',{now},{now},{now+900},500000,false,true,'gpt-image-2.5','',0,'图',false) RETURNING id;''').splitlines()[0])
headers={'Authorization':'Bearer sk-'+key}
base='https://api.aixingtuyun.com'
results=[]
try:
    for attempt in range(20):
        r=requests.get(base+'/v1/models',headers=headers,timeout=10)
        rows=r.json().get('data',[])
        if any(x.get('id')=='gpt-image-2.5' for x in rows):break
        time.sleep(3)
    else:raise RuntimeError('model discovery not refreshed')
    print(json.dumps({'model_discovery':True}),flush=True)
    pricing=requests.get(base+'/api/pricing',timeout=10).json()
    matched=[x for x in pricing.get('data',[]) if x.get('model_name')=='gpt-image-2.5']
    assert matched and matched[0].get('billing_mode')=='tiered_expr', 'pricing cache not ready'
    print(json.dumps({'pricing':matched},ensure_ascii=False),flush=True)
    before=int(sql(f'SELECT used_quota FROM tokens WHERE id={tid};'))
    for body,ctype in [({'model':'gpt-image-2.5','prompt':'validation only','size':'auto','n':1},'json'),({'model':'gpt-image-2.5','prompt':'validation only','size':'4096x4096','n':1},'form')]:
        r=requests.post(base+'/v1/images/generations',headers=headers,**({'json':body} if ctype=='json' else {'data':body}),timeout=(10,25))
        assert r.status_code!=200, 'invalid payload unexpectedly accepted'
        print(json.dumps({'negative_test':ctype,'http':r.status_code}),flush=True)
    assert int(sql(f'SELECT used_quota FROM tokens WHERE id={tid};'))==before,'rejected request charged'
    for size in ['1024x1024','2048x2048','4096x4096']:
        start=time.monotonic()
        r=requests.post(base+'/v1/images/generations',headers=headers,json={'model':'gpt-image-2.5','prompt':'A plain green circle on white, no text.','size':size,'n':1,'response_format':'b64_json'},timeout=(10,220))
        try:d=r.json()
        except ValueError:d={}
        rows=d.get('data') or []
        row={'size':size,'http':r.status_code,'has_image':bool(rows and isinstance(rows[0].get('b64_json'),str)),'seconds':round(time.monotonic()-start,2)}
        if not row['has_image']:row['error']=d.get('error')
        results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
        assert row['has_image'], 'generation failed; stop testing'
    log=json.loads(sql(f"SELECT coalesce(json_agg(r),'[]') FROM (SELECT id,channel_id,quota,other::jsonb->>'billing_mode' as billing_mode,other::jsonb->>'matched_tier' as tier FROM logs WHERE token_id={tid} AND type=2 ORDER BY id)r;"))
    print(json.dumps({'billing_rows':log,'test_token':tid}),flush=True)
    assert [x['quota'] for x in log]==[18750,37500,75000], 'actual pricing mismatch'
    assert [x['channel_id'] for x in log]==[55,56,56], 'route mismatch'
finally:
    sql(f'UPDATE tokens SET status=2,expired_time={int(time.time())-1} WHERE id={tid};')
    print(json.dumps({'revoked_token':tid}),flush=True)
    pathlib.Path('/opt/ai-api-stack/backups/image25-20260921/public-verification.json').write_text(json.dumps(results))
