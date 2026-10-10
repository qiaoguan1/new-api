"""Explicit server-local, drained three-adapter rollout. No paid requests or DB writes.

Retain original containers and current financial stores. Any failure before
reopening restores only operation-owned containers/config, never a database.
Do not rerun after a completed or interrupted journal without reviewing it.
"""
from __future__ import annotations
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import ssl
import stat
import sqlite3
import subprocess
import time
import urllib.request
import deploy_image_fixes as h

OP = "6b2d94dcb4f34f229c17227ce6319484"
ROOT = Path("/opt/ai-api-stack/backups/issue190-20261010-" + OP)
STATE = ROOT / "adapter-rollout.json"
CONF = Path("/opt/ai-api-stack/nginx/conf.d/default.conf")
DRAIN = Path("/opt/xtai-image-job-gateway/data/DRAIN")
MARKER = ("issue190-adapters-" + OP + "\n").encode()
NGINX = "ai-api-stack-nginx-1"
PORTS = {h.BANANA: 8093, h.IMAGE25: 8095, h.NODY: 8097}
PG = "ai-api-stack-postgres-1"
MONITOR_SOURCE = h.LIVE_NATIVE_SOURCE / "setting/operation_setting/monitor_setting.go"
MONITOR_SOURCE_SHA256 = "9151bc389ff0f85fdceeadc5cc68ee3476afe5ccb6c6c281d643beb6ba0a76b1"
MONITOR_SOURCE_PROFILES = {
    MONITOR_SOURCE_SHA256: "modern",
    # Exact production issue187 source, independently captured/reviewed by root:
    # default false/10, frequency-only GetMonitorSetting; no enabled override.
    "8856093e690a099ebda31f0ef6e8d216bfe4815526a8ed6158752546e962ea5f": "legacy",
}
GATE_PROBES = tuple(("POST", path) for path in (
    "/v1/edits", "/v1/images/generations", "/v1/images/edits", "/v1/responses",
    "/v1/responses/compact", "/v1/chat/completions", "/v1/completions", "/v1/messages",
    "/pg/chat/completions", "/api/channel/test", "/api/channel/test/1")) + (
    ("GET", "/api/channel/test"), ("GET", "/api/channel/test/1"))

class DockerAPIError(h.DeploymentError):
    """Only an explicit Docker404 can prove absence; other failures are unknown."""
    def __init__(self, status):
        self.status = status
        super().__init__("Scoped Docker action failed; inspect private journal")

class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")

def docker(path, method="GET", body=None):
    connection = DockerConnection("localhost", timeout=30)
    try:
        connection.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(16 * 1024 * 1024 + 1)
        if response.status not in {200, 201, 204, 304}: raise DockerAPIError(response.status)
        h.require(len(raw) <= 16 * 1024 * 1024, "Bounded Docker result exceeded its limit")
        return json.loads(raw) if raw else {}
    finally:
        connection.close()

def inspect(name):
    return docker("/containers/" + name + "/json")

def inspect_optional(name):
    try: return inspect(name)
    except DockerAPIError as error:
        if error.status == 404: return None
        raise

def command(args, timeout=20):
    result = subprocess.run(args, capture_output=True, timeout=timeout)
    h.require(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024, "Scoped command failed; no raw environment is printed")
    return result.stdout

def save(state):
    stage = ROOT / "adapter-rollout.tmp"
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle); handle.flush(); os.fsync(handle.fileno())
    os.replace(stage, STATE)
    directory = os.open(ROOT, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)

def assert_private_root():
    h.require(ROOT.is_dir() and not ROOT.is_symlink() and ROOT.resolve() == ROOT.absolute(), "Protected operation directory is unavailable")
    info = ROOT.stat()
    h.require(info.st_uid == 0 and info.st_mode & 0o077 == 0, "Operation directory must be root-owned0700")

