from pathlib import Path
import hashlib,json
r=Path('/opt/ai-api-stack/releases/issue173-db-authority')
p=r/'source/middleware/distributor.go'
old='usingGroup := common.GetContextKeyString(c, constant.ContextKeyUsingGroup)'
s=p.read_text();assert s.count(old)==1
new=old+'\nif common.IsQuotaDBAuthoritative() && (modelRequest.Model=="gpt-image-2.5-flare" || modelRequest.Model=="gpt-image-2.5-sunburst") && service.GroupInUserUsableGroups(common.GetContextKeyString(c,constant.ContextKeyUserGroup),"图") {usingGroup="图";common.SetContextKey(c,constant.ContextKeyUsingGroup,usingGroup)}'
before=hashlib.sha256(p.read_bytes()).hexdigest();p.write_text(s.replace(old,new))
a=json.loads((r/'source-changes.json').read_text());a['middleware/distributor.go']={'before':before,'after':hashlib.sha256(p.read_bytes()).hexdigest()};(r/'source-changes.json').write_text(json.dumps(a,indent=2))
print('Exact image variants inherit image tariff for all authorized key groups; no price changes.')
