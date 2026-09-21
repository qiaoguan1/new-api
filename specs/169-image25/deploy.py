"""Explicit, additive onboarding with protected-state comparison and rollback."""
import datetime,json,os,pathlib,secrets,subprocess,sys

MODEL='gpt-image-2.5'
EXPR='param("size") == "4096x4096" ? tier("4k", 1000000) : param("size") == "2048x2048" ? tier("2k", 500000) : tier("1k", 250000)'
ROOT=pathlib.Path('/opt/xtai-image25-adapter')
os.umask(0o077)
def sql(query):return subprocess.check_output(['docker','exec','-i','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAt','-v','ON_ERROR_STOP=1'],input=query,text=True).strip()
def lit(value):return "'"+str(value).replace("'","''")+"'"
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');path.chmod(0o600)

if sys.argv[1]=='prepare':
    assert not (ROOT/'config.json').exists(),'inspect existing config first'
    rows=json.loads(sql('SELECT json_agg(c) FROM (SELECT id,key,base_url FROM channels WHERE id IN(45,49))c;'))
    assert len(rows)==2
    cfg={'adapter_token':secrets.token_urlsafe(36),'providers':{('rolldek' if x['id']==45 else 'hanhe'):{'base_url':x['base_url'],'key':x['key']} for x in rows}}
    write(ROOT/'config.json',cfg);os.chown(ROOT/'config.json',10001,10001)
    print(json.dumps({'prepared':True,'providers':list(cfg['providers'])}))
elif sys.argv[1]=='apply':
    assert not sql("SELECT id FROM channels WHERE models LIKE '%gpt-image-2.5%';"),'model already configured; inspect first'
    cfg=json.loads((ROOT/'config.json').read_text())
    old_channels=sql('SELECT json_agg(c ORDER BY id) FROM channels c;')
    old_options={x['key']:x['value'] for x in json.loads(sql('SELECT json_agg(o) FROM options o;'))}
    groups=json.loads(old_options['GroupRatio']);assert groups['图']==0.15
    keys=['ModelPrice','billing_setting.billing_mode','billing_setting.billing_expr']
    new={k:json.loads(old_options.get(k,'{}')) for k in keys}
    assert all(MODEL not in x for x in new.values())
    new['ModelPrice'][MODEL]=0.25
    new['billing_setting.billing_mode'][MODEL]='tiered_expr'
    new['billing_setting.billing_expr'][MODEL]=EXPR
    backup=pathlib.Path('/opt/ai-api-stack/backups')/('image25-onboard-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(mode=0o700)
    write(backup/'before.json',{'channels':json.loads(old_channels),'options':old_options})
    commands=['BEGIN;',"SELECT pg_advisory_xact_lock(hashtext('image25-onboarding'));"]
    for key,value in new.items():
        if key in old_options:
            commands.append('DO $$ BEGIN IF (SELECT value FROM options WHERE key='+lit(key)+') IS DISTINCT FROM '+lit(old_options[key])+" THEN RAISE EXCEPTION 'pricing changed concurrently'; END IF; END $$;")
        else:commands.append('DO $$ BEGIN IF EXISTS(SELECT 1 FROM options WHERE key='+lit(key)+") THEN RAISE EXCEPTION 'new option appeared concurrently'; END IF; END $$;")
        commands.append('INSERT INTO options(key,value) VALUES('+lit(key)+','+lit(json.dumps(value,separators=(',',':')))+') ON CONFLICT(key) DO UPDATE SET value=excluded.value;')
    for provider,priority in [('rolldek',10),('hanhe',8)]:
        name='Image-2.5 · '+provider
        commands.append('INSERT INTO channels (type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,model_mapping) VALUES(1,'+lit(cfg['adapter_token'])+',1,'+lit(name)+',0,extract(epoch from now())::bigint,'+lit('http://xtai-image25-adapter:8095/'+provider)+','+lit(MODEL)+",'图',"+str(priority)+",0,'{}');")
        commands.append('INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight) SELECT '+lit('图')+','+lit(MODEL)+',id,true,'+str(priority)+',0 FROM channels WHERE name='+lit(name)+';')
    commands.append('COMMIT;');sql('\n'.join(commands))
    added=json.loads(sql("SELECT json_agg(c) FROM (SELECT id,name FROM channels WHERE models='gpt-image-2.5')c;"))
    ids=[x['id'] for x in added]
    assert sql('SELECT json_agg(c ORDER BY id) FROM channels c WHERE id NOT IN('+','.join(map(str,ids))+');')==old_channels
    rollback=['BEGIN;','UPDATE abilities SET enabled=false WHERE channel_id IN('+','.join(map(str,ids))+');','UPDATE channels SET status=2 WHERE id IN('+','.join(map(str,ids))+');']
    for key in keys:
        rollback.append('UPDATE options SET value='+lit(old_options[key])+' WHERE key='+lit(key)+';' if key in old_options else 'DELETE FROM options WHERE key='+lit(key)+';')
    rollback.append('COMMIT;');(backup/'rollback.sql').write_text('\n'.join(rollback))
    write(backup/'result.json',{'channels':added,'prices':{'1K':0.0375,'2K':0.075,'4K':0.15}})
    print(json.dumps({'backup':str(backup),'new_channels':added,'existing_channels_unchanged':True}))