def channel_tests_enabled(environment, snapshot, *, profile="modern"):
    """Resolve exact source-profile semantics; never invent a legacy override.

    Both verified profiles default false and apply positive frequency. Only the
    modern profile supports CHANNEL_TEST_ENABLED. Production selects the profile
    from the captured source hash, not a manifest/operator-supplied flag.
    """
    h.require(profile in {"legacy", "modern"}, "Unverified monitor semantics profile")
    h.require(isinstance(snapshot, dict) and isinstance(snapshot.get("options"), list), "Monitor source is unknown")
    active = snapshot.get("channel_test_active")
    h.require(type(active) is int and active >= 0, "Active channel-test state is unknown")
    values = []
    seen = set()
    for row in snapshot["options"]:
        h.require(isinstance(row, dict) and row.get("key") in {"monitor_setting", "monitor_setting.auto_test_channel_enabled"}, "Unexpected monitor source")
        key = row["key"]
        h.require(key not in seen, "Duplicate monitor source")
        seen.add(key)
        raw = row.get("value")
        h.require(isinstance(raw, str), "Malformed monitor option")
        if key == "monitor_setting":
            try: parsed = json.loads(raw)
            except ValueError: raise h.DeploymentError("Monitor object cannot be decoded") from None
            h.require(isinstance(parsed, dict), "Monitor object is malformed")
            if "auto_test_channel_enabled" not in parsed: continue
            value = parsed["auto_test_channel_enabled"]
            h.require(type(value) is bool, "Monitor object enabled flag is unknown")
        else:
            value = go_bool(raw)
        values.append(value)
    h.require(not values or all(value == values[0] for value in values), "Conflicting monitor sources require review")
    enabled = values[0] if values else False  # verified implementation default
    frequency = environment.get("CHANNEL_TEST_FREQUENCY", "")
    if frequency:
        h.require(isinstance(frequency, str) and re.fullmatch(r"[+-]?[0-9]+", frequency) is not None, "Monitor frequency is malformed")
        number = int(frequency)
        h.require(-(2**63) <= number < 2**63, "Monitor frequency exceeds Go integer range")
        if number > 0: enabled = True
    if profile == "modern" and "CHANNEL_TEST_ENABLED" in environment:
        enabled = go_bool(environment["CHANNEL_TEST_ENABLED"])
    return enabled

def go_bool(value):
    if value in {"1", "t", "T", "TRUE", "true", "True"}: return True
    if value in {"0", "f", "F", "FALSE", "false", "False"}: return False
    raise h.DeploymentError("Monitor boolean cannot be proven")

def monitor_implementation_verified():
    h.require(MONITOR_SOURCE.is_file() and not MONITOR_SOURCE.is_symlink(), "Compiled monitor source is unavailable")
    digest = hashlib.sha256(MONITOR_SOURCE.read_bytes()).hexdigest()
    h.require(digest in MONITOR_SOURCE_PROFILES, "Compiled monitor semantics differ; read-only review required")
    return MONITOR_SOURCE_PROFILES[digest]

def monitor_snapshot():
    # Fixed SELECT only: no arbitrary SQL, mutation, wallet/token or price fields.
    sql = "SELECT json_build_object('options',(SELECT coalesce(json_agg(json_build_object('key',key,'value',value)),'[]'::json) FROM options WHERE key IN ('monitor_setting','monitor_setting.auto_test_channel_enabled')),'channel_test_active',(SELECT count(*) FROM system_tasks WHERE type='channel_test' AND status IN ('pending','running')))::text;"
    raw = command(["docker", "exec", PG, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "newapi", "-d", "new-api", "-At", "-c", sql], timeout=10)
    h.require(len(raw) <= 65536, "Monitor evidence exceeds its bound")
    try: return json.loads(raw)
    except ValueError: raise h.DeploymentError("Monitor evidence is unavailable") from None

def assert_private_ingress(info):
    host = info.get("HostConfig")
    h.require(isinstance(host, dict) and host.get("NetworkMode") not in {"host", "none"}
              and not str(host.get("NetworkMode", "")).startswith("container:"), "Unproved native/adapter network mode")
    for bindings in (host.get("PortBindings") or {}).values():
        for binding in bindings or []:
            h.require(isinstance(binding, dict) and binding.get("HostIp") in {"127.0.0.1", "::1"}, "Public native/adapter port bypasses admission gate")

