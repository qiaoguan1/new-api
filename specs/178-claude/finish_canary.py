"""Reconcile first canary, remove only failed routes, test remaining paths."""
import importlib.util,json,pathlib,time,requests
sp=importlib.util.spec_from_file_location('d',pathlib.Path(__file__).with_name('deploy_claude.py'));d=importlib.util.module_from_spec(sp);sp.loader.exec_module(d)

def reconcile():
 uid=json.loads((d.ROOT/(d.DBTEST+'-account.json')).read_text())['user_id']
 bills=d.rows('SELECT model_name,quota,prompt_tokens,completion_tokens,other,channel_id FROM logs WHERE user_id='+str(uid)+' AND type=2 ORDER BY id',d.DBTEST)
 account=d.rows('SELECT quota,used_quota FROM users WHERE id='+str(uid),d.DBTEST)[0]
 d.h.private(d.ROOT/'canary-reconciled-bills.json',bills)
 print('reconciled',len(bills),account,'bill_sum',sum(r['quota'] for r in bills),flush=True)
 assert len(bills)==8 and 500000-account['quota']==sum(r['quota'] for r in bills),'pending requests; do not restart/replay'

def revise():
 reconcile()
 statements=['BEGIN;']
 for r in d.rows("SELECT id,name FROM channels WHERE name LIKE 'Claude178:%'",d.DBTEST):
  allowed={f"Claude178:{p['source']}:{m['model']}":p['priority'] for m in d.PLAN['models'] for p in m['candidates']}
  status=1 if r['name'] in allowed else 2;priority=allowed.get(r['name'],0)
  statements += ['UPDATE channels SET status='+str(status)+',priority='+str(priority)+' WHERE id='+str(r['id'])+';', 'UPDATE abilities SET enabled='+('true' if status==1 else 'false')+',priority='+str(priority)+' WHERE channel_id='+str(r['id'])+';']
 for key in ['ModelRatio','CompletionRatio','CacheRatio','CreateCacheRatio']:
  current=json.loads(d.rows('SELECT value FROM options WHERE key='+d.q(key),d.DBTEST)[0]['value'])
  for m in d.PLAN['models']:current[m['model']]=m['options'][key]
  statements.append('UPDATE options SET value='+d.q(json.dumps(current))+' WHERE key='+d.q(key)+';')
 statements+=['COMMIT;'];d.h.sql('\n'.join(statements),d.DBTEST)
 import subprocess
 subprocess.run(['docker','restart',d.CANARY],check=True,capture_output=True)
 print('revised routes; canary restarted',flush=True)

def tests():
 marker=d.ROOT/'canary-extra-tests.json';assert not marker.exists()
 a=json.loads((d.ROOT/(d.DBTEST+'-account.json')).read_text());base=d.h.address(d.CANARY,3000);headers={'Authorization':'Bearer '+a['key']};results=[];d.h.private(marker,results)
 cases=[('claude-haiku-4-5-20251001','/v1/chat/completions',False),('claude-opus-4-8','/v1/chat/completions',False),('claude-sonnet-5','/v1/chat/completions',True),('claude-opus-5','/v1/messages',False)]
 for model,path,stream in cases:
  before=int(d.h.sql('SELECT quota FROM users WHERE id='+str(a['user_id']),d.DBTEST));t=time.time()
  body={'model':model,'messages':[{'role':'user','content':'Reply only OK.'}],'max_tokens':16,'stream':stream}
  r=requests.post(base+path,headers={**headers,'anthropic-version':'2023-06-01'},json=body,timeout=40)
  text=r.text;data=r.json() if not stream else {};ok=r.status_code==200 and (('[DONE]' in text and 'content' in text) if stream else bool(data.get('choices') or data.get('content')))
  after=int(d.h.sql('SELECT quota FROM users WHERE id='+str(a['user_id']),d.DBTEST))
  row={'model':model,'path':path,'stream':stream,'status':r.status_code,'success':ok,'seconds':round(time.time()-t,2),'delta':before-after,'usage':data.get('usage'),'error':data.get('error')};results.append(row);d.h.private(marker,results);print(json.dumps(row,ensure_ascii=False),flush=True)
  assert ok and before>after,'format/cost test failed'

if __name__=='__main__':
 import sys
 {'reconcile':reconcile,'revise':revise,'tests':tests}[sys.argv[1]]()
