"""Promote verified credentials, activate staged routes, or disable only this rollout."""
import importlib.util,json,pathlib,requests,time,sys
local=pathlib.Path(__file__).parent
deploy=local/'deploy_claude.py';probe=local/'probe_claude.py'
sp=importlib.util.spec_from_file_location('d',deploy if deploy.exists() else '/tmp/xtai-claude178-deploy.py');d=importlib.util.module_from_spec(sp);sp.loader.exec_module(d)
sp=importlib.util.spec_from_file_location('p',probe if probe.exists() else '/tmp/xtai-claude178-probe.py');p=importlib.util.module_from_spec(sp);sp.loader.exec_module(p)

def promote():
 for slug in ['codeplan','paisio','maolao']:
  secret=json.loads((d.ROOT/(slug+'-probe-secret.json')).read_text());origin=secret['origin'];s=requests.Session();c=p.CREDS[slug]
  selected=[m['model'] for m in d.PLAN['models'] if any(r['source']==slug for r in m['candidates'])]
  try:
   p.b.standard_login(s,origin,c['username'],c['password'])
   records=p.list_tokens(s,origin);found=[r for r in records if r['id']==secret['token_id']];assert len(found)==1
   record=found[0];marker=d.ROOT/(slug+'-production-key.json')
   if marker.exists():
    proof=json.loads(marker.read_text());assert proof['token_id']==record['id'] and proof['models']==selected
    assert record['status']==(1 if selected else 2)
    if selected:
     assert record['expired_time']==-1 and record['unlimited_quota'] and record['model_limits_enabled']
     assert set(record['model_limits'].split(','))==set(selected) and record['allow_ips']=='156.239.3.210' and record['group']==secret['group']
    print(json.dumps({'source':slug,'verified_existing_promotion':True}),flush=True);continue
   d.h.private(d.ROOT/(slug+'-token-before-promotion.json'),record)
   # Unselected source key is disabled, never promoted or restored automatically.
   body={k:record[k] for k in ['id','name','expired_time','remain_quota','unlimited_quota','model_limits_enabled','model_limits','allow_ips','group','cross_group_retry'] if k in record}
   body.update(status=1 if selected else 2)
   if selected:body.update(name='xtai-claude178-production-'+slug,expired_time=-1,unlimited_quota=True,model_limits_enabled=True,model_limits=','.join(selected),allow_ips='156.239.3.210',cross_group_retry=False)
   # Native API status-only is a separate update route from editable token fields.
   if selected:
    response=s.put(origin+'/api/token/',json=body,timeout=15).json();assert response.get('success') is True
   else:
    response=s.put(origin+'/api/token/?status_only=true',json={'id':record['id'],'status':2},timeout=15).json();assert response.get('success') is True
   after=[r for r in p.list_tokens(s,origin) if r['id']==record['id']][0]
   assert after['status']==(1 if selected else 2)
   if selected:
    assert after['expired_time']==-1 and after['unlimited_quota'] and after['model_limits_enabled']
    assert set(after['model_limits'].split(','))==set(selected) and after['allow_ips']=='156.239.3.210' and after['group']==secret['group']
   d.h.private(marker,{'token_id':record['id'],'models':selected,'active':bool(selected),'verified_at':int(time.time())})
   print(json.dumps({'source':slug,'production_models':selected,'disabled':not selected}),flush=True)
  finally:
   try:p.b.standard_logout(s,origin)
   except Exception:pass

