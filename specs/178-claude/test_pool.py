import unittest,yaml
from fix_pool import patch,VALUES

class PoolTests(unittest.TestCase):
 def test_preserves_other_services_and_sensitive_values(self):
  before={'services':{'new-api':{'image':'same','environment':{'SQL_DSN':'sensitive','BATCH_UPDATE_ENABLED':'false'}},'postgres':{'image':'same-db'}}}
  after=yaml.safe_load(patch(yaml.safe_dump(before)))
  self.assertEqual(after['services']['postgres'],before['services']['postgres'])
  self.assertEqual(after['services']['new-api']['environment']['SQL_DSN'],'sensitive')
  for k,v in VALUES.items():self.assertEqual(after['services']['new-api']['environment'][k],v)
 def test_fails_unsupported_environment_shape(self):
  with self.assertRaises(AssertionError):patch('services:\n  new-api:\n    environment: []')
