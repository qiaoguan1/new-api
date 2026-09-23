"""Add only tested NodyHub image SKUs; preserve all existing channels/prices."""
import datetime,json,os,pathlib,secrets,shutil,subprocess,sys
os.umask(0o077)
ROOT=pathlib.Path('/opt/xtai-nodyhub-image-adapter')
MON=pathlib.Path('/opt/ai-api-stack/channel-monitor')
MODELS=['gpt-image-2.5-flare','gpt-image-2.5-sunburst']
NAME='NodyHub · Image2.5 Flare/Sunburst'
def sql(q):return subprocess.check_output(['docker','exec','-i','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAt','-v','ON_ERROR_STOP=1'],input=q,text=True).strip()
def lit(x):return "'"+str(x).replace("'","''")+"'"
def write(p,d):
 t=p.with_name(p.name+'.tmp');t.write_text(json.dumps(d,ensure_ascii=False,indent=2));t.chmod(0o600);os.replace(t,p)
if sys.argv[1]=='prepare':
 assert not (ROOT/'config.json').exists()
 key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-requested-20260923.key').read_text().strip()
 write(ROOT/'config.json',{'adapter_token':secrets.token_urlsafe(36),'providers':{'nodyhub':{'base_url':'https://nodyhub.com','key':key}}});os.chown(ROOT/'config.json',10001,10001)
 print('prepared')
elif sys.argv[1]=='apply':
 assert not sql('SELECT id FROM channels WHERE name='+lit(NAME)+';')
 old_channels=sql('SELECT json_agg(c ORDER BY id) FROM channels c;')
 opts={x['key']:x['value'] for x in json.loads(sql('SELECT json_agg(o) FROM options o;'))}
 price=json.loads(opts['ModelPrice']);assert not any(m in price for m in MODELS)
 assert json.loads(opts['GroupRatio'])['图']==0.15
 cfg=json.loads((ROOT/'config.json').read_text());back=pathlib.Path('/opt/ai-api-stack/backups')/('nodyhub171-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));back.mkdir(mode=0o700)
 write(back/'database-before.json',{'channels':json.loads(old_channels),'options':opts})
 for name in ['upstreams.json','upstream-credentials.json']:shutil.copy2(MON/name,back/name);os.chmod(back/name,0o600)
 for model in MODELS:price[model]=3.0
 rollback=['BEGIN;','UPDATE abilities SET enabled=false WHERE channel_id IN(SELECT id FROM channels WHERE name='+lit(NAME)+');','UPDATE channels SET status=2 WHERE name='+lit(NAME)+';','UPDATE options SET value='+lit(opts['ModelPrice'])+" WHERE key='ModelPrice';",'COMMIT;']
 (back/'rollback.sql').write_text('\n'.join(rollback))
 commands=['BEGIN;',"SELECT pg_advisory_xact_lock(hashtext('nodyhub171'));",'DO $$ BEGIN IF (SELECT value FROM options WHERE key=\'ModelPrice\') IS DISTINCT FROM '+lit(opts['ModelPrice'])+" THEN RAISE EXCEPTION 'prices changed'; END IF; END $$;",'UPDATE options SET value='+lit(json.dumps(price))+" WHERE key='ModelPrice';"]
 commands.append('INSERT INTO channels (type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,model_mapping) VALUES(1,'+lit(cfg['adapter_token'])+',1,'+lit(NAME)+",0,extract(epoch from now())::bigint,'http://xtai-nodyhub-image-adapter:8097/nodyhub',"+lit(','.join(MODELS))+",'图',8,0,'{}');")
 for model in MODELS:commands.append('INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight) SELECT \'图\','+lit(model)+',id,true,8,0 FROM channels WHERE name='+lit(NAME)+';')
 commands.append('COMMIT;');sql('\n'.join(commands))
 cid=int(sql('SELECT id FROM channels WHERE name='+lit(NAME)+';'))
 assert sql(f'SELECT json_agg(c ORDER BY id) FROM channels c WHERE id<>{cid};')==old_channels
 upstreams=json.loads((MON/'upstreams.json').read_text());found=[x for x in upstreams if x.get('slug')=='nodyhub'];assert len(found)==1
 found[0]['enabled']=True
 credentials=json.loads((MON/'upstream-credentials.json').read_text());credentials['nodyhub']['rate']=1.5
 write(MON/'upstreams.json',upstreams);write(MON/'upstream-credentials.json',credentials)
 result={'backup':str(back),'channel_id':cid,'models':MODELS,'cost_cny_per_image':0.3,'retail_cny_per_image':0.45,'conversion_cny_per_credit':1.5,'other_channels_unchanged':True}
 write(back/'result.json',result);print(json.dumps(result))
