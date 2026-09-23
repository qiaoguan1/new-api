"""Restricted NodyHub image routes; only verified models/specifications are enabled."""
import base64,hmac,ipaddress,json,os,pathlib,threading,time,urllib.request,urllib.error,urllib.parse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from image_io import image_bytes

MODELS={'gpt-image-2.5-flare','gpt-image-2.5-sunburst'}
SIZES={'1024x1024'}
CONFIG={}
ACTIVE=threading.BoundedSemaphore(2)

class Rejection(Exception):
    def __init__(self,code,message,status=400):self.code,self.message,self.status=code,message,status

def validate(raw,provider):
    if not isinstance(raw,dict):raise Rejection('invalid_request','JSON object required')
    if set(raw)-{'model','prompt','size','n','response_format','quality'}:raise Rejection('unsupported_parameter','Only text-to-image with verified parameters is enabled')
    if not isinstance(raw.get('model'),str) or raw['model'] not in MODELS:raise Rejection('invalid_model','Unsupported or unverified model')
    if not isinstance(raw.get('prompt'),str) or not raw['prompt'].strip() or len(raw['prompt'])>32000:raise Rejection('invalid_prompt','A prompt of 1 to 32000 characters is required')
    if isinstance(raw.get('n',1),bool) or raw.get('n',1)!=1:raise Rejection('invalid_n','Only one image per request is enabled')
    size=raw.get('size','1024x1024')
    if not isinstance(size,str) or size not in SIZES:raise Rejection('invalid_size','This verified route accepts1024x1024 only')
    if raw.get('quality') not in (None,'auto'):raise Rejection('unsupported_quality','Only default auto quality has verified pricing')
    fmt=raw.get('response_format') or 'url'
    if fmt not in ('url','b64_json'):raise Rejection('invalid_response_format','Use url or b64_json')
    return {'model':raw['model'],'prompt':raw['prompt'],'size':size,'n':1,'response_format':fmt}

def response_payload(data,fmt):
    if isinstance(data,dict) and data.get('error'):raise Rejection('upstream_no_image','Upstream returned an error; not automatically replayed')
    rows=data.get('data') if isinstance(data,dict) else None
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):raise Rejection('upstream_no_image','Upstream returned no image; not automatically replayed')
    row={k:rows[0][k] for k in ('url','b64_json') if rows[0].get(k)}
    if not row:raise Rejection('upstream_no_image','Upstream returned no image; not automatically replayed')
    if 'url' in row:
        if not isinstance(row['url'],str):raise Rejection('invalid_result','Invalid image URL')
        parsed=urllib.parse.urlsplit(row['url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise Rejection('invalid_result','Invalid image URL')
        try:
            if not ipaddress.ip_address(parsed.hostname).is_global:raise Rejection('invalid_result','Private image URL rejected')
        except ValueError:pass
    if 'b64_json' in row:
        try:
            if not isinstance(row['b64_json'],str):raise ValueError()
            raw=base64.b64decode(row['b64_json'],validate=True)
            if not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')):raise ValueError()
        except Exception:raise Rejection('invalid_result','Invalid encoded image')
    if fmt=='b64_json' and not row.get('b64_json'):
        try:row={'b64_json':base64.b64encode(image_bytes(row['url'])).decode()}
        except Exception:raise Rejection('result_fetch_failed','Image was generated but transfer failed; do not automatically regenerate')
        return response_payload({'data':[row],**({'usage':data['usage']} if isinstance(data.get('usage'),dict) else {})},fmt)
    return {'created':int(time.time()),'data':[row],**({'usage':data['usage']} if isinstance(data.get('usage'),dict) else {})}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,data):
        body=json.dumps(data,separators=(',',':')).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def provider(self):
        parts=self.path.split('/')
        if len(parts)<3 or parts[1] not in CONFIG['providers']:raise Rejection('not_found','Unknown route',404)
        return parts[1],'/'+ '/'.join(parts[2:])
    def authenticate(self):
        if not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+CONFIG['adapter_token']):raise Rejection('unauthorized','Unauthorized',401)
    def do_GET(self):
        if self.path=='/health':return self.send(200,{'ok':True})
        try:
            self.authenticate();provider,path=self.provider()
            if path!='/v1/models':raise Rejection('not_found','Unknown endpoint',404)
            self.send(200,{'object':'list','data':[{'id':model,'object':'model','owned_by':'nodyhub-relay'} for model in sorted(MODELS)]})
        except Rejection as e:self.send(e.status,{'error':{'code':e.code,'message':e.message}})
    def do_POST(self):
        acquired=False
        try:
            self.authenticate();provider,path=self.provider()
            if path!='/v1/images/generations':raise Rejection('unsupported_endpoint','Only images/generations is validated',400)
            if not self.headers.get('Content-Type','').strip().lower().startswith('application/json'):
                raise Rejection('unsupported_content_type','JSON Content-Type is required for resolution billing',400)
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:raise Rejection('invalid_request','Invalid body length')
            if not 0<length<=65536:raise Rejection('request_too_large','Body must be at most64 KiB',413)
            self.connection.settimeout(15)
            try:raw=json.loads(self.rfile.read(length))
            except Exception:raise Rejection('invalid_json','Invalid JSON body')
            body=validate(raw,provider)
            acquired=ACTIVE.acquire(blocking=False)
            if not acquired:raise Rejection('busy','Generation concurrency limit reached',429)
            conf=CONFIG['providers'][provider]
            req=urllib.request.Request(conf['base_url'].rstrip('/')+'/v1/images/generations',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+conf['key'],'Content-Type':'application/json','User-Agent':'XingTuNodyHub/1'})
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
            try:
                with opener.open(req,timeout=180) as r:
                    content=r.read(32*1024*1024+1)
                if len(content)>32*1024*1024:raise ValueError('response too large')
                data=json.loads(content)
            except Exception:raise Rejection('upstream_outcome_unconfirmed','Upstream result unconfirmed; not automatically replayed')
            self.send(200,response_payload(data,body['response_format']))
        except Rejection as e:
            self.send(e.status,{'error':{'code':e.code,'message':e.message,'type':'nodyhub_adapter_error'}})
        except (BrokenPipeError,ConnectionResetError):pass
        finally:
            if acquired:ACTIVE.release()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('redirect forbidden')

if __name__=='__main__':
    CONFIG=json.loads(pathlib.Path('/run/secrets/config.json').read_text())
    ThreadingHTTPServer(('0.0.0.0',8097),Handler).serve_forever()
