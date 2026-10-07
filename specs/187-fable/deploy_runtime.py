"""Binary-only issue187 deployment. This module never changes billing/data.

Run on the server only after review: python3 deploy_runtime.py deploy.
Requires image-bound pipeline/fake/real proofs, the exact baseline runtime,
and an absent image-job DRAIN. A busy 60-second drain cancels without a swap.
After candidate readiness commits, release/audit failures never restart it.
"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time
import urllib.request

MODEL = "claude-fable-5-1"
ROOT = Path("/opt/ai-api-stack/backups/fable187-20261007")
BASELINE_IMAGE = "sha256:8ac4d2a3590c34852de81b5e576c8212bc91304a35fbbbccd03bff117d794c97"
REQUIRED_NETWORKS = {"app-net", "ai-api-stack_stack-internal"}
STACK = Path("/opt/ai-api-stack")
COMPOSE = STACK / "docker-compose.override.yml"
NGINX = STACK / "nginx/conf.d/default.conf"
NGINX_CONTAINER = "ai-api-stack-nginx-1"
GATEWAY = "xtai-image-job-gateway-image-job-gateway-1"
GATEWAY_ROOT = Path("/opt/xtai-image-job-gateway")
DRAIN = GATEWAY_ROOT / "data/DRAIN"
DRAIN_OWNER = "issue187-native-binary-only"
STATUS_PORT = 18087
GATE_PATTERN = r"^/(v1/(images/(generations|edits)|responses(/compact)?|chat/completions|completions|messages|embeddings|moderations|rerank)|pg/chat/completions|v1(beta)?/models/[^?]+:(generateContent|streamGenerateContent|embedContent|batchEmbedContents|predict))/?$"


class DeploymentError(RuntimeError):
    """A credential-free failure safe for command-line reporting."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DeploymentError(message)


