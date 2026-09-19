"""OpenAI generation bridge for Toonflow G-2.0's documented async protocol.

Only 1K single text-to-image requests are eligible at the current retail price.
Other shapes are rejected BEFORE submission with429 for safe channel fallback.
After submission no second upstream generation is attempted.
"""
import base64,hashlib,hmac,json,math,os,re,sqlite3,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import urllib.request,urllib.error
from image_io import image_bytes

BASE='https://api.toonflow.net/v1'
MODEL='全能图片G-2.0'
class Failure(Exception):
    def __init__(self,status,code):self.status=status;self.code=code

def translate(raw):
    if not isinstance(raw,dict) or raw.get('model')!='gpt-image-2':raise Failure(429,'unsupported_model_no_submit')
    if raw.get('n',1)!=1 or isinstance(raw.get('n'),bool):raise Failure(429,'unsupported_count_no_submit')
    if any(raw.get(k) for k in ['image','images','mask','reference_images']):raise Failure(429,'reference_requires_other_route')
    if raw.get('stream') or raw.get('extra_body') or raw.get('metadata'):
        raise Failure(429,'extended_options_require_other_route')
    if raw.get('quality') not in (None,'auto','standard') or raw.get('output_format'):
        raise Failure(429,'quality_requires_other_route')
    fmt=raw.get('response_format') or 'url'
    if fmt not in ('url','b64_json'):raise Failure(400,'invalid_response_format')
    prompt=raw.get('prompt')
    if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>32000:raise Failure(400,'invalid_prompt')
    # Do not downgrade a caller's explicit2K/4K request to the cheaper1K tier.
    for field in ['resolution','image_size']:
        if raw.get(field) and str(raw[field]).lower()!='1k':raise Failure(429,'resolution_requires_other_route')
    size=str(raw.get('size') or '1024x1024').lower()
    if size in ('1k','auto'):w=h=1024
    else:
        match=re.fullmatch(r'(\d{3,4})x(\d{3,4})',size)
        if not match:raise Failure(429,'size_requires_other_route')
        w,h=map(int,match.groups())
    if min(w,h)<256 or max(w,h)>1536:raise Failure(429,'resolution_requires_other_route')
    g=math.gcd(w,h);ratio=f'{w//g}:{h//g}'
    return {'model':MODEL,'prompt':prompt,'size':'1k','metadata':{'aspectRatio':ratio}},fmt

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def api(path,body,key,timeout=20):
    request=urllib.request.Request(BASE+path,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.build_opener(NoRedirect()).open(request,timeout=timeout) as response:
        data=response.read(4*1024*1024+1)
        if len(data)>4*1024*1024:raise Failure(400,'upstream_response_too_large')
        return json.loads(data)

def run_generation(body,key,record,call=api,sleeper=time.sleep):
    try:response=call('/image/generateImage',body,key)
    except Exception:
        record('submit_uncertain',None)
        raise Failure(400,'submit_uncertain_do_not_resubmit')
    task=response.get('data')
    if response.get('code')!=200 or not isinstance(task,str) or not task:
        if response.get('code') in (400,401,403,404,429) and not task:
            record('rejected',None)
            raise Failure(429,'upstream_rejected_no_task')
        record('submit_uncertain',None)
        raise Failure(400,'submit_uncertain_do_not_resubmit')
    record('submitted',task)
    deadline=time.monotonic()+140
    while time.monotonic()<deadline:
        try:payload=call('/image/getImageStatus',{'taskICode':task},key); data=payload.get('data') or {}
        except Exception:sleeper(3);continue
        if not isinstance(data,dict):sleeper(3);continue
        status=data.get('status') or payload.get('status')
        if status=='failed':record('failed',task);raise Failure(400,'generation_failed_do_not_resubmit')
        if status=='success':
            result=data.get('data')
            if isinstance(result,str) and result.startswith('https://'):
                record('success',task);return result
            record('result_invalid',task);raise Failure(400,'result_invalid_do_not_resubmit')
        sleeper(3)
    record('pending',task);raise Failure(400,'generation_pending_do_not_resubmit')

def main():
    token=Path('/run/secrets/adapter').read_text().strip();key=Path('/run/secrets/upstream').read_text().strip()
    db=Path('/data/tasks.sqlite3')
    with sqlite3.connect(db) as c:c.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,state TEXT,task TEXT,updated INTEGER)')
    gate=threading.BoundedSemaphore(3)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,payload):
            body=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path in ('/health','/ready'):return self.send(200,{'ok':True,'scope':'gpt-image-2 1K text generation'})
            if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):return self.send(401,{'error':{'code':'unauthorized'}})
            if self.path=='/v1/models':return self.send(200,{'object':'list','data':[{'id':'gpt-image-2','object':'model','owned_by':'xingtu'}]})
            self.send(404,{'error':{'code':'not_found'}})
        def do_POST(self):
            acquired=False;request_id=uuid.uuid4().hex
            def record(state,task):
                with sqlite3.connect(db,timeout=5) as c:c.execute('INSERT OR REPLACE INTO tasks VALUES(?,?,?,?)',(request_id,state,task,int(time.time())))
            try:
                if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):raise Failure(401,'unauthorized')
                if self.path!='/v1/images/generations':raise Failure(429,'endpoint_requires_other_route')
                try:length=int(self.headers.get('Content-Length','0'))
                except ValueError:raise Failure(400,'invalid_body')
                if not 0<length<=65536:raise Failure(400,'invalid_body')
                raw=json.loads(self.rfile.read(length));body,fmt=translate(raw)
                acquired=gate.acquire(blocking=False)
                if not acquired:raise Failure(429,'busy_no_submit')
                record('starting',None)
                url=run_generation(body,key,record)
                if fmt=='b64_json':
                    try:item={'b64_json':base64.b64encode(image_bytes(url)).decode()}
                    except Exception:raise Failure(400,'delivery_failed_do_not_resubmit')
                else:item={'url':url}
                self.send(200,{'created':int(time.time()),'data':[item]})
            except Failure as e:self.send(e.status,{'error':{'type':'adapter_error','code':e.code,'message':e.code}})
            except Exception:self.send(400,{'error':{'code':'internal_error_do_not_resubmit'}})
            finally:
                if acquired:gate.release()
    server=ThreadingHTTPServer(('0.0.0.0',8094),Handler);server.daemon_threads=True;server.serve_forever()
if __name__=='__main__':main()
