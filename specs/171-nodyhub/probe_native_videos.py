"""One native submission per selected model; persist task IDs before any polling."""
import json,os,pathlib,requests,time,sys
os.umask(0o077)
root=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence')
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
models={'wan3.0-video':('2','854x480'),'grok-imagine-1.5-video':('1','854x480'),'grok-imagine-video-official':('1','854x480'),'grok-video-3':('1','854x480'),'omni-flash':('2','1280x720'),'flux-3-video':('5','1280x720')}
for model in sys.argv[1:]:
 assert model in models
 path=root/(model+'-native-test.json')
 if path.exists():print(json.dumps({'model':model,'skipped':'already_attempted'}));continue
 seconds,size=models[model];body={'model':model,'prompt':'A small blue ball rolling gently on a plain white table.','seconds':seconds,'size':size}
 out={'model':model,'request':body,'started_at':int(time.time()),'state':'submitting'};path.write_text(json.dumps(out))
 try:
  r=requests.post('https://nodyhub.com/v1/videos',headers={'Authorization':'Bearer '+key},json=body,timeout=(10,90))
  out['http']=r.status_code
  try:d=r.json()
  except ValueError:d={'non_json':True,'prefix':r.text[:250]}
  out['response']=d
 except Exception as e:out['observation_error_type']=type(e).__name__
 out['state']='attempt_recorded';path.write_text(json.dumps(out,ensure_ascii=False))
 print(json.dumps(out,ensure_ascii=False)[:2200],flush=True)
