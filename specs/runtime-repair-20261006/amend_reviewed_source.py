"""Apply the reviewed amendments once to the already-built staging source."""
import hashlib
import pathlib
import shutil
import subprocess

ROOT = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
SOURCE = ROOT / 'source'
assert (ROOT / 'source-patched.sha256').exists()
assert not (ROOT / 'source-review-amended.sha256').exists()


def replace(name, old, new, count=1, limit=-1):
    path = SOURCE / name
    content = path.read_text()
    assert content.count(old) == count, 'review amendment anchor changed: ' + name
    path.write_text(content.replace(old, new, limit))


replace('controller/relay.go', '\t\taddUsedChannel(c, channel.Id)', '\t\taddUsedChannel(c, channel.Id)\n\t\tif service.IsImageRequestPath(c.Request.URL.Path) { c.Set("xtai_image_submit_started", false); c.Header(common.ImageSubmissionStateHeader, "not_submitted") }', count=2, limit=1)
replace('service/http.go', 'strings.EqualFold(k, "X-XingTu-Relay-Request-ID")', 'strings.EqualFold(k, common.ImageRelayRequestIDHeader) || strings.EqualFold(k, common.ImageSubmissionStateHeader)')
replace('model/log.go', 'otherStr := common.MapToJsonStr(other)', 'otherStr := common.MapToJsonStr(appendImageClientAudit(c, other))')
replace('model/log.go', 'otherStr := common.MapToJsonStr(params.Other)', 'otherStr := common.MapToJsonStr(appendImageClientAudit(c, params.Other))')
changed = ['controller/relay.go', 'service/http.go', 'model/log.go']
for source in (ROOT / 'native-overlay').rglob('*.go'):
    name = source.relative_to(ROOT / 'native-overlay').as_posix()
    target = SOURCE / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    changed.append(name)
shutil.copy2(ROOT / 'native_request_id.go', SOURCE / 'middleware/request-id.go')
subprocess.run(['docker', 'run', '--rm', '-v', str(SOURCE) + ':/src', '--entrypoint', '/usr/local/go/bin/gofmt', 'xtai/issue173-native-builder:verified', '-w'] + ['/src/' + name for name in changed], check=True)
(ROOT / 'source-review-amended.sha256').write_text('\n'.join(hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() + '  ' + name for name in sorted(changed)) + '\n')
print('review fixes applied to staging source only')
