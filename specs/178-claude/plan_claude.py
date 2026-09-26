"""Derive tariff dimensions from dedicated-key actual bills, not token averages."""
import json,pathlib
from decimal import Decimal,ROUND_CEILING

RETAINED={
 'claude-haiku-4-5-20251001':['codeplan'],
 'claude-sonnet-4-6':['paisio','codeplan'],
 'claude-sonnet-5':['paisio'],
 'claude-opus-4-6':['paisio','codeplan'],
 'claude-opus-4-7':['paisio','codeplan'],
 'claude-opus-4-8':['paisio'],
 'claude-opus-5':['codeplan'],
 'claude-opus-5-5':['codeplan'],
}

def derive(rows,rate):
 rates=[]
 for row in rows:
  other=row.get('other') or {};other=json.loads(other) if isinstance(other,str) else other
  base=Decimal(str(other['model_ratio']))*Decimal(str(other['group_ratio']))*Decimal(str(rate))*2
  completion=Decimal(str(other['completion_ratio']));cache=Decimal(str(other.get('cache_ratio',1)));write=Decimal(str(other.get('cache_creation_ratio',1)))
  assert all(x>0 and x<10000 for x in [base,completion,cache,write])
  rates.append({'input':base,'output':base*completion,'cache_read':base*cache,'cache_write':base*write})
 assert rates and all(r==rates[0] for r in rates)
 return rates[0]

def ratio(numerator,denominator):return float((numerator/denominator).quantize(Decimal('0.000000000001'),rounding=ROUND_CEILING))

def make_plan(root):
 root=pathlib.Path(root);records={};probes={}
 for slug in ['maolao','paisio','codeplan']:
  probes[slug]=json.loads((root/(slug+'-probes.json')).read_text())
  d=json.loads((root/(slug+'-logs.json')).read_text())['data'];records[slug]=d['items'] if isinstance(d,dict) else d
 result=[]
 for model,sources in RETAINED.items():
  candidates=[]
  for rank,slug in enumerate(sources):
   tests=[x for x in probes[slug]['tests'] if x['model']==model and x.get('success') and x.get('response_model')==model];assert len(tests)==1
   bills=[x for x in records[slug] if x.get('type')==2 and x.get('model_name')==model and x.get('token_name')=='xtai-claude178-'+slug+'-20260926'];assert len(bills)==1
   prices=derive(bills,probes[slug]['cny_per_credit'])
   candidates.append({'source':slug,'priority':10 if rank==0 else 8,'cost_cny_per_m':{k:str(v) for k,v in prices.items()},'sample_quota':bills[0]['quota'],'sample_bill_request_id':bills[0]['request_id']})
  retail={k:max(Decimal(c['cost_cny_per_m'][k]) for c in candidates)*Decimal('1.5') for k in ['input','output','cache_read','cache_write']}
  options={'ModelRatio':ratio(retail['input'],Decimal('0.3')),'CompletionRatio':ratio(retail['output'],retail['input']),'CacheRatio':ratio(retail['cache_read'],retail['input']),'CreateCacheRatio':ratio(retail['cache_write'],retail['input'])}
  result.append({'model':model,'candidates':candidates,'retail_cny_per_m':{k:str(v) for k,v in retail.items()},'options':options})
 return {'schema_version':1,'models':result,'group':'文','markup':'1.5','group_ratio':'0.15','source':'dedicated_key_actual_bill_rates'}

if __name__=='__main__':
 import os,sys
 plan=make_plan(sys.argv[1]);p=pathlib.Path(sys.argv[1])/'plan.json'
 with os.fdopen(os.open(p,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600),'w') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
 for row in plan['models']:print(json.dumps({'model':row['model'],'providers':[x['source'] for x in row['candidates']],'price':row['retail_cny_per_m']},ensure_ascii=False))
