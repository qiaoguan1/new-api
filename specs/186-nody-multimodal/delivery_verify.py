"""GET-only delivery verification of six already-created issue186 tasks.

Never submits a generation task or changes production configuration. Evidence
updates are restricted to the task-owned private JSON files below.
"""

import concurrent.futures,hashlib,json,os,pathlib,re,subprocess,sys,tempfile,time,traceback,urllib.parse,uuid
from decimal import Decimal
ROOT=pathlib.Path('/opt/ai-api-stack/backups/nody-multimodal-186-20261007')
CASES={
 'grok-imagine-video-official-1':'1d9ddf27-f609-47f9-ace6-59de4919df4f',
 'grok-imagine-video-official-2':'61b7ee8d-9e23-4a4e-88f6-2eb12a11ec9e',
 'grok-video-3-1':'cb151f28-bf24-4514-b4e8-1458355a7f57',
 'grok-video-3-2':'e9193fdc-c917-46c9-8f5b-e40c48eeb510',
 'grok-imagine-1.5-video-1':'2dbf7287-22d8-40b8-ab02-45b94d708efc',
 'grok-imagine-1.5-video-2':'f740174a-04af-415a-a8d3-64bfbdb14131',
}
sys.path.insert(0,'/opt/ai-api-stack/releases/issue186-nody-multimodal/source/ops/video-job-gateway')
from adapters import ProviderConfig,_observation
from nodyhub import NodyTransport,result_url
from reference_contract import ReferenceMediaVerifier
print(json.dumps({'stage':'readonly_delivery_verification_started'}),flush=True)
info=json.loads(subprocess.check_output(['docker','inspect','xtai-video-public-execution']))[0]
env=dict(x.split('=',1) for x in info['Config']['Env'])
hosts=tuple(x.strip().lower() for x in env['VIDEO_JOB_NODYHUB_RESULT_HOSTS'].split(',') if x.strip())
config=ProviderConfig('nodyhub',env['VIDEO_JOB_NODYHUB_BASE_URL'],env['VIDEO_JOB_NODYHUB_API_KEY'],hosts,poll_timeout_seconds=40)
transport=NodyTransport()

def probe_download(path, task_id):
 """Probe one bounded download without relying on host ffprobe."""
 name='xtai-issue186-delivery-'+uuid.uuid4().hex
 labels={'xtai.task.issue':'186','xtai.task.role':'delivery-proof','xtai.task.original-id':task_id}
 command=[
  'docker','run','--rm','--pull','never','--name',name,
  '--network','none','--read-only','--user','0:0','--cap-drop','ALL',
  '--security-opt','no-new-privileges','--pids-limit','64',
  '--memory','256m','--cpus','1',
  '--mount','type=bind,src='+str(path)+',dst=/data/result.mp4,readonly',
 ]
 for key,value in labels.items():command.extend(['--label',key+'='+value])
 command.extend([
  '--entrypoint','ffprobe','xtai/video-job-gateway:issue186',
  '-v','error','-protocol_whitelist','file,pipe','-show_entries',
  'format=format_name,duration:stream=codec_type,codec_name,width,height,sample_rate,channels',
  '-of','json','/data/result.mp4',
 ])
 try:
  checked=subprocess.run(command,capture_output=True,text=True,timeout=30,check=True)
  return json.loads(checked.stdout)
 finally:
  # A timed-out Docker client may leave its container running. Remove only the
  # uniquely named, label-verified sandbox created by this invocation.
  inspected=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=10)
  if inspected.returncode==0:
   rows=json.loads(inspected.stdout)
   owned=(len(rows)==1 and rows[0].get('Name')=='/'+name
          and all((rows[0].get('Config',{}).get('Labels')or{}).get(key)==value for key,value in labels.items()))
   if not owned:raise RuntimeError('sandbox_cleanup_identity_mismatch')
   subprocess.run(['docker','rm','--force',name],capture_output=True,text=True,timeout=10,check=True)
  else:
   absent=subprocess.run(['docker','ps','-a','--filter','name=^/'+name+'$','--format','{{.ID}}'],capture_output=True,text=True,timeout=10)
   if absent.returncode!=0 or absent.stdout.strip():raise RuntimeError('sandbox_cleanup_not_confirmed')

def atomic_update(path,fields):
 current=json.loads(path.read_text()); current.update(fields)
 if current.get('response',{}).get('id')!=fields['normalized_observation']['task_id']:raise ValueError('evidence_original_identity_changed')
 fd,temporary=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=ROOT)
 try:
  os.fchmod(fd,0o600)
  with os.fdopen(fd,'w') as out:
   json.dump(current,out,ensure_ascii=False,indent=2);out.flush();os.fsync(out.fileno())
  os.replace(temporary,path);os.chmod(path,0o600)
 finally:
  if os.path.exists(temporary):os.unlink(temporary)
