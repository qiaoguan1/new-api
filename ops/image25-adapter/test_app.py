import unittest,base64,http.client,threading
import app
from unittest.mock import patch
from app import validate,Rejection,response_payload

class Tests(unittest.TestCase):
    def body(self,**kw):return {'model':'gpt-image-2.5','prompt':'blue circle',**kw}
    def test_three_hanhe_tiers(self):
        for size in ['1024x1024','2048x2048','4096x4096']:self.assertEqual(validate(self.body(size=size),'hanhe')['size'],size)
    def test_rolldek_only_1k(self):
        self.assertEqual(validate(self.body(),'rolldek')['size'],'1024x1024')
        with self.assertRaises(Rejection) as ctx:validate(self.body(size='4096x4096'),'rolldek')
        self.assertEqual(ctx.exception.status,429)
    def test_reject_unpriced_inputs(self):
        for kwargs in [{'size':'auto'},{'size':'4K'},{'n':2},{'n':True},{'quality':'high'},{'extra_body':{}},{'resolution':'4K'},{'model':'gpt-image-2'},{'size':'8192x8192'}]:
            with self.assertRaises(Rejection):validate(self.body(**kwargs),'hanhe')
    def test_http200_error_not_success(self):
        with self.assertRaises(Rejection):response_payload({'error':{'message':'bad'}},'url')
        with self.assertRaises(Rejection):response_payload({'error':{'message':'bad'},'data':[{'url':123}]},'url')
    def test_invalid_results(self):
        for row in [{'url':123},{'url':'http://example.com/a'},{'url':'https://127.0.0.1/a'},{'b64_json':'not-base64'},{'b64_json':'YWJj'}]:
            with self.assertRaises(Rejection):response_payload({'data':[row]},'url')
    def test_url_to_b64(self):
        png=b'\x89PNG\r\n\x1a\n'+b'0'*32
        with patch('app.image_bytes',return_value=png):
            self.assertEqual(response_payload({'data':[{'url':'https://test.invalid/a'}]},'b64_json')['data'][0]['b64_json'],base64.b64encode(png).decode())
    def test_transfer_failure_must_not_retry(self):
        with patch('app.image_bytes',side_effect=ValueError()):
            with self.assertRaises(Rejection) as ctx:response_payload({'data':[{'url':'https://test.invalid/a'}]},'b64_json')
        self.assertEqual(ctx.exception.status,400)
    def test_http_auth_and_form_rejected_before_upstream(self):
        with patch.object(app,'CONFIG',{'adapter_token':'test','providers':{'hanhe':{}}}),patch('app.urllib.request.build_opener') as upstream:
            server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                for content_type,auth,expected in [('application/x-www-form-urlencoded','Bearer test',400),('multipart/form-data','Bearer test',400),('application/json','wrong',401)]:
                    c=http.client.HTTPConnection('127.0.0.1',server.server_port)
                    c.request('POST','/hanhe/v1/images/generations',body='{}',headers={'Content-Type':content_type,'Authorization':auth})
                    r=c.getresponse();self.assertEqual(r.status,expected);r.read();c.close()
                upstream.assert_not_called()
            finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
