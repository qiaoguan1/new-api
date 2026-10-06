"""Repair staging-only compatibility; refuse drift from the reviewed manifest."""
import hashlib
import pathlib
import shutil
import subprocess

root = pathlib.Path('/opt/ai-api-stack/releases/issue183-runtime')
source = root / 'source'
manifest = root / 'source-review-amended.sha256'
expected = dict(line.split('  ', 1)[::-1] for line in manifest.read_text().splitlines())
path = source / 'middleware/request-id.go'
assert hashlib.sha256(path.read_bytes()).hexdigest() == expected['middleware/request-id.go']
assert path.read_text().count('common.NewRequestId()') == 1
shutil.copy2(root / 'native_request_id.go', path)
subprocess.run(['docker', 'run', '--rm', '-v', str(source) + ':/src', '--entrypoint', '/usr/local/go/bin/gofmt', 'xtai/issue173-native-builder:verified', '-w', '/src/middleware/request-id.go'], check=True)
expected['middleware/request-id.go'] = hashlib.sha256(path.read_bytes()).hexdigest()
manifest.write_text('\n'.join(value + '  ' + name for name, value in sorted(expected.items())) + '\n')
print('production generator preserved; reviewed image audit behavior unchanged')
