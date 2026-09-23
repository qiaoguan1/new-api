"""Corrected protocol probes, only after earlier attempts reached terminal failure."""
import json,os,pathlib,requests,time,sys
os.umask(0o077);root=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence')
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
cases={'wan3.0-video':(2,'480p'),'wan3.0-video-prime':(2,'480p'),'grok-imagine-1.5-video':(1,'480p'),'grok-imagine-video-official':(1,'480p'),'omni-flash':(2,'720p'),'flux-3-video':(5,'720p'),'grok-video-3':(1,'720p')}
for model in sys.argv[1:]:
 assert model in cases
 target=root/(model+'-v2-test.json')
 if target.exists():print(json.dumps({'model':model,'skipped':'already_attempted'}),flush=True);continue
 duration,resolution=cases[model]
 body={'model':model,'prompt':'A small blue ball rolling gently on a plain white table.','duration':duration,'resolution':resolution,'aspect_ratio':'16:9'}
 endpoint='/v2/videos/generations'
 if model=='grok-video-3':body={'model':model,'prompt':body['prompt'],'seconds':'1','size':'1280x720'};endpoint='/v1/videos'
 out={'model':model,'request':body,'endpoint':endpoint,'started_at':int(time.time()),'state':'submitting'};target.write_text(json.dumps(out))
 try:
  r=requests.post('https://nodyhub.com'+endpoint,headers={'Authorization':'Bearer '+key},json=body,timeout=(10,90));out['http']=r.status_code
  try:out['response']=r.json()
  except ValueError:out['response']={'raw':r.text[:250]}
 except Exception as e:out['observation_error_type']=type(e).__name__
 out['state']='attempt_recorded';target.write_text(json.dumps(out,ensure_ascii=False));print(json.dumps(out,ensure_ascii=False)[:2300],flush=True)
