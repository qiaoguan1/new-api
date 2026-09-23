import unittest,base64,http.client,threading
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
  for body in [{'error':{'message':'failure'}},{'data':[{'b64_json':'notvalid'}]},{'data':[{'url':'http://localhost/a'}]}]:
   with self.assertRaises(app.Rejection):app.response_payload(body,'b64_json')
 def test_auth_and_content_type_before_upstream(self):
  with patch.object(app,'CONFIG',{'adapter_token':'test','providers':{'nodyhub':{}}}),patch('app.urllib.request.build_opener') as upstream:
   server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
   try:
    for auth,ctype,expected in [('wrong','application/json',401),('Bearer test','multipart/form-data',400)]:
     c=http.client.HTTPConnection('127.0.0.1',server.server_port);c.request('POST','/nodyhub/v1/images/generations',body='{}',headers={'Authorization':auth,'Content-Type':ctype});r=c.getresponse();self.assertEqual(r.status,expected);r.read();c.close()
    upstream.assert_not_called()
   finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