def gate():
 expected={f"Claude178:{p['source']}:{m['model']}" for m in d.PLAN['models'] for p in m['candidates']}
 routes=json.loads((d.ROOT/'new-api-applied.json').read_text());assert {r['name'] for r in routes}==expected
 for source in {p['source'] for m in d.PLAN['models'] for p in m['candidates']}:
  proof=json.loads((d.ROOT/(source+'-production-key.json')).read_text());assert proof['active']
 # Wait at least one whole option synchronization interval after staging.
 assert time.time()-(d.ROOT/'new-api-applied.json').stat().st_mtime>=65,'pricing not yet synchronized'
 ids=','.join(str(r['id']) for r in routes)
 live=d.rows('SELECT id,name,models,"group",priority,status FROM channels WHERE id IN ('+ids+')','new-api')
 expected_routes={r['id']:r for r in routes}
 assert len(live)==len(routes) and {r['id'] for r in live}==set(expected_routes)
 for r in live:
  saved=expected_routes[r['id']]
  assert r['name']==saved['name'] and r['models']==saved['models'] and r['priority']==saved['priority'] and r['group']=='文' and r['status']==2
 abilities=d.rows('SELECT channel_id,model,"group",enabled,priority FROM abilities WHERE channel_id IN ('+ids+')','new-api')
 assert len(abilities)==len(routes) and {r['channel_id'] for r in abilities}==set(expected_routes)
 for r in abilities:
  assert not r['enabled'] and r['group']=='文' and r['model']==expected_routes[r['channel_id']]['models'] and r['priority']==expected_routes[r['channel_id']]['priority']
 names=','.join(d.q(m['model']) for m in d.PLAN['models'])
 metadata=d.rows('SELECT model_name,status FROM models WHERE deleted_at IS NULL AND model_name IN ('+names+')','new-api')
 assert len(metadata)==len(d.PLAN['models']) and all(r['status']==0 for r in metadata)
 # Read the running service's in-memory option map through existing authorized admin access.
 admin=d.rows('SELECT id,trim(access_token) AS token FROM users WHERE id=1 AND role=100 AND status=1','new-api')[0]
 response=requests.get(d.h.address(d.h.NATIVE,3000)+'/api/option/',headers={'Authorization':'Bearer '+admin['token'],'New-Api-User':str(admin['id'])},timeout=15)
 response.raise_for_status();body=response.json();assert body['success']
 runtime={r['key']:r['value'] for r in body['data']};assert float(json.loads(runtime['GroupRatio'])['文'])==.15
 for option in ['ModelRatio','CompletionRatio','CacheRatio','CreateCacheRatio']:
  values=json.loads(runtime[option])
  for m in d.PLAN['models']:assert abs(values[m['model']]-m['options'][option])<1e-10,(option,m['model'])
 d.h.private(d.ROOT/'runtime-pricing-verified.json',{'time':int(time.time()),'models':[m['model'] for m in d.PLAN['models']]})
 return routes

def activate():
 routes=gate();ids=','.join(str(r['id']) for r in routes);models=','.join(d.q(m['model']) for m in d.PLAN['models'])
 current=d.rows('SELECT id,name,status FROM channels WHERE id IN ('+ids+')','new-api');assert all(r['status']==2 for r in current)
 d.h.sql('BEGIN; SET LOCAL lock_timeout=\'5s\'; LOCK TABLE channels,abilities,models IN SHARE ROW EXCLUSIVE MODE; DO $$ BEGIN IF (SELECT count(*) FROM channels WHERE status=2 AND id IN ('+ids+'))<>'+str(len(routes))+' THEN RAISE EXCEPTION \'staged routes changed\'; END IF; END $$; UPDATE channels SET status=1 WHERE id IN ('+ids+'); UPDATE abilities SET enabled=true WHERE channel_id IN ('+ids+') AND "group"=\'文\'; UPDATE models SET status=1 WHERE deleted_at IS NULL AND model_name IN ('+models+'); COMMIT;')
 d.h.private(d.ROOT/'production-activated.json',{'ids':[r['id'] for r in routes],'time':int(time.time())})
 print('Claude routes activated',len(routes))

def disable():
 routes=json.loads((d.ROOT/'new-api-applied.json').read_text());ids=','.join(str(r['id']) for r in routes)
 names=','.join(d.q(m['model']) for m in d.PLAN['models'])
 d.h.sql('BEGIN; UPDATE channels SET status=2 WHERE id IN ('+ids+'); UPDATE abilities SET enabled=false WHERE channel_id IN ('+ids+'); UPDATE models SET status=0 WHERE deleted_at IS NULL AND model_name IN ('+names+'); COMMIT;')
 print('only Claude178 routes disabled; old routes and prices unchanged')

if __name__=='__main__':{'promote':promote,'activate':activate,'disable':disable}[sys.argv[1]]()
