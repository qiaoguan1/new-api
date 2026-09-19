"""Backed-up routing-only update; video, GPT6 and retail prices are invariant."""
import datetime,json,os,pathlib,subprocess
os.umask(0o077)
def sql(q):
    return subprocess.check_output(['docker','exec','-i','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAt','-v','ON_ERROR_STOP=1'],input=q,text=True).strip()
def lit(s):return "'"+str(s).replace("'","''")+"'"
scope=[23,37,38,39,48,52]
core=['gpt-5.5','gpt-5.6-sol','gpt-5.6-terra','gpt-5.6-luna','gpt-image-2']
ids=','.join(map(str,scope));models=','.join(map(lit,core))
snapshot=json.loads(sql(f"SELECT json_build_object('channels',(SELECT json_agg(c) FROM channels c WHERE id IN({ids})),'abilities',(SELECT json_agg(a) FROM abilities a WHERE channel_id IN({ids})));"))
protected_query="""SELECT json_build_object('video',(SELECT json_agg(c ORDER BY id) FROM (SELECT id,status,models,model_mapping,priority,base_url FROM channels WHERE id IN(42,43,46))c),'gpt6',(SELECT json_agg(a ORDER BY channel_id,model,\"group\") FROM abilities a WHERE model IN('gpt-6','gpt-6-astra')),'prices',(SELECT json_agg(o ORDER BY key) FROM options o WHERE key IN('ModelRatio','CompletionRatio','ModelPrice','GroupRatio')));"""
protected=json.loads(sql(protected_query))
backup=pathlib.Path('/opt/ai-api-stack/backups')/('funded-core-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'));backup.mkdir(mode=0o700)
(backup/'before.json').write_text(json.dumps(snapshot,ensure_ascii=False),encoding='utf-8')
(backup/'protected-before.json').write_text(json.dumps(protected,ensure_ascii=False),encoding='utf-8')

# Channel cache ranks with channel priority; ability priorities stay in sync.
route_models={23:['gpt-image-2'],38:['gpt-image-2'],39:['gpt-5.5','gpt-5.6-sol','gpt-5.6-terra'],48:['gpt-5.5','gpt-5.6-sol'],52:['gpt-image-2']}
priorities={23:10,38:8,39:10,48:8,52:6}
commands=['BEGIN;',"SELECT pg_advisory_xact_lock(hashtext('funded-core-routing'));" ]
rollback=['BEGIN;']
for ch in snapshot['channels']:
    cid=ch['id']
    rollback.append('UPDATE channels SET '+','.join(k+'='+('NULL' if ch[k] is None else lit(ch[k])) for k in ['models','status','priority'])+f' WHERE id={cid};')
    if cid==37:
        commands.extend(['UPDATE channels SET status=2 WHERE id=37;','UPDATE abilities SET enabled=false WHERE channel_id=37;']);continue
    unrelated=[m for m in (ch['models'] or '').split(',') if m and m not in core]
    enabled=route_models[cid]
    commands.append(f"UPDATE channels SET status=1,priority={priorities[cid]},models={lit(','.join(unrelated+enabled))} WHERE id={cid};")
    commands.append(f'DELETE FROM abilities WHERE channel_id={cid} AND model IN({models});')
    for model in enabled:
        group='图' if model=='gpt-image-2' else '文'
        commands.append(f'INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight) VALUES({lit(group)},{lit(model)},{cid},true,{priorities[cid]},0);')

token=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/toonflow-image-adapter.key').read_text().strip()
existing=sql("SELECT id FROM channels WHERE name='Toonflow · Image-2 1K';")
if existing:raise SystemExit('Toonflow channel already exists; inspect before reapplying')
commands.append(f'''INSERT INTO channels (type,key,status,name,weight,created_time,base_url,models,"group",priority,auto_ban,model_mapping)
 VALUES(1,{lit(token)},1,'Toonflow · Image-2 1K',0,extract(epoch from now())::bigint,'http://xtai-toonflow-image-adapter:8094','gpt-image-2','图',7,0,'{{}}');''')
commands.append('''INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight) SELECT '图','gpt-image-2',id,true,7,0 FROM channels WHERE name='Toonflow · Image-2 1K';''')
commands.append('COMMIT;')
rollback.append(f'DELETE FROM abilities WHERE channel_id IN({ids});')
for row in snapshot['abilities']:
    columns=['group','model','channel_id','enabled','priority','weight','tag']
    vals=['NULL' if row.get(k) is None else str(row[k]).lower() if isinstance(row[k],bool) else lit(row[k]) for k in columns]
    rollback.append('INSERT INTO abilities ("group",model,channel_id,enabled,priority,weight,tag) VALUES('+','.join(vals)+');')
rollback.extend(["UPDATE abilities SET enabled=false WHERE channel_id IN(SELECT id FROM channels WHERE name='Toonflow · Image-2 1K');","UPDATE channels SET status=2 WHERE name='Toonflow · Image-2 1K';",'COMMIT;'])
(backup/'rollback.sql').write_text('\n'.join(rollback),encoding='utf-8')
sql('\n'.join(commands))
after=json.loads(sql(protected_query))
if after!=protected:raise SystemExit('protected configuration changed during deployment: inspect concurrently applied changes')
print(json.dumps({'backup':str(backup),'toonflow_channel':sql("SELECT id FROM channels WHERE name='Toonflow · Image-2 1K';"),'protected_unchanged':True},ensure_ascii=False))
