"""Backport token default fix onto exact issue173 production source, not newer UI."""
import pathlib,shutil,hashlib,json
BASE=pathlib.Path('/opt/ai-api-stack/releases/issue173-db-authority/source')
ROOT=pathlib.Path('/opt/ai-api-stack/releases/issue180-p1')
context=ROOT/'native-context';context.mkdir(exist_ok=True)
source=ROOT/'source';assert not source.exists(),'source already prepared'
shutil.copytree(BASE,source,ignore=shutil.ignore_patterns('.git','node_modules'))
p=source/'model/token.go';text=p.read_text();assert text.count('func (token *Token) Insert() error {')==1
text=text.replace('"github.com/QuantumNous/new-api/common"','"github.com/QuantumNous/new-api/common"\n"github.com/QuantumNous/new-api/setting"')
text=text.replace('func (token *Token) Insert() error {','''// ApplyDefaultGroup preserves explicit groups and the legacy disabled setting.
func (token *Token) ApplyDefaultGroup() {
 if setting.DefaultUseAutoGroup && token.Group == "" { token.Group = "auto" }
}

func (token *Token) Insert() error {
 token.ApplyDefaultGroup()''');p.write_text(text)
p=source/'controller/token.go';text=p.read_text();old='cleanToken.Group = token.Group';assert text.count(old)==1;p.write_text(text.replace(old,old+'\ncleanToken.ApplyDefaultGroup()'))
for name in ['model/token.go','controller/token.go','model/quota_authority.go']:
 target=context/name;target.parent.mkdir(exist_ok=True);shutil.copy2(source/name,target)
print(json.dumps({'context':str(context),'preserved_quota_authority_sha':hashlib.sha256((context/'model/quota_authority.go').read_bytes()).hexdigest()}))
