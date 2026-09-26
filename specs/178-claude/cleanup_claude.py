"""Remove only task-created canary resources, preserving private evidence and live routes."""
import importlib.util,json,pathlib,subprocess
sp=importlib.util.spec_from_file_location('d',pathlib.Path(__file__).with_name('deploy_claude.py'));d=importlib.util.module_from_spec(sp);sp.loader.exec_module(d)

def main():
 assert json.loads((d.ROOT/'production-canary.json').read_text())['success']
 a=json.loads((d.ROOT/'new-api-account.json').read_text());uid=a['user_id'];tid=a['token_id']
 users=d.rows('SELECT id,username,quota,used_quota FROM users WHERE id='+str(uid),'new-api');assert len(users)==1 and users[0]['username']=='claude178-canary'
 tokens=d.rows('SELECT id,user_id,name,used_quota FROM tokens WHERE id='+str(tid),'new-api');assert len(tokens)==1 and tokens[0]['user_id']==uid and tokens[0]['name']=='claude178-canary'
 d.h.private(d.ROOT/'production-test-account-final.json',{'users':users,'tokens':tokens})
 d.h.sql('BEGIN; UPDATE tokens SET status=2,remain_quota=0,expired_time=0,deleted_at=now() WHERE id='+str(tid)+' AND user_id='+str(uid)+'; UPDATE users SET status=2,quota=0,deleted_at=now() WHERE id='+str(uid)+' AND username=\'claude178-canary\'; COMMIT;')
 c=json.loads((d.ROOT/(d.DBTEST+'-account.json')).read_text());cu=c['user_id']
 bills=d.rows('SELECT model_name,quota,prompt_tokens,completion_tokens,other,channel_id FROM logs WHERE type=2 AND user_id='+str(cu)+' ORDER BY id',d.DBTEST)
 quota=int(d.h.sql('SELECT quota FROM users WHERE id='+str(cu),d.DBTEST))
 assert 500000-quota==sum(r['quota'] for r in bills),'unsettled canary calls remain'
 d.h.private(d.ROOT/'canary-final-evidence.json',{'bills':bills,'remaining_test_quota':quota})
 info=d.h.inspect(d.CANARY);assert info['Config']['Image']=='new-api-fixed:issue173-quota-authority'
 env=d.h.env(info);assert '/'+d.DBTEST in env['SQL_DSN'] and env['SQL_MAX_OPEN_CONNS']=='5'
 subprocess.run(['docker','stop','--time','30',d.CANARY],check=True,capture_output=True)
 subprocess.run(['docker','rm',d.CANARY],check=True,capture_output=True)
 assert d.DBTEST=='xtai_claude178_canary'
 assert d.h.sql("SELECT count(*) FROM pg_stat_activity WHERE datname='xtai_claude178_canary';")=='0'
 d.h.sql('DROP DATABASE xtai_claude178_canary;')
 d.h.private(d.ROOT/'cleanup-complete.json',{'test_account_revoked':uid,'test_token_revoked':tid,'canary_container_removed':d.CANARY,'canary_database_removed':d.DBTEST,'evidence_preserved':True})
 print('Only task-created canary container/database removed; own test account/token revoked and synthetic remaining balance cleared. Production routes preserved.')

if __name__=='__main__':main()
