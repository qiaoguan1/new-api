import importlib.util
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

BASE = pathlib.Path(__file__).parent
SOURCE = BASE if (BASE/'fetch-upstream-balance.py').exists() else BASE.parent/'scripts'
sys.path.insert(0, str(SOURCE))
sys.path.append('/opt/ai-api-stack/channel-monitor/scripts')
def load(name,filename):
    spec=importlib.util.spec_from_file_location(name,SOURCE/filename)
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module);return module
b=load('candidate_balance','fetch-upstream-balance.py')
r=load('candidate_recharges','fetch-upstream-recharges.py')

class LogoutTests(unittest.TestCase):
    def session(self):
        s=Mock();s.headers={};s._monitor_login_origin='https://example.com';s._monitor_login_sid='current-session';s.post.return_value.status_code=200;s.post.return_value.json.return_value={'success':True};return s
    def test_modern_logout_scoped_to_own_session(self):
        s=self.session();b.standard_logout(s,'https://example.com')
        self.assertEqual(s.post.call_args.args[0],'https://example.com/api/user/auth/logout')
        self.assertEqual(s.post.call_args.kwargs['headers']['X-Auth-Session'],'current-session')
        self.assertFalse(s.post.call_args.kwargs['allow_redirects']);s.close.assert_called_once()
    def test_failed_login_does_not_issue_logout(self):
        s=self.session();s._monitor_login_origin=None;b.standard_logout(s,'https://example.com');s.post.assert_not_called();s.close.assert_called_once()
    def test_successful_balance_lookup_always_logs_out(self):
        s=self.session()
        with patch.object(b.requests,'Session',return_value=s),patch.object(b,'standard_login'),patch.object(b,'standard_self',return_value={'quota':500000}):
            result=b.probe_balance('example',{'username':'u','password':'p','website_url':'https://example.com','rate':1},'')
        self.assertEqual(result['balance_usd'],1);s.post.assert_called_once();s.close.assert_called_once()
    def test_failed_collect_still_revokes_modern_session(self):
        s=self.session()
        with patch.object(b.requests,'Session',return_value=s),patch.object(b,'standard_login'),patch.object(b,'standard_self',side_effect=RuntimeError('lookup failed')),patch.object(b,'v1_login',side_effect=RuntimeError('not v1')):
            with self.assertRaises(RuntimeError):b.probe_balance('example',{'username':'u','password':'p','website_url':'https://example.com','rate':1},'')
        s.post.assert_called_once()
    def test_recharge_failure_runs_logout(self):
        s=self.session();c=Mock();c.origin_of.return_value='https://example.com';c.UA='test';c.standard_self.side_effect=RuntimeError('lookup failed')
        with patch.object(r.requests,'Session',return_value=s):
            with self.assertRaises(RuntimeError):r.collect_classic(c,{'username':'u','password':'p','website_url':'https://example.com'},'')
        c.standard_logout.assert_called_once_with(s,'https://example.com')
    def test_logout_error_never_masks_original_collection_result(self):
        s=self.session();s.post.side_effect=RuntimeError('secret response')
        with patch('sys.stderr') as output:b.standard_logout(s,'https://example.com')
        self.assertNotIn('secret response',str(output.write.call_args_list));s.close.assert_called_once()

if __name__=='__main__':unittest.main()
