"""Bounded operator-authorized upstream tests; never print secrets or image bodies."""
import base64,concurrent.futures,datetime,json,pathlib,struct,subprocess,time
import requests

osdir=pathlib.Path('/opt/ai-api-stack/backups/image25-20260921')
osdir.mkdir(mode=0o700,exist_ok=True)
q='SELECT json_agg(c) FROM (SELECT id,key,base_url FROM channels WHERE id IN(6,45,49))c;'
lookup={x['id']:x for x in json.loads(subprocess.check_output(['docker','exec','ai-api-stack-postgres-1','psql','-U','newapi','-d','new-api','-XAtc',q],text=True))}

def run(case):
    cid,model,size=case;c=lookup[cid];start=time.monotonic();ts=int(time.time())
    output={'channel':cid,'model':model,'size':size,'started_at':ts}
    try:
        r=requests.post(c['base_url'].rstrip('/')+'/v1/images/generations',headers={'Authorization':'Bearer '+c['key']},json={'model':model,'prompt':'A plain blue circle centered on a white background. No text.','size':size,'n':1,'response_format':'b64_json'},timeout=(10,210))
        output['http']=r.status_code
        try:d=r.json()
        except ValueError:d={}
        rows=d.get('data') or [];output['image_count']=len(rows) if isinstance(rows,list) else 0
        output['has_image']=bool(isinstance(rows,list) and rows and (rows[0].get('b64_json') or rows[0].get('url')))
        output['request_id']=r.headers.get('x-request-id')
        if isinstance(rows,list) and rows and rows[0].get('b64_json'):
            raw=base64.b64decode(rows[0]['b64_json'])
            output['image_bytes']=len(raw)
            if raw.startswith(b'\x89PNG\r\n\x1a\n'):output['pixel_dimensions']=list(struct.unpack('>II',raw[16:24]))
            try:
                import io
                from PIL import Image
                output['pixel_dimensions']=list(Image.open(io.BytesIO(raw)).size)
            except ImportError:pass
        if not output['has_image']:
            e=d.get('error') or d.get('message') or {}
            output['error']=str(e).replace(c['key'],'[REDACTED]')[:250]
        output['usage']=d.get('usage')
    except Exception as e:output['error_type']=type(e).__name__
    output['seconds']=round(time.monotonic()-start,2)
    (osdir/(str(cid)+'-'+model+'-'+size+'.json')).write_text(json.dumps(output,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False),flush=True)
    return output

if __name__=='__main__':
    cases=[(45,'gpt-image-2.5','1024x1024'),(49,'gpt-image-2.5','1024x1024'),(6,'gpt-image-2.5-flare','1024x1024'),(6,'gpt-image-2.5-sunburst','1024x1024')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(run,cases))
