import json,pathlib,sys,unittest,tempfile
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch,Mock
from decimal import Decimal
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nodyhub import NodyHubAdapter,NODY_MODELS,verified_quote
from adapters import ProviderConfig,JsonResponse,TransportFailure,AdapterError
from billing_collectors import NewAPITaskBillingCollector,BillingCollectionError
from relay_pricing import RelayPricing
from store import _reservation_from_payload,build_settlement_evidence
from app import Config,Gateway,GatewayError

ID='f38d6547-27af-4f3b-8945-8c643162c432'
class Fake:
 def __init__(self,payload):self.payload=payload;self.calls=[]
 def request_json(self,method,url,**kwargs):self.calls.append((method,url,kwargs));return JsonResponse(200,{},self.payload,'')
class Tests(unittest.TestCase):
 def config(self):return ProviderConfig('nodyhub','https://nodyhub.com','test',('getapib.org',))
 def payload(self,model):
  r,d,_=NODY_MODELS[model];return dict(model=model,prompt='blue ball',duration=d,resolution=r,aspect_ratio='16:9',mode='text',generate_audio=True)
 def test_all_routes_use_verified_fields(self):
  for model in NODY_MODELS:
   transport=Fake({'id':ID,'status':'queued'});a=NodyHubAdapter(self.config(),transport);a.submit('test',model,self.payload(model))
   _,url,kw=transport.calls[0]
   self.assertTrue(url.endswith('/v1/videos' if model=='grok-video-3' else '/v2/videos/generations'))
   self.assertEqual(kw['payload']['model'],model)
 def test_original_uuid_survives_provider_id_change(self):
  for data in [{'result':{'videos':[{'url':['https://getapib.org/a.mp4']}]}},{'metadata':{'video_url':'https://getapib.org/a.mp4'}},{'video_url':'https://getapib.org/a.mp4'}]:
   a=NodyHubAdapter(self.config(),Fake({'id':'task_raw','status':'completed',**data}));out=a.poll(ID)
   self.assertEqual(out.upstream_task_id,ID);self.assertEqual(out.status,'succeeded');self.assertTrue(out.result_url)
 def test_bad_spec_never_submits(self):
  f=Fake({});a=NodyHubAdapter(self.config(),f);p=self.payload('omni-flash');p['duration']=10
  with self.assertRaises(AdapterError):a.submit('test','omni-flash',p)
  self.assertEqual(f.calls,[])
 def test_nody_text_generate_task_billing(self):
  c=NewAPITaskBillingCollector('nodyhub','https://nodyhub.com/api/task/self',rate_cny_per_usd='1.5')
  row={'task_id':ID,'action':'textGenerate','status':'SUCCESS','quota':850000,'finish_time':1790170226}
  result=c._parse_newapi_record({'success':True,'data':{'total':1,'items':[row]}},ID)
  self.assertEqual(result.actual_cost_cny_exact,'2.550000')
 def test_quote_is_not_ark(self):
  q=verified_quote('omni-flash','720p',4);self.assertEqual(q['amount_cny_exact'],'3.825000');self.assertNotIn('official_cost_cny_exact',q)
  p={'_billing_v2':True,'_billing_contract_version':'xtai-video-billing-v2.2','model':'omni-flash','resolution':'720p','duration':4,'_relay_price':q}
  r=_reservation_from_payload(json.dumps(p));self.assertEqual(r['status'],'reserved')
 def test_old_and_new_prices_coexist(self):
  p=RelayPricing(ROOT/'relay-pricing.json');rows=p.official_snapshot([('seedance-2.0','720p'),('omni-flash','720p')])['models']
  self.assertEqual(len(rows),2)
 def test_existing_production_models_are_unchanged(self):
  before=json.loads((ROOT/'tests/fixtures/production-before-nody.json').read_text(encoding='utf-8'))
  after=json.loads((ROOT/'catalog.json').read_text(encoding='utf-8'))
  self.assertEqual(after['models'][:len(before['models'])],before['models'])
  self.assertEqual(len(after['models'])-len(before['models']),7)
 def test_v22_text_reservation_and_idempotent_actual_settlement(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
   adapter=NodyHubAdapter(self.config(),Fake({}))
   config=Config(token='test',data_dir=pathlib.Path(directory),catalog_file=ROOT/'catalog.json',providers={'nodyhub':self.config()},pricing_file=ROOT/'relay-pricing.json',public_base_url='https://api.aixingtuyun.com',v21_approved_providers=frozenset({'nodyhub'}))
   g=Gateway(config,adapters={'nodyhub':adapter},billing_collectors={'nodyhub':SimpleNamespace(ready=True)},start_monitor=False)
   g.start_submit=lambda _:None
   body={'provider_id':'video-aixingtu-api','request_id':'nody-test-request','model':'grok-imagine-video-official','resolution':'480p','duration':1,'aspect_ratio':'16:9','generate_audio':True,'prompt':'blue ball'}
   snapshot,reused=g.submit_v22(body,idempotency_key=body['request_id'])
   self.assertFalse(reused);job=snapshot['job_id']
   self.assertEqual(snapshot['billing']['reserved_amount'],'0.675000')
   self.assertEqual(snapshot['billing']['reserve_basis'],'verified_upstream_1_5')
   self.assertEqual(snapshot['billing']['contract_version'],'xtai-video-billing-v2.2')
   self.assertTrue(g.submit_v22(body,idempotency_key=body['request_id'])[1])
   g.store.claim_submit(job);g.store.mark_running(job,ID,'queued',5)
   g.store.finish(job,'succeeded',result={'type':'url','source_url':'https://getapib.org/a.mp4'},upstream_task_id=ID,upstream_status='completed')
   self.assertEqual(g.store.get(job_id=job)['result_delivery'],'pending_settlement')
   evidence=build_settlement_evidence(job_id=job,revision=1,provider_task_id=ID,actual_cost_status='actual',actual_cost_cny_exact='0.300000',evidence_source='nodyhub_authenticated_video_task',evidence_id='unique-row',observed_at=datetime.now().astimezone().isoformat(),contract_version='xtai-video-billing-v2.2')
   settled,again=g.store.apply_settlement(evidence)
   self.assertEqual(settled['billing']['charged_amount'],'0.450000');self.assertEqual(settled['billing']['refund_amount'],'0.225000');self.assertEqual(settled['result_delivery'],'ready')
   self.assertTrue(g.store.apply_settlement(evidence)[1])
   prices=g.video_prices();self.assertEqual(len(prices['pricing']['models']),7)
   self.assertNotIn('reference_cost',json.dumps(prices))
   caps=g.capabilities('xtai-video-billing-v2.2')['capabilities']['video']['models']
   new=[x for x in caps if x['id'] in NODY_MODELS]
   self.assertEqual(len(new),7)
   self.assertTrue(all(x['available'] and not x['reference_video']['supported'] and x['generate_audio_required'] for x in new))
   body['request_id']='nody-reference-denied';body['images']=['https://example.com/a.png']
   with self.assertRaises(GatewayError):g.submit_v22(body,idempotency_key=body['request_id'])
 def test_failed_task_requires_explicit_matching_refund(self):
  c=NewAPITaskBillingCollector('nodyhub','https://nodyhub.com/api/task/self',authorization='test',rate_cny_per_usd='1.5')
  task={'success':True,'data':{'total':1,'items':[{'task_id':ID,'status':'FAILURE','quota':50000,'submit_time':100,'finish_time':105}]}}
  logs={'success':True,'data':{'total':1,'items':[{'id':1,'type':6,'quota':50000,'created_at':106,'content':'refund '+ID}]}}
  with patch.object(c,'_request_json',side_effect=[task,logs]):self.assertEqual(c.collect_failed(ID).actual_cost_cny_exact,'0.000000')
 def test_expired_recovery_never_assumes_zero_cost(self):
  for task_id in ['',ID]:
   with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
    config=Config(token='test',data_dir=pathlib.Path(directory),catalog_file=ROOT/'catalog.json',providers={'nodyhub':self.config()},pricing_file=ROOT/'relay-pricing.json',public_base_url='https://api.aixingtuyun.com',v21_approved_providers=frozenset({'nodyhub'}))
    collector=SimpleNamespace(ready=True,collect_failed=Mock(side_effect=BillingCollectionError('refund_missing')))
    g=Gateway(config,adapters={'nodyhub':NodyHubAdapter(self.config(),Fake({}))},billing_collectors={'nodyhub':collector},start_monitor=False)
    g.start_submit=Mock()
    body={'provider_id':'video-aixingtu-api','request_id':'recovery-test','model':'grok-imagine-video-official','resolution':'480p','duration':1,'aspect_ratio':'16:9','generate_audio':True,'prompt':'blue ball'}
    snapshot,_=g.submit_v22(body,idempotency_key=body['request_id']);job=snapshot['job_id']
    g.store.claim_submit(job)
    g.store.begin_recovery(job,error={'code':'test'},upstream_task_id=task_id)
    with g.store.connect() as db:db.execute('update video_jobs set recovery_deadline_at=1 where job_id=?',(job,))
    g.start_submit.reset_mock();g._recover_one({'job_id':job})
    out=g.store.get(job_id=job)
    self.assertEqual(out['billing']['status'],'pending_review')
    self.assertNotEqual(out['billing'].get('charged_amount'),'0.000000')
    g.start_submit.assert_not_called()
 def test_unverified_duration_quote_rejected(self):
  for value in [1.5,True,2,'01']:
   with self.assertRaises(ValueError):verified_quote('grok-imagine-video-official','480p',value)
 def test_refund_on_second_ledger_page(self):
  c=NewAPITaskBillingCollector('nodyhub','https://nodyhub.com/api/task/self',authorization='test',rate_cny_per_usd='1.5')
  task={'success':True,'data':{'total':1,'items':[{'task_id':ID,'status':'FAILURE','quota':50000,'submit_time':100,'finish_time':105}]}}
  page1={'success':True,'data':{'total':101,'items':[{'id':i,'type':2} for i in range(1,101)]}}
  page2={'success':True,'data':{'total':101,'items':[{'id':101,'type':6,'quota':50000,'created_at':106,'content':'refund '+ID}]}}
  with patch.object(c,'_request_json',side_effect=[task,page1,page2]) as request:
   self.assertEqual(c.collect_failed(ID).actual_cost_cny_exact,'0.000000')
   self.assertEqual(request.call_args_list[2].args[1]['p'],2)
  for broken in [{'success':True,'data':{'total':'bad','items':[]}}, {'success':True,'data':{'total':1,'items':['bad']}}]:
   with patch.object(c,'_request_json',side_effect=[task,broken]):
    with self.assertRaises(BillingCollectionError):c.collect_failed(ID)
if __name__=='__main__':unittest.main()
