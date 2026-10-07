"""Bind the tested build to its baseline/source and refresh the gated tariff."""

import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess


R = Path("/opt/ai-api-stack/releases/issue187-fable")
B = Path("/opt/ai-api-stack/backups/fable187-20261007")
BASE = Path("/opt/ai-api-stack/releases/issue183-runtime/source")
BASE_IMAGE = "sha256:8ac4d2a3590c34852de81b5e576c8212bc91304a35fbbbccd03bff117d794c97"


def private(path: Path, data: object) -> None:
    """Use the rollout's private atomic exclusive evidence convention."""
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2))
    path.chmod(0o600)


if __name__ == "__main__":
    build = json.loads((R / "build-result.json").read_text())
    assert build["success"] is True
    amended = json.loads((R / "source-amended.json").read_text())
    digest = hashlib.sha256((R / "source-amended.json").read_bytes()).hexdigest()
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", build["image"]], text=True))[0]
    assert build["source_amended_sha256"] == digest == image["Config"]["Labels"]["xtai.issue187.source_sha256"]
    hashes = amended["changed_sha256"]
    baseline_hashes = {}
    for name, expected in hashes.items():
        assert hashlib.sha256((R / "source" / name).read_bytes()).hexdigest() == expected
        baseline_hashes[name] = hashlib.sha256((BASE / name).read_bytes()).hexdigest() if (BASE / name).exists() else None
    unchanged = 0
    for path in BASE.rglob("*"):
        name = path.relative_to(BASE).as_posix()
        if not path.is_file() or any(p in {".git", "__pycache__", "node_modules"} for p in path.parts) or name in hashes:
            continue
        assert (R / "source" / name).read_bytes() == path.read_bytes(), "non-Fable source changed"
        unchanged += 1
    proof = {"image": build["image"], "baseline_image": BASE_IMAGE, "source_sha256": hashes,
             "baseline_source_sha256": baseline_hashes, "unchanged_source_count": unchanged,
             "new_files": amended["new_files"], "build_label_sha256": digest}
    proof_path = B / "runtime-source-proof.json"
    private(proof_path, proof)
    manifest = {"image": build["image"], "tag": "new-api-fixed:issue187-fable", "baseline_image": BASE_IMAGE,
                "source_sha256": hashes, "source_proof": proof_path.name,
                "source_proof_sha256": hashlib.sha256(proof_path.read_bytes()).hexdigest()}
    private(B / "runtime-candidate.json", manifest)
    spec = importlib.util.spec_from_file_location("plan", R / "plan.py")
    planner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(planner)
    evidence = []
    for slug in ["rolldek-ccmax", "maolao"]:
        probe = json.loads((B / (slug + "-probe.json")).read_text())
        secret = json.loads((B / (slug + "-secret.json")).read_text())
        data = json.loads((B / (slug + "-logs.json")).read_text())["data"]
        rows = data.get("items", []) if isinstance(data, dict) else data
        matches = [row for row in rows if row.get("type") == 2 and row.get("model_name") == planner.MODEL
                   and row.get("token_name") == secret["name"]
                   and row.get("prompt_tokens") == probe["usage"]["prompt_tokens"]
                   and row.get("completion_tokens") == probe["usage"]["completion_tokens"]]
        assert len(matches) == 1
        bill = matches[0]
        other = json.loads(bill["other"]) if isinstance(bill["other"], str) else bill["other"]
        evidence.append({"source": slug, "group_ratio": other["group_ratio"], "cny_per_credit": secret["cny_per_credit"],
                         "probe": probe, "bill": bill, "expected_token_name": secret["name"]})
    candidate = planner.build_plan(evidence)
    assert candidate["compatibility_verified"] is False
    if not (B / "plan.pre-A.json").exists():
        shutil.copy2(B / "plan.json", B / "plan.pre-A.json")
    private(B / "evidence-linked.json", evidence)
    private(B / "plan.json", candidate)
    print(json.dumps({"candidate_image": manifest["image"], "changed_files": len(hashes),
                      "unchanged_source_files": unchanged, "compatibility_verified": False}), flush=True)
