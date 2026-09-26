import unittest
from decimal import Decimal
from plan_claude import derive,ratio,RETAINED

class PricingTest(unittest.TestCase):
 def test_rates_use_bill_metadata_not_rounded_small_sample(self):
  r=derive([{'quota':1,'prompt_tokens':4,'completion_tokens':2,'other':{'model_ratio':0.5,'group_ratio':0.2,'completion_ratio':5,'cache_ratio':0.1,'cache_creation_ratio':1.25}}],1.03)
  self.assertEqual(r['input'],Decimal('0.206'));self.assertEqual(r['output'],Decimal('1.030'));self.assertEqual(r['cache_write'],Decimal('0.25750'))
 def test_expensive_cache_dimension_not_discarded(self):
  a=derive([{'other':{'model_ratio':1.5,'group_ratio':0.6,'completion_ratio':5,'cache_ratio':0.1,'cache_creation_ratio':1.25}}],1)
  b=derive([{'other':{'model_ratio':1.5,'group_ratio':0.18,'completion_ratio':5,'cache_ratio':1}}],1)
  self.assertEqual(ratio(max(a['cache_read'],b['cache_read']),max(a['input'],b['input'])),0.3)
 def test_conflicting_rates_rejected(self):
  with self.assertRaises(AssertionError):derive([{'other':{'model_ratio':1,'group_ratio':1,'completion_ratio':5}},{'other':{'model_ratio':2,'group_ratio':1,'completion_ratio':5}}],1)
 def test_selected_routes_exclude_slow_claude_provider(self):
  self.assertEqual(len(RETAINED),8)
  self.assertEqual(sum(map(len,RETAINED.values())),11)
  for sources in RETAINED.values():
   self.assertTrue(1<=len(sources)<=2)
   self.assertTrue(set(sources)<={'codeplan','paisio'})
if __name__=='__main__':unittest.main()
