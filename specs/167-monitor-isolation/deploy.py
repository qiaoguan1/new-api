"""Deploy only verified monitoring scripts with preconditions and rollback copies."""
import datetime
import hashlib
import json
import os
import pathlib
import shutil

ROOT = pathlib.Path('/opt/ai-api-stack/channel-monitor/scripts')
STAGED = pathlib.Path('/tmp/xtai-issue167/scripts')
EXPECTED = {
    'auto-apply-pricing.py': '0ca7b60f3e560081a41171c3c7a88458e33a1ae88b4cbb8a46c2782eefae7a0b',
    'daily-ops-digest.py': '9a5a4e54a025c773a4ff0b8ee929f915cea7617a2eee165ef25cffdccb6a1450',
    'fetch-upstream-recharges.py': 'ff8041bf8563f46c2f0c7e88fb35e539baf3b3842267d2bdc0a5f4047d45e9ef',
}

for name, expected in EXPECTED.items():
    assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest() == expected, name+' changed; stop'
    compile((STAGED/name).read_bytes(), name, 'exec')
backup = pathlib.Path('/opt/ai-api-stack/backups') / ('issue167-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
backup.mkdir(mode=0o700)
for name in EXPECTED:
    shutil.copy2(ROOT/name, backup/name)
try:
    for name in EXPECTED:
        temp = ROOT/(name+'.issue167.tmp')
        shutil.copyfile(STAGED/name, temp)
        os.chmod(temp, (ROOT/name).stat().st_mode & 0o777)
        os.replace(temp, ROOT/name)
except Exception:
    for name in EXPECTED:
        shutil.copy2(backup/name, ROOT/name)
    raise
print(json.dumps({'backup':str(backup),'deployed':list(EXPECTED)}))
