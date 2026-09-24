"""Backport only reviewed quota-authority changes onto exact deployed source."""
from pathlib import Path
import hashlib,json,shutil

BASE=Path('/opt/ai-api-stack/releases/issue157-stream-errors-72d5a71c/source')
OUT=Path('/opt/ai-api-stack/releases/issue173-db-authority/source')
OVERLAY=Path('/opt/ai-api-stack/releases/issue173-db-authority/overlay')
if OUT.exists():raise SystemExit('candidate source already exists; inspect before retry')
shutil.copytree(BASE,OUT,ignore=shutil.ignore_patterns('.git','node_modules'))
changes={}
def replace(name,old,new):
 p=OUT/name;text=p.read_text();assert text.count(old)==1,(name,old)
 changes.setdefault(name,{'before':hashlib.sha256(p.read_bytes()).hexdigest()})
 p.write_text(text.replace(old,new))

replace('main.go','if os.Getenv("BATCH_UPDATE_ENABLED") == "true" {','if os.Getenv("BATCH_UPDATE_ENABLED") == "true" && !common.IsQuotaDBAuthoritative() {')
replace('controller/misc.go','"quota_per_unit":              common.QuotaPerUnit,','"quota_per_unit":              common.QuotaPerUnit,\n"quota_db_authoritative":common.IsQuotaDBAuthoritative(),')
replace('model/user.go','func GetUserQuota(id int, fromDB bool) (quota int, err error) {','func GetUserQuota(id int, fromDB bool) (quota int, err error) {\nif common.IsQuotaDBAuthoritative(){err=DB.Model(&User{}).Where("id = ?",id).Select("quota").First(&quota).Error;return quota,err}')
replace('model/token.go','func GetTokenByKey(key string, fromDB bool) (token *Token, err error) {','func GetTokenByKey(key string, fromDB bool) (token *Token, err error) {\nif common.IsQuotaDBAuthoritative(){err=DB.Where(map[string]interface{}{"key":key}).First(&token).Error;return token,err}')
replace('model/user_cache.go','func GetUserCache(userId int) (userCache *UserBase, err error) {','func GetUserCache(userId int) (userCache *UserBase, err error) {\nif common.IsQuotaDBAuthoritative(){user,err:=GetUserById(userId,false);if err!=nil{return nil,err};return user.ToBaseUser(),nil}')
for name,signature,call in [
 ('model/user.go','func IncreaseUserQuota(id int, quota int, db bool) (err error) {','increaseUserQuota(id,quota)'),
 ('model/user.go','func DecreaseUserQuota(id int, quota int, db bool) (err error) {','decreaseUserQuota(id,quota)'),
 ('model/token.go','func IncreaseTokenQuota(tokenId int, key string, quota int) (err error) {','increaseTokenQuota(tokenId,quota)'),
 ('model/token.go','func DecreaseTokenQuota(id int, key string, quota int) (err error) {','decreaseTokenQuota(id,quota)')]:
 old=signature+'\n\tif quota < 0 {\n\t\treturn errors.New("quota 不能为负数！")\n\t}'
 replace(name,old,old+'\nif common.IsQuotaDBAuthoritative(){return '+call+'}')
replace('service/funding_source.go','model.DecreaseUserQuota(w.userId, amount, false)','model.ReserveUserQuota(w.userId, amount)')
replace('service/billing_session.go','model.DecreaseUserQuota(funding.userId, delta, false)','model.ReserveUserQuota(funding.userId, delta)')
replace('service/billing_session.go','func (s *BillingSession) shouldTrust(c *gin.Context) bool {','func (s *BillingSession) shouldTrust(c *gin.Context) bool {\nif common.IsQuotaDBAuthoritative(){return false}')
replace('service/quota.go','func PreConsumeTokenQuota(relayInfo *relaycommon.RelayInfo, quota int) error {','func PreConsumeTokenQuota(relayInfo *relaycommon.RelayInfo, quota int) error {\nif common.IsQuotaDBAuthoritative() && !relayInfo.IsPlayground{return model.ReserveTokenQuota(relayInfo.TokenId,quota)}')
for name in ['common/quota_authority.go','common/quota_math.go','model/quota_authority.go']:
 shutil.copy2(OVERLAY/name,OUT/name);changes[name]={'before':'added'}
for name,row in changes.items():row['after']=hashlib.sha256((OUT/name).read_bytes()).hexdigest()
(OUT.parent/'source-changes.json').write_text(json.dumps(changes,indent=2))
print(json.dumps({'source':str(OUT),'changed_files':list(changes)}))
