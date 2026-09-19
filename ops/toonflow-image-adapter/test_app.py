import unittest
from unittest.mock import Mock,patch
import app

class AdapterTests(unittest.TestCase):
    def test_official_body(self):
        b,f=app.translate({'model':'gpt-image-2','prompt':'circle','size':'1024x1024','response_format':'b64_json'})
        self.assertEqual(b,{'model':'全能图片G-2.0','prompt':'circle','size':'1k','metadata':{'aspectRatio':'1:1'}})
        self.assertEqual(f,'b64_json')
    def test_higher_resolutions_do_not_get_silently_downgraded(self):
        for addition in [{'size':'2048x2048'},{'size':'4K'},{'resolution':'2K'},{'image_size':'4K'},{'n':2},{'images':['data:image/png;base64,aA==']},{'extra_body':{'resolution':'4K'}},{'quality':'high'}]:
            with self.subTest(addition=addition),self.assertRaises(app.Failure) as e:app.translate({'model':'gpt-image-2','prompt':'circle',**addition})
            self.assertEqual(e.exception.status,429)
    def test_landscape_ratio(self):
        b,f=app.translate({'model':'gpt-image-2','prompt':'circle','size':'1536x1024'})
        self.assertEqual(b['metadata']['aspectRatio'],'3:2')
    def test_poll_success_submits_only_once(self):
        call=Mock(side_effect=[{'code':200,'data':'task123'},{'data':{'status':'running'}},{'data':{'status':'success','data':'https://cdn.example/image.png'}}]);record=Mock()
        result=app.run_generation({},'key',record,call,lambda _:None)
        self.assertEqual(result,'https://cdn.example/image.png')
        self.assertEqual([x.args[0] for x in call.call_args_list].count('/image/generateImage'),1)
        record.assert_any_call('submitted','task123')
    def test_uncertain_submission_never_retries(self):
        call=Mock(side_effect=TimeoutError());record=Mock()
        with self.assertRaises(app.Failure) as e:app.run_generation({},'key',record,call,lambda _:None)
        self.assertEqual(e.exception.status,400);call.assert_called_once();record.assert_called_once_with('submit_uncertain',None)
    def test_failed_task_does_not_trigger_channel_replay(self):
        call=Mock(side_effect=[{'code':200,'data':'task123'},{'data':{'status':'failed'}}])
        with self.assertRaises(app.Failure) as e:app.run_generation({},'key',Mock(),call,lambda _:None)
        self.assertEqual(e.exception.status,400);self.assertEqual(call.call_count,2)
    def test_pre_submit_rejection_can_fallback(self):
        with self.assertRaises(app.Failure) as e:app.run_generation({},'key',Mock(),Mock(return_value={'code':400,'data':None}),lambda _:None)
        self.assertEqual(e.exception.status,429)
    def test_malformed_success_and_server_errors_are_uncertain(self):
        for response in [{'code':200,'data':{}},{'code':500,'data':None}]:
            call=Mock(return_value=response)
            with self.assertRaises(app.Failure) as e:app.run_generation({},'key',Mock(),call,lambda _:None)
            self.assertEqual(e.exception.status,400);call.assert_called_once()
    def test_poll_deadline_keeps_pending_task(self):
        record=Mock();call=Mock(return_value={'code':200,'data':'task123'})
        with patch.object(app.time,'monotonic',side_effect=[0,200]),self.assertRaises(app.Failure) as e:app.run_generation({},'key',record,call,lambda _:None)
        self.assertEqual(e.exception.status,400);record.assert_any_call('pending','task123');call.assert_called_once()

if __name__=='__main__':unittest.main()
