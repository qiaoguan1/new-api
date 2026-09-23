"""One advertised chat-video request. Capture state; never retry a submitted job."""
import json,os,pathlib,re,requests,time
os.umask(0o077)
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
root=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence')
body={'model':'grok-video-3','messages':[{'role':'user','content':'Generate a short video: a small blue ball slowly rolls on a plain white table. Duration5 seconds,480p.'}],'stream':True,'duration':5,'resolution':'480p'}
start=time.monotonic();events=[];text='';out={'model':body['model'],'requested_duration':5,'requested_resolution':'480p','started_at':int(time.time())}
try:
 with requests.post('https://nodyhub.com/v1/chat/completions',headers={'Authorization':'Bearer '+key},json=body,stream=True,timeout=(10,150)) as r:
  out['http']=r.status_code;out['content_type']=r.headers.get('Content-Type');out['request_id']=r.headers.get('x-request-id')
  if 'text/event-stream' in r.headers.get('Content-Type',''):
   for line in r.iter_lines(chunk_size=1):
    if time.monotonic()-start>210:raise TimeoutError('bounded observation expired')
    if not line.startswith(b'data:'):continue
    v=line[5:].strip()
    if v==b'[DONE]':out['done']=True;break
    try:d=json.loads(v)
    except ValueError:continue
    events.append(d)
    if d.get('error'):out['error']=d['error']
    for choice in d.get('choices') or []:text+=str((choice.get('delta') or {}).get('content') or '')
    if len(text)>100000:raise ValueError('response text exceeded bound')
  else:
   d=r.json();events.append(d);out['error']=d.get('error');out['response_keys']=list(d)
except Exception as e:out['observation_error_type']=type(e).__name__
out['seconds']=round(time.monotonic()-start,2);out['has_https_result']=bool(re.search(r'https://',text));out['task_ids']=re.findall(r'task_[A-Za-z0-9_-]+',text)
(root/'grok-video3-chat-test.json').write_text(json.dumps({'summary':out,'events':events,'text':text},ensure_ascii=False))
print(json.dumps(out,ensure_ascii=False),flush=True)