def verify(path):
 value=json.loads(path.read_text()); task_id=value['response']['id']
 report={'case':path.stem,'id':task_id,'actual_cost_cny_exact':value.get('actual_cost_cny_exact')}
 try:
  if task_id!=CASES[path.stem]:raise ValueError('unexpected_original_task_identity')
  bills=value.get('billing_query',{}); items=(bills.get('data')or{}).get('items')or[]
  if bills.get('success') is not True or len(items)!=1 or items[0].get('task_id')!=task_id or items[0].get('status')!='SUCCESS':raise ValueError('authenticated_success_receipt_required')
  response=transport.request_json('GET',config.base_url.rstrip('/')+'/v1/videos/'+task_id,headers={'Authorization':'Bearer '+config.api_key},payload=None,timeout=40)
  if response.status!=200:raise ValueError('poll_http_'+str(response.status))
  payload=response.payload; normalized=dict(payload.get('data') if isinstance(payload.get('data'),dict) and payload['data'].get('status') else payload)
  normalized['id']=task_id; normalized['video_url']=result_url(payload)
  observation=_observation(normalized,fallback_task_id=task_id)
  if observation.status=='failed':raise ValueError('provider_poll_reports_terminal_failure')
  url=observation.result_url
  scalar=isinstance(payload.get('output'),str)
  if not url and scalar:url=payload['output'].strip()
  if not url:raise ValueError('result_url_missing')
  parsed=urllib.parse.urlsplit(url);host=(parsed.hostname or '').lower().rstrip('.')
  if parsed.scheme!='https' or not host or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443):raise ValueError('result_url_unsafe')
  if not any(host==h or host.endswith('.'+h) for h in hosts):raise ValueError('result_host_not_allowed')
  verifier=ReferenceMediaVerifier(hosts,timeout_seconds=45);addresses=verifier._public_dns_addresses(host,'delivery')
  deadline=time.monotonic()+45;maximum=64*1024*1024
  with tempfile.NamedTemporaryFile(prefix='xtai-issue186-delivery-',suffix='.mp4') as target:
   with verifier._open_pinned(url,host,addresses,{'Accept':'video/mp4,application/octet-stream','User-Agent':'xtai-issue186-delivery-proof'},'delivery',deadline=deadline) as media:
    declared=media.headers.get('Content-Length');mime=media.headers.get('Content-Type','').split(';')[0].strip().lower()
    http_status=media.status
    if mime not in ('video/mp4','application/octet-stream'):raise ValueError('result_content_type_not_mp4')
    if declared is not None and (int(declared)<=0 or int(declared)>maximum):raise ValueError('result_content_length_out_of_bound')
    digest=hashlib.sha256();total=0
    while True:
     if time.monotonic()>=deadline:raise TimeoutError('bounded_delivery_timeout')
     chunk=media.read(65536)
     if not chunk:break
     total+=len(chunk)
     if total>maximum:raise ValueError('result_body_out_of_bound')
     digest.update(chunk);target.write(chunk)
    if total<=0 or (declared is not None and total!=int(declared)):raise ValueError('result_content_length_mismatch')
   # Probe after closing the HTTP context so local probe errors cannot trigger
   # a second yield from the downloader's connection-retry context manager.
   target.flush();target.seek(0);magic=target.read(32)
   if len(magic)<12 or magic[4:8]!=b'ftyp':raise ValueError('result_mp4_magic_missing')
   probe=probe_download(target.name,task_id);streams=probe.get('streams')or[]
   video=[s for s in streams if s.get('codec_type')=='video' and s.get('codec_name') in ('h264','hevc')]
   audio=[s for s in streams if s.get('codec_type')=='audio']
   if not video or not audio:raise ValueError('result_video_or_audio_missing')
   duration=Decimal(str((probe.get('format')or{}).get('duration')or'0'))
   if abs(duration-Decimal(value['request']['duration']))>Decimal('0.2'):raise ValueError('result_duration_not_matching_tuple')
   formats=str((probe.get('format')or{}).get('format_name')or'').split(',')
   if 'mp4' not in formats and 'mov' not in formats:raise ValueError('result_container_not_mp4')
   fields={'last_poll':payload,'media_probe':probe,'delivery_check':{'checked_at':int(time.time()),'http_status':http_status,'content_type':mime,'bytes':total,'sha256':digest.hexdigest(),'audio_video':True,'source_host':host,'maximum_bytes':maximum,'mp4_magic':True,'duration_matches':True},'normalized_observation':{'status':'succeeded','task_id':task_id,'original_task_id':task_id,'source_output_type':type(payload.get('output')).__name__ if 'output'in payload else 'structured','derived_from':'authenticated_SUCCESS_receipt_and_MP4_delivery'}}
   atomic_update(path,fields)
   report.update(status='succeeded',output_type=fields['normalized_observation']['source_output_type'],source_host=host,http_status=http_status,content_type=mime,bytes=total,duration=str(duration),video=video,audio=audio)
 except Exception as error:
  code=getattr(error,'code','read_only_delivery_failed')
  if not isinstance(code,(str,int)) or not re.fullmatch('[a-z0-9_]{1,100}',str(code)):code='read_only_delivery_failed'
  frames=[{'file':pathlib.Path(frame.filename).name,'function':frame.name,'line':frame.lineno} for frame in traceback.extract_tb(error.__traceback__)[-8:]]
  causes=[];seen=set();cause=error
  while cause is not None and id(cause) not in seen and len(causes)<5:
   seen.add(id(cause));causes.append(type(cause).__name__);cause=cause.__cause__ or cause.__context__
  report.update(status='needs_processing',error_type=type(error).__name__,error_code=code,diagnostic_frames=frames,cause_types=causes)
 return report
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 futures=[pool.submit(verify,ROOT/(case+'.json')) for case in sorted(CASES)]
 for future in concurrent.futures.as_completed(futures):print(json.dumps(future.result(),ensure_ascii=False),flush=True)
