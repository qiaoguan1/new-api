"""Three authorized single-image canaries; no retries and no media logging."""
import concurrent.futures,json,os,pathlib,requests,time
os.umask(0o077)
root=pathlib.Path('/opt/ai-api-stack/backups/nodyhub171-evidence');root.mkdir(mode=0o700,exist_ok=True)
key=pathlib.Path('/opt/ai-api-stack/secrets/upstreams/nodyhub-requested-20260923.key').read_text().strip()
def run(model):
 start=time.monotonic();out={'model':model,'started_at':int(time.time()),'size':'1024x1024'}
 try:
  r=requests.post('https://nodyhub.com/v1/images/generations',headers={'Authorization':'Bearer '+key},json={'model':model,'prompt':'A plain blue circle on a white background. No text.','size':'1024x1024','n':1,'response_format':'b64_json'},timeout=(10,180))
  out['http']=r.status_code
  try:d=r.json()
  except ValueError:d={}
  rows=d.get('data') or [];out['has_image']=bool(isinstance(rows,list) and rows and isinstance(rows[0],dict) and (rows[0].get('url') or rows[0].get('b64_json')))
  out['request_id']=r.headers.get('x-request-id')
  if not out['has_image']:out['error']=str(d.get('error') or d.get('message') or 'no image').replace(key,'[REDACTED]')[:200]
 except Exception as e:out['error_type']=type(e).__name__
 out['seconds']=round(time.monotonic()-start,2)
 (root/(model+'.json')).write_text(json.dumps(out,ensure_ascii=False))
 print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as p:list(p.map(run,['grok-imagine-image-2.0','gpt-image-2.5-flare','gpt-image-2.5-sunburst']))