def assert_internal_submitters(native):
    profile = monitor_implementation_verified()
    h.require(profile in {"legacy", "modern"}, "Monitor implementation not verified")
    assert_private_ingress(native)
    h._environment(native["Config"])
    environment = dict(row.split("=", 1) for row in native["Config"].get("Env") or [])
    snapshot = monitor_snapshot()
    h.require(channel_tests_enabled(environment, snapshot, profile=profile) is False and snapshot["channel_test_active"] == 0,
              "Automatic or active channel tests prevent safe adapter replacement")
    return True

class LocalNginxTLS(http.client.HTTPSConnection):
    """Use loopback nginx with the real TLS hostname; no external DNS/redirect."""
    def connect(self):
        raw = socket.create_connection(("127.0.0.1", 443), timeout=self.timeout)
        try: self.sock = ssl.create_default_context().wrap_socket(raw, server_hostname="api.aixingtuyun.com")
        except Exception:
            raw.close(); raise

def nginx_probe(method, path):
    h.require((method, path) in GATE_PROBES, "Unapproved free admission probe")
    connection = LocalNginxTLS("api.aixingtuyun.com", timeout=3)
    try:
        connection.request(method, path, body=b"{}" if method == "POST" else None,
                           headers={"Content-Type": "application/json", "Host": "api.aixingtuyun.com"})
        response = connection.getresponse(); raw = response.read(16385)
        h.require(len(raw) <= 16384, "Admission probe exceeds its bound")
        try: body = json.loads(raw)
        except ValueError: raise h.DeploymentError("Admission gate response is not proven") from None
        return response.status, dict(response.getheaders()), body
    finally: connection.close()

def probe_maintenance():
    for method, path in GATE_PROBES:
        status, headers, body = nginx_probe(method, path)
        headers = {key.lower(): value for key, value in headers.items()}
        error = body.get("error") if isinstance(body, dict) else None
        h.require(status == 503 and headers.get("x-xingtu-image-submission-state") == "not_submitted"
                  and bool(headers.get("x-xingtu-relay-request-id")) and isinstance(error, dict)
                  and error.get("code") == "relay_maintenance" and OP in str(error.get("message", "")),
                  "Actual no-submit maintenance gate is not owned/active")
    return True

def assert_owned_admission(state):
    h.require(not CONF.is_symlink() and hashlib.sha256(CONF.read_bytes()).hexdigest() == state["gated_nginx_sha256"], "Owned nginx gate changed externally")
    h.require(not DRAIN.is_symlink() and DRAIN.read_bytes() == MARKER, "Owned image drain changed externally")
    for name, identity in ((h.IMAGE_GATEWAY, state["gateway_id"]), (NGINX, state["nginx_id"]), (h.NATIVE, state["native_id"])):
        info = inspect(name)
        h.require(info["Id"] == identity and info["State"].get("Running") is True, "Concurrent gateway/native/nginx operation detected")
        if name == h.NATIVE:
            h.require(info["Image"] == state["native_image"], "Native image changed externally")
            assert_internal_submitters(info)
    ready = local_get(h.IMAGE_GATEWAY, 8090, "/ready")
    h.require(ready.get("draining") is True and ready.get("accepting") is False, "Image gateway has not acknowledged the drain")
    return probe_maintenance()

