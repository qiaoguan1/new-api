"""Back up and constrain only the native service's PostgreSQL connection pool."""
import json,os,pathlib,subprocess,time,yaml

ROOT=pathlib.Path('/opt/ai-api-stack')
BACKUP=ROOT/'backups/claude178-20260926'
VALUES={'SQL_MAX_OPEN_CONNS':'30','SQL_MAX_IDLE_CONNS':'5','SQL_MAX_LIFETIME':'300'}

def patch(text):
 data=yaml.safe_load(text);assert isinstance(data['services']['new-api']['environment'],dict)
 data['services']['new-api']['environment'].update(VALUES)
 result=yaml.safe_dump(data,allow_unicode=True,sort_keys=False)
 updated=yaml.safe_load(result);before=yaml.safe_load(text)
 for key in VALUES:
  if key in before['services']['new-api']['environment']:updated['services']['new-api']['environment'][key]=before['services']['new-api']['environment'][key]
  else:updated['services']['new-api']['environment'].pop(key)
 assert updated==before,'unrelated configuration change'
 return result

def main():
 path=ROOT/'docker-compose.override.yml';backup=BACKUP/'pool-compose.before.yml'
 assert not backup.exists(),'inspect previous application before repeating'
 content=path.read_text();replacement=patch(content)
 with os.fdopen(os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:f.write(content)
 snapshot=json.loads(subprocess.check_output(['docker','inspect','ai-api-stack-new-api-1']))[0]
 with os.fdopen(os.open(BACKUP/'pool-container.before.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as f:json.dump(snapshot,f)
 # Preserve file ownership/mode; write within the existing configuration file.
 path.write_text(replacement)
 try:
  resolved=json.loads(subprocess.check_output(['docker','compose','config','--format','json'],cwd=ROOT))
  service=resolved['services']['new-api'];assert service['image']==snapshot['Config']['Image']
  env=service['environment'];assert all(str(env[k])==v for k,v in VALUES.items())
  assert env['QUOTA_DB_AUTHORITATIVE']=='true' and env['BATCH_UPDATE_ENABLED']=='false'
 except Exception:
  path.write_text(content);raise
 subprocess.run(['docker','compose','up','-d','--no-deps','--pull','never','--timeout','60','new-api'],cwd=ROOT,check=True)
 subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-t'],check=True,capture_output=True)
 subprocess.run(['docker','exec','ai-api-stack-nginx-1','nginx','-s','reload'],check=True,capture_output=True)
 print(json.dumps({'pool':VALUES,'configuration_saved':True,'database_restarted':False}))

if __name__=='__main__':main()
