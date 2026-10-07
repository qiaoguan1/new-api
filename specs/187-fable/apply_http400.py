"""Attested small amendment to the candidate only; never writes live source."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess


R = Path("/opt/ai-api-stack/releases/issue187-fable")


if __name__ == "__main__":
    marker = R / "source-amended.json"
    saved = json.loads(marker.read_text())
    for name, digest in saved["changed_sha256"].items():
        assert hashlib.sha256((R / "source" / name).read_bytes()).hexdigest() == digest
    archive = R / "http400-source-before"
    archive.mkdir(mode=0o700)
    paths = ["relay/helper/fable_compat.go", "controller/relay.go"]
    for name in paths:
        target = archive / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(R / "source" / name, target)
    shutil.copy2(marker, archive / marker.name)
    controller = R / "source/controller/relay.go"
    text = controller.read_text()
    old = '\t\t} else {\n\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest)\n\t\t}'
    new = ('\t\t} else if c.GetBool(helper.Fable51RequestContextKey) {\n'
           '\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest, types.ErrOptionWithStatusCode(http.StatusBadRequest))\n'
           '\t\t} else {\n\t\t\tnewAPIError = types.NewError(err, types.ErrorCodeInvalidRequest)\n\t\t}')
    assert text.count(old) == 1
    controller.write_text(text.replace(old, new, 1))
    shutil.copy2(R / "fable_compat.go", R / "source/relay/helper/fable_compat.go")
    assert not (R / "source/controller/fable_validation_test.go").exists()
    shutil.copy2(R / "fable_validation_test.go", R / "source/controller/fable_validation_test.go")
    paths += ["controller/fable_validation_test.go"]
    subprocess.run(["docker", "run", "--rm", "--network", "none", "-v", str(R / "source") + ":/build",
                    "golang:1.26.1-alpine", "gofmt", "-w", *["/build/" + name for name in paths]], check=True)
    saved["changed_sha256"].update({name: hashlib.sha256((R / "source" / name).read_bytes()).hexdigest() for name in paths})
    saved["new_files"].append("controller/fable_validation_test.go")
    saved["http400_scoped_amendment"] = True
    marker.write_text(json.dumps(saved, sort_keys=True, indent=2))
    print("attested exact-model HTTP400 amendment applied", flush=True)