def pre_switch_backup(state, manifest):
    """Optional reviewed backup during ALREADY authorized deployment maintenance.

    Does not independently introduce downtime or execute price/collector tasks.
    Root must explicitly supply the installed worker SHA; absence means no hook.
    A worker failure aborts promotion with originals and financial data intact.
    """
    hook = manifest.get("pre_switch_backup")
    if hook is None: return
    h.require(isinstance(hook, dict) and set(hook) == {"enabled", "entrypoint_sha256"}
              and hook["enabled"] is True, "Backup hook is not explicitly approved")
    h._identity(hook["entrypoint_sha256"])
    path = Path("/opt/ai-api-stack/channel-monitor/scripts/newapi-daily-backup.py")
    h.require(path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == hook["entrypoint_sha256"], "Reviewed installed backup worker changed")
    assert_owned_admission(state)
    state["pre_switch_backup"] = {"status": "started", "entrypoint_sha256": hook["entrypoint_sha256"]}; save(state)
    raw = command(["python3", str(path), "--retain", "100"], timeout=900)
    fd = os.open(ROOT / "release-backup.stdout", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(raw); output.flush(); os.fsync(output.fileno())
    state["pre_switch_backup"]["status"] = "worker_succeeded"; save(state)
    assert_owned_admission(state)

def write_conf(content, expected):
    # Preserve the inode: the running nginx container may bind this exact file.
    h.require(not CONF.is_symlink(), "Nginx configuration link changed externally")
    fd = os.open(CONF, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r+b") as output:
        identity = os.fstat(output.fileno())
        h.require(stat.S_ISREG(identity.st_mode) and output.read() == expected, "Nginx configuration changed externally; preserve it")
        output.seek(0); output.write(content); output.truncate(); output.flush(); os.fsync(output.fileno())
        current = CONF.stat()
        h.require((identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino), "Nginx path replaced externally; preserve foreign file")
    command(["docker", "exec", NGINX, "timeout", "15s", "nginx", "-t"])
    command(["docker", "exec", NGINX, "timeout", "15s", "nginx", "-s", "reload"])

def local_get(name, port, path):
    info = inspect(name)
    ip = info["NetworkSettings"]["Networks"]["app-net"]["IPAddress"]
    request = urllib.request.Request("http://" + ip + ":" + str(port) + path)
    try: response = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3)
    except urllib.error.HTTPError as error:
        if name != h.IMAGE_GATEWAY or path != "/ready" or error.code != 503:
            error.close(); raise
        response = error  # a draining gateway truthfully reports readiness503
    with response:
        body = response.read(32769)
        h.require(len(body) <= 32768, "Bounded readiness result exceeded its limit")
        return json.loads(body)

def thread_count(name):
    # Read PID1 (the confirmed Python handler), not the newly executed process.
    raw = command(["docker", "exec", name, "python", "-c",
                   "import pathlib;print(len(list(pathlib.Path('/proc/1/task').iterdir())))"])
    h.require(re.fullmatch(rb"[0-9]+\n?", raw) is not None, "Handler activity is unknown")
    return int(raw)

def idle_sample(state):
    admission = assert_owned_admission(state)
    connection = sqlite3.connect("file:/opt/xtai-image-job-gateway/data/image-jobs.sqlite3?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("pragma query_only=on")
        active = connection.execute("select count(*) from image_jobs where status in ('queued','submitting','running')").fetchone()[0]
    finally:
        connection.close()
    ready = local_get(h.BANANA, 8093, "/ready")
    activity = command(["docker", "exec", NGINX, "wget", "-qO-", "http://127.0.0.1:18090/"])
    match = re.search(rb"Reading: (\d+) Writing: (\d+)", activity)
    h.require(match is not None, "Ingress activity is unknown")
    return {"image_jobs_active": int(active), "banana_active_requests": ready.get("active_requests"),
            "nody_handler_threads": thread_count(h.NODY), "image25_handler_threads": thread_count(h.IMAGE25),
            "native_reading": int(match[1]), "native_writing": int(match[2]),
            "admission_verified": admission, "channel_tests_disabled": admission}

def assert_quiet(state):
    samples = []
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            sample = idle_sample(state)
            quiet = (sample["image_jobs_active"] == 0 and sample["banana_active_requests"] == 0
                     and sample["nody_handler_threads"] == 1 and sample["image25_handler_threads"] == 1
                     and sample["native_reading"] == 0 and sample["native_writing"] <= 1
                     and sample.get("admission_verified") is True and sample.get("channel_tests_disabled") is True)
            samples = (samples + [sample])[-3:] if quiet else []
            if len(samples) < 3:
                time.sleep(1)
                continue
            evidence = {"operation_id": OP, "maintenance_active": all(row.get("admission_verified") is True for row in samples), "owned_image_drain": DRAIN.read_bytes() == MARKER,
                        "internal_submitters_excluded": all(row.get("channel_tests_disabled") is True for row in samples), "checked_at": time.time(), "samples": samples}
            h.assert_idle_evidence(evidence, OP, now=time.time())
            return evidence
        except (h.DeploymentError, OSError, urllib.error.URLError):
            samples = []
            time.sleep(1)
    raise h.DeploymentError("Accepted or unknown requests remain; adapter swap refused")

def assert_candidate_owned(info, name, target):
    h.require(name in PORTS and isinstance(info, dict), "Candidate identity is unavailable")
    h._identity(info.get("Id", ""))
    h.require(info.get("Name") in {"/" + name, "/" + name + "-failed190-" + OP[:12]}
              and info.get("Image") == target["candidate_image"], "Candidate instance/image/name ownership differs")
    labels = info.get("Config", {}).get("Labels") or {}
    h.require(labels.get(h.OP_LABEL) == OP and labels.get("com.aixingtuyun.image-fixes-app-sha256") == target["app_sha256"],
              "Candidate operation/source label ownership differs")

def reconcile_create(name, state, target):
    """Resolve a lost create response only by exact name/image/source/op; never create again."""
    info = inspect_optional(name)
    if info is None: return None
    if info.get("Id") == state["targets"][name]["before_id"]:
        return None  # stop/rename failed before any candidate creation
    assert_candidate_owned(info, name, target)
    recorded = state["targets"][name].get("new_id")
    h.require(recorded in (None, info["Id"]), "Journal candidate identity conflicts with current instance")
    state["targets"][name].update(new_id=info["Id"], create_reconciled=True)
    save(state)
    return info["Id"]

def adapter_active(name, identity):
    """Read only the exact owned handler; missing/malformed activity is not idle."""
    if name == h.BANANA:
        value = local_get(identity, PORTS[name], "/ready").get("active_requests")
        h.require(type(value) is int and value >= 0, "Banana activity is unknown")
        return value
    value = thread_count(identity)
    h.require(type(value) is int and value >= 1, "Image handler activity is unknown")
    return value - 1

def assert_rollback_idle(name, identity, target, state):
    """Never stop a potentially active replacement, even when promotion failed."""
    deadline = time.monotonic() + 45
    samples = 0
    while time.monotonic() < deadline:
        info = inspect(identity)
        assert_candidate_owned(info, name, target)
        h.require(info["Id"] == identity, "Recorded candidate identity changed")
        assert_owned_admission(state)
        if info.get("State", {}).get("Running") is False:
            return  # a proven stopped process cannot contain an active handler
        h.require(info.get("State", {}).get("Running") is True, "Candidate running state is unknown")
        if adapter_active(name, identity) == 0:
            samples += 1
        else: samples = 0
        if samples >= 3: return
        time.sleep(1)
    raise h.DeploymentError("Replacement remains active/unknown; preserve maintenance for manual reconciliation")

def native_adapter_health(name):
    """Free native-network health read verifies fresh DNS/connectivity, never generation."""
    h.require(name in PORTS, "Unapproved adapter network probe")
    raw = command(["docker", "exec", h.NATIVE, "timeout", "8s", "wget", "-qO-", "http://" + name + ":" + str(PORTS[name]) + "/health"], timeout=10)
    h.require(len(raw) <= 32768, "Adapter health exceeds its bound")
    try: body = json.loads(raw)
    except ValueError: raise h.DeploymentError("Native adapter connectivity is unconfirmed") from None
    h.require(isinstance(body, dict) and body.get("ok") is True, "Native cannot reach restored/current adapter")

def verify_restored_adapter(name, old):
    """Verify the retained instance against current mounts, never restoring a DB or secret file."""
    current = inspect(old["Id"])
    h.require(current.get("Id") == old["Id"] and current.get("Image") == old["Image"]
              and current.get("Name") == "/" + name and current.get("State", {}).get("Running") is True,
              "Retained original identity is not restored")
    for key in ("Config", "HostConfig"):
        h.require(current.get(key) == old.get(key), "Retained original runtime config drifted")
    h.require(h._canonical_mounts(current.get("Mounts")) == h._canonical_mounts(old.get("Mounts")), "Retained original mounts drifted")
    h.require(h.network_endpoints(current) == h.network_endpoints(old), "Retained original network aliases/static settings drifted")
    h.require(local_get(name, PORTS[name], "/health").get("ok") is True, "Restored adapter is not healthy")
    native_adapter_health(name)

def retain_failed_candidate(name, target, manifest, state):
    """Compensate only a verified idle owned instance; journal before stopping it."""
    identity = target["new_id"]
    assert_rollback_idle(name, identity, manifest["targets"][name], state)
    target["rollback_stop_intent"] = True; save(state)
    docker("/containers/" + identity + "/stop?t=15", "POST")
    # Recheck ownership after an ambiguous stop boundary before changing names.
    current = inspect(identity)
    assert_candidate_owned(current, name, manifest["targets"][name])
    h.require(current.get("State", {}).get("Running") is False, "Candidate stop result is unknown")
    docker("/containers/" + identity + "/rename?name=" + name + "-failed190-" + OP[:12], "POST")
    for network in inspect(identity)["NetworkSettings"]["Networks"]:
        docker("/networks/" + network + "/disconnect", "POST", {"Container": identity, "Force": False})

def run():
    h.require(os.name == "posix", "Production rollout requires the Linux server")
    import fcntl
    h.require(os.geteuid() == 0, "Protected server operation required")
    assert_private_root()
    lock = os.open(ROOT / "adapter-rollout.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_locked()
    finally: os.close(lock)

def run_locked():
    """Run only under the operation lock; no interrupted journal is automatically resumed."""
    h.require(not STATE.exists() and not STATE.is_symlink(), "Prior rollout journal requires explicit review; no automatic retry")
    manifest = json.loads((ROOT / "adapter-candidates.json").read_text())
    h.require(manifest["operation"] == OP and set(manifest["targets"]) == set(PORTS), "Candidate scope mismatch")
    before = {name: inspect(name) for name in PORTS}
    for name, info in before.items():
        target = manifest["targets"][name]
        h._identity(target["candidate_image"], image=True); h._identity(target["app_sha256"])
        h.require(info["Id"] == target["before_id"] and info["Image"] == target["before_image"], "Current adapter identity differs")
        candidate = docker("/images/" + target["candidate_image"] + "/json")
        h.require(candidate["Id"] == target["candidate_image"] and candidate["Config"]["Labels"].get("com.aixingtuyun.image-fixes-app-sha256") == target["app_sha256"], "Candidate immutable/source attestation differs")
        h.clone_create_config(info, target["candidate_image"], OP, image_labels=candidate["Config"]["Labels"])
        assert_private_ingress(info)
        for suffix in ("-rollback190-", "-failed190-"):
            h.require(inspect_optional(name + suffix + OP[:12]) is None, "Existing rollback/failed name is not owned by this fresh operation")
    native = inspect(h.NATIVE)
    assert_internal_submitters(native)
    nginx = inspect(NGINX)
    h.require(not DRAIN.exists() and not DRAIN.is_symlink(), "Existing drain belongs to another operation")
    original = CONF.read_bytes()
    h.require(b"18090" not in original and b"issue190-adapters" not in original, "Maintenance identity collision")
    start = original.index(b"server_name api.aixingtuyun.com aixingtuyun.com www.aixingtuyun.com;")
    index = original.index(b"    location / {", start)
    gated = original[:index] + h.maintenance_location(OP).encode() + original[index:]
    gated += b"\nserver { listen 127.0.0.1:18090; location / { stub_status; } }\n"
    gateway_id = inspect(h.IMAGE_GATEWAY)["Id"]
    state = {"operation": OP, "phase": "staged", "before": before, "gateway_id": gateway_id,
             "native_id": native["Id"], "native_image": native["Image"], "nginx_id": nginx["Id"],
             "gated_nginx_sha256": hashlib.sha256(gated).hexdigest(),
             "original_nginx_sha256": hashlib.sha256(original).hexdigest(), "targets": {}, "paid_requests": 0}
    original_path = ROOT / "adapter-nginx.before.conf"
    fd = os.open(original_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(original); output.flush(); os.fsync(output.fileno())
    save(state)
    fd = os.open(DRAIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(MARKER); output.flush(); os.fsync(output.fileno())
    directory = os.open(DRAIN.parent, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
    committed = False
    try:
        write_conf(gated, original)
        state["phase"] = "draining"; save(state)
        state["idle"] = assert_quiet(state); save(state)
        pre_switch_backup(state, manifest)
        for name, old in before.items():
            state["idle"] = assert_quiet(state)
            h.assert_fresh_baseline(old, inspect(name))
            assert_owned_admission(state)
            state["phase"] = "replacing"; state["current_target"] = name
            state["targets"][name] = {"before_id": old["Id"], "new_id": None}; save(state)
            docker("/containers/" + old["Id"] + "/stop?t=15", "POST")
            docker("/containers/" + old["Id"] + "/rename?name=" + name + "-rollback190-" + OP[:12], "POST")
            for network in old["NetworkSettings"]["Networks"]:
                docker("/networks/" + network + "/disconnect", "POST", {"Container": old["Id"], "Force": False})
            image = manifest["targets"][name]["candidate_image"]
            labels = docker("/images/" + image + "/json")["Config"]["Labels"]
            payload = h.clone_create_config(old, image, OP, image_labels=labels)
            state["targets"][name]["create_intent"] = True; save(state)
            try: created = docker("/containers/create?name=" + name, "POST", payload)
            except Exception:
                state["targets"][name]["create_outcome_unknown"] = True; save(state)
                reconcile_create(name, state, manifest["targets"][name])
                raise
            h._identity(created.get("Id", ""))
            state["targets"][name]["new_id"] = created["Id"]; save(state)
            docker("/containers/" + created["Id"] + "/start", "POST")
            for _ in range(12):
                try:
                    h.require(local_get(name, PORTS[name], "/health").get("ok") is True, "Adapter not healthy")
                    break
                except Exception: time.sleep(1)
            else: raise h.DeploymentError("Candidate adapter did not become healthy")
            h.assert_replacement(old, inspect(name), image, OP, image_labels=labels)
            digest = command(["docker", "exec", name, "python", "-c",
                              "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('/app/app.py').read_bytes()).hexdigest())"]).decode().strip()
            h.require(digest == manifest["targets"][name]["app_sha256"], "Candidate app hash mismatch")
            native_adapter_health(name)
        assert_owned_admission(state)
        state["phase"] = "verified_before_reopen"; save(state); committed = True
        write_conf(original, gated)
        h.require(DRAIN.read_bytes() == MARKER, "Owned drain changed during release")
        DRAIN.unlink()
        state["phase"] = "deployed"; state["completed_at"] = int(time.time()); save(state)
        print(json.dumps({"adapters_deployed": list(PORTS), "video_unchanged": True, "native_unchanged": True,
                          "prices_changed": False, "paid_requests": 0}))
    except Exception as primary:
        if committed:
            state["phase"] = "post_commit_recovery_required"; save(state)
            raise h.DeploymentError("Verified adapters retained; inspect ingress without unsafe restarts") from primary
        try:
            for name, target in reversed(list(state["targets"].items())):
                old = before[name]
                if target.get("create_intent") and not target["new_id"]:
                    reconcile_create(name, state, manifest["targets"][name])
                if target["new_id"]:
                    retain_failed_candidate(name, target, manifest, state)
                current = inspect(old["Id"])
                h.require(current["Id"] == old["Id"] and current["Image"] == old["Image"]
                          and current["Name"] in {"/" + name, "/" + name + "-rollback190-" + OP[:12]}, "Retained original ownership changed")
                if current["Name"] != "/" + name:
                    docker("/containers/" + old["Id"] + "/rename?name=" + name, "POST")
                for network, endpoint in h.network_endpoints(old).items():
                    if network not in current["NetworkSettings"]["Networks"]:
                        docker("/networks/" + network + "/connect", "POST", {"Container": old["Id"], "EndpointConfig": endpoint})
                docker("/containers/" + old["Id"] + "/start", "POST")
                verify_restored_adapter(name, old)
            # Always validate gate ownership and refresh nginx DNS. An external
            # restore/edit is not permission to silently release our drain.
            assert_owned_admission(state)
            write_conf(original, gated)
            h.require(DRAIN.read_bytes() == MARKER, "Owned drain changed")
            DRAIN.unlink()
            state["phase"] = "rolled_back"; state["error_type"] = type(primary).__name__; save(state)
        except Exception as recovery:
            state["phase"] = "recovery_required"; save(state)
            raise h.DeploymentError("Rollback incomplete; maintenance retained and current DBs untouched") from recovery
        raise primary

if __name__ == "__main__":
    run()