def private_write(path: Path, value: object) -> None:
    """Create exclusive, durable private evidence without replaying a phase."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def verify_proofs(candidate: dict, pipeline: dict, fake: dict, real: dict) -> None:
    """Reject stale/unbound proof summaries before touching ingress or compose."""
    image = candidate["image"]
    require(pipeline.get("success") is True and pipeline.get("phase") == "verified" and
            pipeline.get("production_unchanged") is True and pipeline.get("image") == image,
            "current-image validation pipeline is not complete")
    for proof in [fake, real]:
        require(proof.get("model") == MODEL and proof.get("image") == image and proof.get("billing_exact") is True and
                isinstance(proof.get("results"), list) and bool(proof["results"]), "current-image validation proof missing")
    require(fake.get("compatibility_verified") is True, "fake compatibility verification missing")
    require(all(row.get("billing_exact") is True if row.get("http") == 200 else
                row.get("http") == 400 and row.get("quota") == 0 and row.get("upstream_posts") == 0
                for row in fake["results"]), "fake verification contains failed cases")
    require(all(row.get("http") == 200 and row.get("billing_exact") is True and row.get("response_model") == MODEL
                for row in real["results"]), "real verification contains failed cases")


def change_native_image(text: str, image: str) -> str:
    """Change exactly services.new-api.image, preserving all other YAML values."""
    import yaml
    before = yaml.safe_load(text)
    require(isinstance(before, dict) and isinstance(before.get("services", {}).get("new-api"), dict), "native compose service missing")
    require(isinstance(before["services"]["new-api"].get("image"), str), "native compose image missing")
    after = copy.deepcopy(before)
    after["services"]["new-api"]["image"] = image
    result = yaml.safe_dump(after, allow_unicode=True, sort_keys=False)
    verified = yaml.safe_load(result)
    require(verified == after, "compose serialization changed unrelated values")
    verified["services"]["new-api"]["image"] = before["services"]["new-api"]["image"]
    require(verified == before, "compose change exceeds native image scope")
    return result


def maintenance_config(text: str) -> str:
    """Add scoped generation 503s; preserve every video/callback location byte."""
    server = "server_name api.aixingtuyun.com aixingtuyun.com www.aixingtuyun.com;"
    require(text.count(server) == 1 and "issue187-native-maintenance" not in text and
            str(STATUS_PORT) not in text, "ambiguous existing nginx gate or status listener")
    start = text.index(server)
    openings = list(re.finditer(r"\bserver\s*\{", text[:start]))
    require(bool(openings), "native nginx server block missing")
    index = openings[-1].end()
    # Put the maintenance regex first in this server, not merely before the
    # catch-all prefix: nginx takes the first matching regex location.
    depth, end, quote_char, escaped, comment = 1, index, None, False, False
    while end < len(text) and depth:
        char = text[end]
        if comment:
            comment = char != "\n"
        elif quote_char:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote_char:
                quote_char = None
        elif char in ("'", '"'):
            quote_char = char
        elif char == "#":
            comment = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        end += 1
    require(depth == 0 and index < start < end, "native nginx server boundary ambiguous")
    block = text[index:end]
    samples = ["/v1/" + path for path in ["messages", "chat/completions", "completions", "responses", "responses/compact",
               "images/generations", "images/edits", "embeddings", "moderations", "rerank"]] + ["/pg/chat/completions"]
    for match in re.finditer(r"location\s+(=|\^~)\s+(\S+)\s*\{", block):
        modifier, path = match.groups()
        shadows = re.fullmatch(GATE_PATTERN, path) is not None if modifier == "=" else (
            any(sample.startswith(path) for sample in samples) or
            any(prefix.startswith(path) or path.startswith(prefix) for prefix in ["/v1/models/", "/v1beta/models/"]))
        require(not shadows, "existing nginx location can bypass the maintenance gate")
    location = '''    # issue187-native-maintenance: text/image only
    location ~ GATE_PATTERN {
        add_header X-XingTu-Image-Submission-State not_submitted always;
        add_header X-XingTu-Relay-Request-ID $request_id always;
        default_type application/json;
        return 503 '{"error":{"code":"relay_maintenance","message":"Brief verified native upgrade; no generation submitted"}}';
    }
'''.replace("GATE_PATTERN", GATE_PATTERN)
    return text[:index] + "\n" + location + text[index:] + "\nserver { listen 127.0.0.1:" + str(STATUS_PORT) + "; location / { stub_status; } }\n"


def check_native(info: dict, values: dict, image: str, expected_env: dict | None = None,
                 networks: set | None = None) -> None:
    """Verify pinned binary, authority flags, pools and unchanged environments."""
    require(info.get("Image") == image and info.get("State", {}).get("Running") is True, "native binary is not ready")
    live_networks = set(info.get("NetworkSettings", {}).get("Networks", {}))
    require(REQUIRED_NETWORKS <= live_networks and (networks is None or live_networks == networks), "native networks changed")
    require(values.get("QUOTA_DB_AUTHORITATIVE") == "true" and values.get("BATCH_UPDATE_ENABLED") == "false" and
            [values.get(key) for key in ["SQL_MAX_OPEN_CONNS", "SQL_MAX_IDLE_CONNS", "SQL_MAX_LIFETIME"]] == ["30", "5", "300"],
            "native authority or pool settings changed")
    require(expected_env is None or values == expected_env, "native environment changed")


def verify_nginx_mount(info: dict) -> None:
    """Atomic host replacements require the existing read-only directory bind.

    A single-file bind retains its original inode after os.replace. Changing
    that shared proxy mount is a separate, user-approved maintenance operation.
    """
    mounts = info.get("Mounts", [])
    require(not any(m.get("Destination") == "/etc/nginx/conf.d/default.conf" for m in mounts),
            "single-file nginx bind requires approved proxy remount before deployment")
    require(any(m.get("Destination") == "/etc/nginx/conf.d" and
                m.get("Source") == str(STACK / "nginx/conf.d") and m.get("RW") is False for m in mounts),
            "read-only nginx directory bind not verified; deployment prohibited")


class RuntimeDeployment:
    """A scoped replacement state machine, with no SQL write or DB restore path."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self._rollout = None
        self._command_index = 0
        self._invocation = secrets.token_hex(8)
        self._compose_expected = None
        self._nginx_expected = None
        self._drain_identity = None
        self._drain_complete = False
        self._status_tool = "wget"

    @property
    def rollout(self):
        if self._rollout is None:
            spec = importlib.util.spec_from_file_location("runtime_rollout", Path(__file__).with_name("rollout.py"))
            require(spec is not None and spec.loader is not None, "rollout helper unavailable")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._rollout = module.Rollout(root=self.root)
        return self._rollout

    @property
    def h(self):
        return self.rollout.h

    def run(self, command: list, timeout: int = 75) -> str:
        """Keep complete command diagnostics private; never expose compose env."""
        process = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        self._command_index += 1
        private_write(self.root / ("runtime-command-" + self._invocation + "-" + str(self._command_index) + ".json"),
                      {"returncode": process.returncode, "stdout": process.stdout, "stderr": process.stderr})
        require(process.returncode == 0, "runtime command failed; inspect private diagnostics")
        return process.stdout

    def preflight(self) -> tuple:
        """All proof and semantic checks precede any maintenance or file mutation."""
        require(not (self.root / "runtime-started.json").exists() and not (self.root / "runtime-deployed.json").exists(),
                "runtime phase already started; reconcile instead of replaying")
        candidate = self.rollout.load_candidate()
        verify_proofs(candidate, self.rollout.read("validation-pipeline-result.json"),
                      self.rollout.read("fake-verified.json"), self.rollout.read("real-verified.json"))
        require(self.h.inspect("xtai-fable187-canary")["Image"] == candidate["image"], "canary image no longer matches proof")
        native = self.h.inspect(self.h.NATIVE)
        values = self.h.env(native)
        check_native(native, values, BASELINE_IMAGE)
        verify_nginx_mount(self.h.inspect(NGINX_CONTAINER))
        require(not DRAIN.exists() and not DRAIN.is_symlink(), "pre-existing image DRAIN requires user decision")
        compose, nginx = COMPOSE.read_text(), NGINX.read_text()
        changed, gate = change_native_image(compose, candidate["image"]), maintenance_config(nginx)
        import yaml
        original_image = yaml.safe_load(compose)["services"]["new-api"]["image"]
        require(self.run(["docker", "image", "inspect", "--format", "{{.Id}}", original_image]).strip() == BASELINE_IMAGE,
                "compose baseline image is not pinned baseline")
        candidate_info = json.loads(self.run(["docker", "image", "inspect", candidate["image"]]))[0]
        require(candidate_info["Id"] == candidate["image"], "candidate image unavailable")
        # Compose merges must recreate exactly the current environment, including
        # image defaults, not silently replace helper-created settings.
        resolved = json.loads(self.run(["docker", "compose", "-f", str(STACK / "docker-compose.yml"), "-f", str(COMPOSE), "config", "--format", "json"]))
        expected = dict(entry.split("=", 1) for entry in candidate_info["Config"].get("Env", []))
        expected.update({key: str(value) for key, value in resolved["services"]["new-api"].get("environment", {}).items()})
        require(expected == values, "compose would change native environment; user decision required")
        self.select_status_transport()
        self._compose_expected, self._nginx_expected = compose, nginx
        before = {"native_image": BASELINE_IMAGE, "native_env": values, "native_networks": list(native["NetworkSettings"]["Networks"]),
                  "native_inspect": native, "gateway_inspect": self.h.inspect(GATEWAY), "nginx_inspect": self.h.inspect(NGINX_CONTAINER),
                  "compose": compose, "base_compose": (STACK / "docker-compose.yml").read_text(), "nginx": nginx,
                  "gateway_compose": (GATEWAY_ROOT / "compose.yaml").read_text(), "candidate": candidate}
        return candidate, before, changed, gate

    def select_status_transport(self) -> None:
        """Verify an existing container tool before changing any ingress files."""
        tool = self.run(["docker", "exec", NGINX_CONTAINER, "sh", "-c",
                         "if command -v wget >/dev/null 2>&1; then printf wget; "
                         "elif command -v curl >/dev/null 2>&1; then printf curl; else exit 1; fi"])
        require(tool.strip() in {"wget", "curl"}, "nginx status transport unavailable")
        self._status_tool = tool.strip()

    def status_command(self) -> list:
        """Use only the fixed loopback listener and the preflight-verified tool."""
        address = "http://127.0.0.1:" + str(STATUS_PORT) + "/"
        require(self._status_tool in {"wget", "curl"}, "nginx status transport not verified")
        options = ["wget", "-qO-", address] if self._status_tool == "wget" else [
            "curl", "--fail", "--silent", "--show-error", "--max-time", "2", address]
        return ["docker", "exec", NGINX_CONTAINER] + options

    def backup(self, before: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        private_write(self.root / "runtime-started.json", {"at": int(time.time()), "baseline_image": BASELINE_IMAGE})
        private_write(self.root / "runtime-before.json", before)

    def atomic_text(self, path: Path, text: str, expected: str) -> None:
        """Refuse concurrent edits; preserve permissions on the existing file."""
        require(path.read_text() == expected and not path.is_symlink(), "deployment file changed concurrently")
        temporary = path.with_name(path.name + ".fable187.tmp")
        mode = path.stat().st_mode & 0o777
        with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)

    def write_compose(self, text: str) -> None:
        self.atomic_text(COMPOSE, text, self._compose_expected)
        self._compose_expected = text

    def nginx_reload(self, text: str) -> None:
        previous = self._nginx_expected
        self.atomic_text(NGINX, text, previous)
        self._nginx_expected = text
        try:
            loaded = self.run(["docker", "exec", NGINX_CONTAINER, "cat", "/etc/nginx/conf.d/default.conf"])
            require(loaded == text, "container did not receive scoped nginx configuration")
            self.run(["docker", "exec", NGINX_CONTAINER, "nginx", "-t"])
            self.run(["docker", "exec", NGINX_CONTAINER, "nginx", "-s", "reload"])
        except Exception:
            self.atomic_text(NGINX, previous, text)
            self._nginx_expected = previous
            loaded = self.run(["docker", "exec", NGINX_CONTAINER, "cat", "/etc/nginx/conf.d/default.conf"])
            require(loaded == previous, "restored container nginx configuration mismatch")
            self.run(["docker", "exec", NGINX_CONTAINER, "nginx", "-t"])
            self.run(["docker", "exec", NGINX_CONTAINER, "nginx", "-s", "reload"])
            raise

    def create_drain(self) -> None:
        descriptor = os.open(DRAIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        identity = os.fstat(descriptor)
        self._drain_identity = (identity.st_dev, identity.st_ino)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(DRAIN_OWNER)
            handle.flush()
            os.fsync(handle.fileno())
        self._drain_complete = True

    def remove_drain(self) -> None:
        identity = DRAIN.stat()
        require(not DRAIN.is_symlink() and self._drain_identity == (identity.st_dev, identity.st_ino), "image DRAIN ownership changed")
        contents = DRAIN.read_text()
        require(contents == DRAIN_OWNER if self._drain_complete else DRAIN_OWNER.startswith(contents), "image DRAIN contents changed")
        DRAIN.unlink()

    def wait_drained(self) -> bool:
        """Keep the strict 60-second all-writers guard with private diagnostics.

        Only fixed classifications and bounded integer samples are retained;
        status bodies, command stderr, request URLs and exceptions are omitted.
        The artifact is written before the caller restores a failed drain.
        """
        started, quiet = time.monotonic(), 0
        deadline = started + 60
        samples, classification, drained = [], "deadline_without_sample", False
        try:
            while time.monotonic() < deadline:
                sample = {"elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                          "active_images": -1, "unknown_images": -1, "transport_rc": -1,
                          "timed_out": 0, "parse_ok": 0, "reading": -1, "writing": -1, "quiet_count": 0}
                try:
                    with sqlite3.connect("file:" + str(GATEWAY_ROOT / "data/image-jobs.sqlite3") + "?mode=ro", uri=True, timeout=2) as connection:
                        active = connection.execute("SELECT count(*) FROM image_jobs WHERE status IN ('queued','submitting','running')").fetchone()[0]
                        unknown = connection.execute("SELECT count(*) FROM image_jobs WHERE status IS NULL OR status NOT IN ('queued','submitting','running','succeeded','failed','uncertain')").fetchone()[0]
                except Exception:
                    classification = "image_state_unavailable"
                    samples.append(sample)
                    raise
                sample.update(active_images=int(active), unknown_images=int(unknown))
                if unknown:
                    classification = "unknown_image_state"
                    samples.append(sample)
                    require(False, "unknown image-job state; cannot prove drain complete")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    classification = "deadline_during_image_check"
                    samples.append(sample)
                    return False
                clear = False
                try:
                    output = subprocess.run(self.status_command(), capture_output=True, text=True, timeout=min(3, remaining))
                    sample["transport_rc"] = int(output.returncode)
                    match = re.search(r"Reading: (\d+) Writing: (\d+)", output.stdout)
                    sample["parse_ok"] = int(match is not None)
                    if match is not None:
                        sample.update(reading=int(match[1]), writing=int(match[2]))
                    if output.returncode != 0:
                        classification = "status_transport_error"
                    elif match is None:
                        classification = "status_parse_error"
                    elif active:
                        classification = "active_image_jobs"
                    elif sample["reading"] != 0 or sample["writing"] > 1:
                        classification = "nginx_connections_busy"
                    else:
                        clear = True
                        classification = "quiet_window_incomplete"
                except subprocess.TimeoutExpired:
                    sample["timed_out"] = 1
                    classification = "status_transport_timeout"
                except Exception:
                    classification = "status_transport_unavailable"
                    samples.append(sample)
                    raise
                quiet = quiet + 1 if clear else 0
                sample["quiet_count"] = quiet
                samples.append(sample)
                if quiet >= 3 and time.monotonic() <= deadline:
                    classification, drained = "drained", True
                    return True
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(1, remaining))
            return False
        finally:
            private_write(self.root / ("runtime-drain-" + self._invocation + ".json"),
                          {"drained": drained, "classification": classification, "deadline_seconds": 60,
                           "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
                           "samples": samples[:64], "samples_truncated": len(samples) > 64})

    def compose_up(self) -> None:
        self.run(["docker", "compose", "-f", str(STACK / "docker-compose.yml"), "-f", str(COMPOSE), "up", "-d",
                  "--no-deps", "--pull", "never", "--timeout", "60", "new-api"], timeout=90)

    def ready(self, image: str, before: dict) -> None:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                native = self.h.inspect(self.h.NATIVE)
                check_native(native, self.h.env(native), image, before["native_env"], set(before["native_networks"]))
                with urllib.request.urlopen("http://127.0.0.1:3000/api/status", timeout=2) as response:
                    body = json.load(response)
                require(body.get("success") is True and body.get("data", {}).get("quota_db_authoritative") is True,
                        "native authority status unavailable")
                require(self.h.sql("SELECT 1;") == "1", "database readiness unavailable")
                require(self.h.inspect(GATEWAY)["Image"] == before["gateway_inspect"]["Image"] and
                        self.h.inspect(NGINX_CONTAINER)["Image"] == before["nginx_inspect"]["Image"], "unrelated service image changed")
                with urllib.request.urlopen(self.h.address(GATEWAY, 8090) + "/health", timeout=2) as response:
                    require(response.status == 200, "existing image gateway health unavailable")
                return
            except Exception:
                time.sleep(1)
        raise DeploymentError("native readiness failed; ingress must stay closed until verified recovery")

    def deploy(self) -> None:
        candidate, before, changed, gate = self.preflight()
        self.backup(before)
        paused = drained = swapped = ready_committed = False
        try:
            self.create_drain()
            drained = True
            paused = True  # Installation may touch/reload ingress before raising.
            self.nginx_reload(gate)
            require(self.wait_drained(), "60-second drain incomplete; runtime was not switched")
            self.write_compose(changed)
            swapped = True  # compose can partially recreate even when it fails.
            self.compose_up()
            self.ready(candidate["image"], before)
            ready_committed = True  # Never restart after readiness/accepted video traffic.
            private_write(self.root / "runtime-ready.json", {"image": candidate["image"], "at": int(time.time()), "ingress_released": False})
            self.nginx_reload(before["nginx"])
            paused = False
            self.remove_drain()
            drained = False
            private_write(self.root / "runtime-deployed.json", {"image": candidate["image"], "baseline_image": BASELINE_IMAGE,
                          "at": int(time.time()), "ingress_released": True, "wallets_not_restored": True,
                          "other_services_unchanged": True, "environment_unchanged": True})
        except Exception as primary:
            drained = drained or self._drain_identity is not None
            if ready_committed:
                private_write(self.root / "runtime-recovery-required.json", {"at": int(time.time()), "error_type": type(primary).__name__,
                              "candidate_kept_running": True, "ingress_release_requires_review": True, "wallets_not_restored": True})
                raise DeploymentError("ready candidate retained; inspect ingress release, do not replay deployment") from None
            try:
                if swapped:
                    self.write_compose(before["compose"])
                    self.compose_up()
                    self.ready(before["native_image"], before)
                if paused:
                    self.nginx_reload(before["nginx"])
                    paused = False
                if drained:
                    self.remove_drain()
                    drained = False
                private_write(self.root / "runtime-aborted.json", {"at": int(time.time()), "error_type": type(primary).__name__,
                              "binary_switch_attempted": swapped, "baseline_verified": swapped, "ingress_released": True, "wallets_not_restored": True})
            except Exception as recovery:
                private_write(self.root / "runtime-recovery-required.json", {"at": int(time.time()), "error_type": type(recovery).__name__,
                              "ingress_stays_closed": True, "wallets_not_restored": True})
                raise DeploymentError("recovery incomplete; retain maintenance gate and inspect private evidence") from None
            raise DeploymentError("deployment aborted; original ingress restored without database rollback") from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["deploy"])
    parser.parse_args()
    try:
        RuntimeDeployment().deploy()
        print(json.dumps({"completed": True, "binary_only": True, "production_data_unchanged": True}))
    except Exception as error:
        print(json.dumps({"completed": False, "error_type": type(error).__name__,
                          "reason": str(error) if isinstance(error, DeploymentError) else "inspect private runtime evidence"}))
        raise SystemExit(1) from None
