"""Read only: poll already-created tasks, validate media headers, save evidence."""
import ipaddress,json,pathlib,requests,socket,time,urllib.parse
ROOT=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence')
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-test-20260923.key').read_text().strip()
files=[ROOT/'omni-flash-4sec-test.json',ROOT/'flux-3-video-v2-test.json']
def result_url(x):
 for name in ['video_url','url']:
  if isinstance(x.get(name),str):return x[name]
 for container in ['metadata','video']:
  y=x.get(container) or {}
  if isinstance(y,dict):
   for name in ['video_url','url']:
    if isinstance(y.get(name),str):return y[name]
 rows=(x.get('result') or {}).get('videos') or []
 if rows:
  url=rows[0].get('url');return url[0] if isinstance(url,list) and url else url
 return None
seen={};deadline=time.monotonic()+600
while time.monotonic()<deadline:
 done=True
 for p in files:
  d=json.loads(p.read_text());tid=d.get('response',{}).get('id');x=d.get('last_poll',{})
  if not tid:continue
  if x.get('status') not in ['completed','failed','cancelled','expired']:
   try:
    r=requests.get('https://nodyhub.com/v1/videos/'+tid,headers={'Authorization':'Bearer '+key},timeout=15);x=r.json();d['last_poll']=x;d['last_polled_at']=int(time.time());p.write_text(json.dumps(d,ensure_ascii=False))
   except Exception as e:print(json.dumps({'model':d['model'],'poll_error_type':type(e).__name__}),flush=True);done=False;continue
  state=x.get('status');url=result_url(x)
  row={'model':d['model'],'task_id':tid,'status':state,'progress':x.get('progress'),'result_url_present':bool(url),'error':x.get('error'),'finished_at':x.get('completed_at',x.get('completed'))}
  if state=='completed' and url and not d.get('media_check'):
   try:
    parsed=urllib.parse.urlsplit(url);assert parsed.scheme=='https' and parsed.hostname and not parsed.username
    assert all(ipaddress.ip_address(a[4][0]).is_global for a in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM))
    r=requests.head(url,timeout=15,allow_redirects=False);d['media_check']={'http':r.status_code,'content_type':r.headers.get('Content-Type'),'length':r.headers.get('Content-Length')};p.write_text(json.dumps(d,ensure_ascii=False))
   except Exception as e:row['media_error_type']=type(e).__name__
  row['media_check']=d.get('media_check')
  if seen.get(tid)!=row:print(json.dumps(row,ensure_ascii=False),flush=True);seen[tid]=row
  if state not in ['completed','failed','cancelled','expired']:done=False
 if done:break
 time.sleep(15)
else:print(json.dumps({'observation':'still_pending_at_deadline','no_resubmit':True}),flush=True)
