import importlib.util
import json
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))

def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

d = load('daily-ops-digest')
r = load('fetch-upstream-recharges')
p = load('auto-apply-pricing')

class IsolationTests(unittest.TestCase):
    def test_expired_toonflow_returns_unknown_without_secret(self):
        collector = Mock()
        collector.origin_of.return_value = 'https://api.toonflow.net'
        collector._toonflow_token.side_effect = RuntimeError('secret-sk-do-not-print')
        result = r.collect_provider(collector, 'toonflow', {}, 'https://api.toonflow.net')
        self.assertEqual(result['status'], 'unavailable')
        self.assertIsNone(result['current_balance_usd'])
        self.assertNotIn('secret-sk', json.dumps(result))

    def test_main_persists_good_providers_when_middle_raises(self):
        targets = [(x,x,'https://example.com',{}) for x in ('good','bad','last')]
        with patch.object(r,'_load_balance_collector'), patch.object(r,'read_json',return_value={}), patch.object(r,'select_targets',return_value=targets), patch.object(r,'collect_provider',side_effect=[{'status':'complete'},RuntimeError('secret'),{'status':'complete'}]), patch.object(r,'write_private_json') as write:
            self.assertEqual(r.main(),0)
            result=write.call_args.args[1]
        self.assertEqual(result['complete'],2)
        self.assertEqual(result['unavailable'],1)
        self.assertNotIn('secret',json.dumps(result))

    def test_missing_sections_still_build_unknown_digest(self):
        report=d.build_digest([{'slug':'good','name':'Good'}],{},None,{},None,'2026-09-19',generated_at=200000)
        self.assertEqual(report['channels'][0]['collection_status'],'unknown')
        self.assertIsNone(report['channels'][0]['daily_cost_cny'])
        self.assertEqual(report['pricing']['status'],'unknown')
        self.assertIn('ledger_day_missing',report['warnings'])

    def test_corrupt_optional_file_becomes_unknown(self):
        with patch.object(d,'read_json',side_effect=d.DigestError('digest_input_invalid')):
            self.assertIsNone(d.read_optional_artifact(pathlib.Path('missing.json')))

    def test_invalid_complete_amount_does_not_break_mail_contract(self):
        report=d.build_digest([{'slug':'bad'}],{'days':{'2026-09-19':{'bad':{'collection_status':'complete'}}}},{'date':'2026-09-19'},{'runs':[{'date':'2026-09-19'}]},{'providers':{'bad':{'status':'complete'}}},'2026-09-19',generated_at=200000)
        self.assertEqual(report['channels'][0]['collection_status'],'unknown')
        self.assertEqual(report['channels'][0]['balance_status'],'unknown')

    def test_out_of_range_provider_cannot_reject_whole_email(self):
        report=d.build_digest([{'slug':'bad'}],{'days':{'2026-09-19':{'bad':{'collection_status':'complete','day_log_rows':10**12,'day_log_cost_cny':10}}}},{'date':'2026-09-19'},{'runs':[{'date':'2026-09-19'}]},{'providers':{'bad':{'status':'complete','balance_usd':1e20}}},'2026-09-19',generated_at=200000)
        self.assertEqual(report['channels'][0]['collection_status'],'unknown')
        self.assertEqual(report['channels'][0]['balance_status'],'unknown')
        self.assertIn('provider_statistics_invalid',report['warnings'])
        self.assertEqual(d._count(float('inf')),0)

    def test_timeout_is_local_to_provider(self):
        with patch.object(r,'_collect_provider',side_effect=TimeoutError('secret-response')):
            result=r.collect_provider(Mock(),'slow',{},'https://example.com')
        self.assertEqual(result['status'],'unavailable')
        self.assertNotIn('secret-response',json.dumps(result))

    def test_failed_recharge_provider_cannot_contribute_stale_money(self):
        report=d.build_digest([{'slug':'good'}],{'days':{'2026-09-19':{}}},{'date':'2026-09-19'}, {'runs':[{'date':'2026-09-19'}]}, {},'2026-09-19',generated_at=200000,recharges={'source':'authenticated_upstream_recharge_records','generated_at':200000,'providers':{'bad':{'status':'unavailable','paid_amounts':{'CNY':999}}}})
        self.assertEqual(report['recharges']['paid_cny_total'],0)
        self.assertIn('recharge_providers_unavailable',report['pricing']['reasons'])

    def test_stale_recharges_do_not_inflate_totals(self):
        report=d.build_digest([{'slug':'good'}],{'days':{'2026-09-19':{}}},{'date':'2026-09-19'}, {'runs':[{'date':'2026-09-19'}]}, {},'2026-09-19',generated_at=200000,recharges={'source':'authenticated_upstream_recharge_records','generated_at':1,'providers':{'bad':{'status':'complete','paid_amounts':{'CNY':999}}}})
        self.assertEqual(report['recharges']['status'],'unknown')
        self.assertIsNone(report['recharges']['paid_cny_total'])
        self.assertIn('recharge_summary_stale',report['pricing']['reasons'])

    def test_one_model_exception_does_not_abort_other_models(self):
        options={'ModelRatio':{},'CompletionRatio':{},'ModelPrice':{},'GroupRatio':{}}
        def plan(*args,**kw):
            target=kw['target_models']
            if not target:return {'date':'x','options':{k:{} for k in p.OPTION_KEYS},'decisions':[]}
            if target=={'bad'}:raise ValueError('do-not-print-secret')
            return {'options':{'ModelRatio':{'good':1},'CompletionRatio':{},'ModelPrice':{}},'decisions':[{'model':'good','action':'apply'}]}
        with patch.object(p,'build_audit_policy',return_value={'discovered_models':{'bad','good'}}),patch.object(p,'build_pricing_plan',side_effect=plan):
            result=p.build_isolated_pricing_plan({}, {},'x',options,max_change_ratio=0.5)
        self.assertEqual([x['action'] for x in result['decisions']],['skip','apply'])
        self.assertEqual(result['options']['ModelRatio'],{'good':1})
        self.assertNotIn('secret',json.dumps(result))

    def test_bad_channel_mapping_blocks_only_its_models(self):
        audit={'date':'2026-09-19','channels':[
            {'status':1,'scan_status':'ok','upstream_slug':'broken','model_mapping':'not-json','models':{'gpt-image-2':{'available':True}}},
            {'status':1,'scan_status':'ok','upstream_slug':'good','models':{'other':{'available':True}}}]}
        policy=p.build_audit_policy(audit,'2026-09-19')
        self.assertIn('gpt-image-2',policy['blocked_models'])
        self.assertEqual(policy['model_sources']['other'],{'good'})

if __name__=='__main__':unittest.main()
