import unittest,base64,http.client,threading,json,io,urllib.error
from unittest.mock import patch
import app
class Tests(unittest.TestCase):
 def body(self,**kw):return {'model':'gpt-image-2.5-flare','prompt':'test',**kw}
 def test_verified_models(self):
  for model in app.MODELS:self.assertEqual(app.validate(self.body(model=model),'nodyhub')['model'],model)
 def test_unverified_specs_rejected(self):
  for kw in [{'size':'2048x2048'},{'size':'4K'},{'size':[]},{'n':2},{'n':True},{'quality':'high'},{'image':'url'},{'model':'grok-imagine-image-2.0'},{'model':'wan3.0-video'}]:
   with self.assertRaises(app.Rejection):app.validate(self.body(**kw),'nodyhub')
 def test_errors_not_billed_as_success(self):
  for body in [{'error':{'message':'failure'}},{'data':[{'b64_json':'notvalid'}]},{'data':[{'url':'http://localhost/a'}]},{'data':[]},{'data':[{'url':'https://['}]}]:
   with self.subTest(body=body),self.assertRaises(app.Rejection) as raised:app.response_payload(body,'b64_json')
   self.assertEqual((raised.exception.status,raised.exception.submission_state),(502,'uncertain'))
 def test_auth_and_content_type_before_upstream(self):
  with patch.object(app,'CONFIG',{'adapter_token':'test','providers':{'nodyhub':{}}}),patch('app.urllib.request.build_opener') as upstream:
   server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    for auth,ctype,expected in [('wrong','application/json',401),('Bearer test','multipart/form-data',400)]:
     c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/nodyhub/v1/images/generations',body='{}',headers={'Authorization':auth,'Content-Type':ctype});r=c.getresponse();self.assertEqual(r.status,expected);r.read();c.close()
    upstream.assert_not_called()
   finally:server.shutdown();server.server_close();thread.join()

 def request(self,path,body=None,*,method='POST',headers=None,include_headers=False):
  """Exercise the real adapter handler with local-only fixture credentials."""
  server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
  thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   client=http.client.HTTPConnection('127.0.0.1',server.server_port)
   client.request(method,path,body=json.dumps(body) if body is not None else None,headers={'Authorization':'Bearer test','Content-Type':'application/json',**(headers or {})})
   response=client.getresponse()
   value=(response.status,response.getheader('X-XingTu-Image-Submission-State'),json.loads(response.read()))
   if include_headers:value=(*value,dict(response.getheaders()))
   client.close()
   return value
  finally:server.shutdown();server.server_close();thread.join()

 def config(self):
  return patch.object(app,'CONFIG',{'adapter_token':'test','providers':{'nodyhub':{'base_url':'https://upstream.invalid','key':'fixture-only'}}})

 def test_uncertain_generation_outcomes_are_502_not_input_errors(self):
  for error in [TimeoutError('timeout'),urllib.error.URLError('connection reset'),urllib.error.HTTPError('https://upstream.invalid',503,'unavailable',{},io.BytesIO(b'unknown'))]:
   with self.subTest(error=type(error).__name__),self.config(),patch('app.urllib.request.build_opener') as upstream:
    upstream.return_value.open.side_effect=error
    status,state,data=self.request('/nodyhub/v1/images/generations',self.body())
    self.assertEqual(status,502)
    self.assertEqual(state,'uncertain')
    self.assertEqual(data['error']['code'],'upstream_outcome_unconfirmed')
    self.assertEqual(upstream.return_value.open.call_count,1)

 def test_local_spec_rejections_are_not_submitted_or_silently_converted(self):
  cases=[('/nodyhub/v1/images/edits',self.body(),'endpoint_requires_other_route'),('/nodyhub/v1/images/generations',self.body(size='2048x2048'),'resolution_requires_other_route')]
  for path,body,marker in cases:
   with self.subTest(marker=marker),self.config(),patch('app.urllib.request.build_opener') as upstream:
    status,state,data=self.request(path,body)
    self.assertEqual((status,state),(400,'not_submitted'))
    self.assertIn(marker,data['error']['message'])
    upstream.assert_not_called()

 def test_generated_result_transfer_failure_stays_uncertain(self):
  with self.config(),patch('app.urllib.request.build_opener') as upstream,patch('app.image_bytes',side_effect=TimeoutError('fetch')):
   response=upstream.return_value.open.return_value.__enter__.return_value
   response.read.return_value=b'{"data":[{"url":"https://example.invalid/image.png"}]}'
   status,state,data=self.request('/nodyhub/v1/images/generations',self.body(response_format='b64_json'))
   self.assertEqual((status,state),(502,'uncertain'))
   self.assertEqual(data['error']['code'],'result_fetch_failed')
   self.assertEqual(upstream.return_value.open.call_count,1)

 def test_model_catalog_advertises_only_verified_generation_spec(self):
  with self.config():
   status,_,data=self.request('/nodyhub/v1/models',method='GET')
   self.assertEqual(status,200)
   self.assertEqual({row['id'] for row in data['data']},app.MODELS)
   for row in data['data']:
    capability=row['image_capabilities']
    self.assertEqual(capability['endpoints'],['/v1/images/generations'])
    self.assertEqual(capability['sizes'],['1024x1024'])
    self.assertEqual(capability['image_count'],[1])
    self.assertFalse(capability['image_edit'])
    self.assertEqual(capability['verification_status'],'verified')

 def test_bad_or_empty_generation_body_is_never_replayed(self):
  for content in [b'not JSON',b'{"data":[]}',b'{"error":{"message":"unknown rejection"}}']:
   with self.subTest(content=content),self.config(),patch('app.urllib.request.build_opener') as upstream:
    upstream.return_value.open.return_value.__enter__.return_value.read.return_value=content
    status,state,data=self.request('/nodyhub/v1/images/generations',self.body())
    self.assertEqual((status,state),(502,'uncertain'))
    self.assertEqual(upstream.return_value.open.call_count,1)

 def test_verified_success_does_not_change_requested_model_or_size(self):
  with self.config(),patch('app.urllib.request.build_opener') as upstream:
   encoded=base64.b64encode(b'\x89PNG\r\n\x1a\nfixture-only').decode()
   upstream.return_value.open.return_value.__enter__.return_value.read.return_value=json.dumps({'data':[{'b64_json':encoded}]}).encode()
   status,state,data=self.request('/nodyhub/v1/images/generations',self.body(response_format='b64_json',size='1024x1024',n=1))
   self.assertEqual((status,state),(200,'submitted'))
   self.assertEqual(data['data'],[{'b64_json':encoded}])
   forwarded=json.loads(upstream.return_value.open.call_args.args[0].data)
   self.assertEqual((forwarded['model'],forwarded['size'],forwarded['n']),('gpt-image-2.5-flare','1024x1024',1))

 def test_local_busy_rejection_proves_no_upstream_submission(self):
  with self.config(),patch.object(app,'ACTIVE') as active,patch.object(app.urllib.request,'build_opener') as upstream:
   active.acquire.return_value=False
   status,state,data=self.request('/nodyhub/v1/images/generations',self.body())
   self.assertEqual((status,state),(429,'not_submitted'))
   self.assertIn('upstream_rejected_no_task',data['error']['message'])
   upstream.assert_not_called()
   active.release.assert_not_called()

 def test_stable_request_id_and_returned_upstream_id_preserve_actual_join(self):
  with self.config(),patch.object(app.urllib.request,'build_opener') as upstream:
   response=upstream.return_value.open.return_value.__enter__.return_value
   response.headers={'X-Oneapi-Request-Id':'provider-exact-123','Authorization':'Bearer never-forward'}
   response.read.return_value=b'{"data":[{"url":"https://example.invalid/image.png"}]}'
   status,state,_,headers=self.request('/nodyhub/v1/images/generations',self.body(),headers={'X-Request-ID':'ops190-stable-abc'},include_headers=True)
   self.assertEqual((status,state),(200,'submitted'))
   self.assertEqual(headers.get('X-Oneapi-Request-Id'),'provider-exact-123')
   self.assertNotIn('Authorization',headers)
   request=upstream.return_value.open.call_args.args[0]
   self.assertEqual(request.get_header('X-request-id'),'ops190-stable-abc')
   self.assertEqual(request.get_header('Authorization'),'Bearer fixture-only')

 def test_known_provider_id_survives_error_or_bad_result_without_retry(self):
  error=urllib.error.HTTPError('https://upstream.invalid',503,'unavailable',{'X-Oneapi-Request-Id':'original-provider-id'},io.BytesIO(b'unknown'))
  with self.config(),patch.object(app.urllib.request,'build_opener') as upstream:
   upstream.return_value.open.side_effect=error
   status,state,_,headers=self.request('/nodyhub/v1/images/generations',self.body(),include_headers=True)
   self.assertEqual((status,state),(502,'uncertain'))
   self.assertEqual(headers.get('X-Oneapi-Request-Id'),'original-provider-id')
   self.assertEqual(upstream.return_value.open.call_count,1)

 def test_request_ids_are_strict_and_not_credentials_or_headers(self):
  for value in ['with space','a'*129,'request\r\nAuthorization: secret','sk-accidental-key',123,None]:
   self.assertEqual(app.valid_request_id(value),'')
  self.assertEqual(app.valid_request_id('ops190.a-b_1:2'),'ops190.a-b_1:2')
if __name__=='__main__':unittest.main()
