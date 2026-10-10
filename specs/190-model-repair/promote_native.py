"""Linux-only, server-local native image-policy promotion with retained rollback.

Only the frozen native image replaces the existing native process. This script
never posts generation, changes a tariff/channel/wallet, replays a task, rewrites
video configuration, stops a video service or restores financial stores.

The exact original main.go is NOT graceful: SIGTERM can interrupt accepted work.
The modern checkout's srv.Shutdown is not evidence for this older production
binary. This script therefore DEFAULT BLOCKS until fresh human maintenance
approval and a reviewed native-wide no-submit nginx artifact/route audit exist.
The public-video dependency profile must be captured from its exact deployed
source, not inferred from a different checkout. Native restart may temporarily
return503 to dependent reads/callbacks. This is NOT zero-downtime. Quiet samples
are not a universal proof against all concurrent writers.

The private native-candidate.json must bind exact source, binary and public entry
assets. Fresh source/dependency attestation and reviewer approval are mandatory.
Interrupted journals are never automatically resumed. After admission proof,
SIGTERM is sent once; no timeout SIGKILL is used. Unknown ownership/activity
preserves maintenance. No maintenance approval file is created by this script.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping

import deploy_image_fixes as h
import promote_adapters as a

OP = a.OP
ROOT = a.ROOT
STATE = ROOT / "native-rollout.json"
MANIFEST = ROOT / "native-candidate.json"
APPROVAL = ROOT / "native-maintenance-approval.json"
GATED_CONF = ROOT / "native-nginx.gated.conf"
CONF = a.CONF
DRAIN = a.DRAIN
MARKER = ("issue190-native-" + OP + "\n").encode()
NGINX = a.NGINX
PG = a.PG
CANDIDATE_IMAGE = "sha256:9e48fbe061747ae79d83eedfc3ddb2b1562930ec5d6ea45d79ce53dc8c0cbf91"
POLICY_SHA256 = "ef1fca981ad12322fa5c0d286458694f2214086e7a4d6069617ea6449628fff3"
PRICING_SHA256 = "cf2e03904549ffc6d596e1c86647ca4ab29dbeeda4c71d8bad6fb41afea40b74"
MAIN_SHA256 = "d1da45d1f933f6d27ecef5dba511ae2724381c13ce47c81bd339af1a7aaa97ec"
NATIVE_ROUTER_SHA256 = "5dbf9bf9839d9bd7266c560477dcf6ec235292ed52a4dfb0529b063ae45c723a"
APPROVAL_SCOPE = "native-wide-no-submit-maintenance-with-video-create-pause"
VIDEO_CONTAINERS = ("xtai-public-video-catalog176", "xtai-video-public-execution",
                    "xtai-video-job-gateway-v2-production", "xtai-video-job-gateway-video-job-gateway-1")
VIDEO_STORES = {
    "public": Path("/opt/xtai/state/public-video-execution/data/video-jobs.sqlite3"),
    "v2": Path("/opt/xtai/state/video-billing-v2-production/data/video-jobs.sqlite3"),
    "legacy": Path("/opt/xtai/state/video-job-gateway/data/video-jobs.sqlite3"),
}
IMAGE_STORE = Path("/opt/xtai-image-job-gateway/data/image-jobs.sqlite3")
FAILED_NAME = h.NATIVE + "-failed190-native-" + OP[:12]
ROLLBACK_NAME = h.NATIVE + "-rollback190-native-" + OP[:12]
VERSION_NAME = "xtai-native190-version-" + OP[:12]
ASSET_PATH = re.compile(r"/(?:assets/[A-Za-z0-9_./-]+\.(?:js|css)|static/(?:js/[A-Za-z0-9_.-]+\.js|css/[A-Za-z0-9_.-]+\.css))")
GATE_PROBES = a.GATE_PROBES + tuple(("POST", path) for path in (
    "/v1/embeddings", "/v1/rerank", "/v1/moderations", "/v1/audio/transcriptions",
    "/v1/audio/translations", "/v1/audio/speech", "/v1/engines/issue190-probe/embeddings",
    "/v1/models/issue190-probe:generateContent", "/v1beta/models/issue190-probe:generateContent",
    "/mj/submit/imagine", "/fast/mj/submit/imagine", "/relax/mj/submit/imagine",
    "/mj/insight-face/swap", "/mj/submit/upload-discord-images", "/suno/submit/music",
    "/v1/videos", "/v1/video-jobs")) + (("GET", "/v1/realtime"),)


def save(state: Mapping[str, object]) -> None:
    """Atomically journal private intent/outcome in an independent0600 file."""
    stage = ROOT / "native-rollout.tmp"
    fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(state, handle); handle.flush(); os.fsync(handle.fileno())
    os.replace(stage, STATE)
    directory = os.open(ROOT, os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)


def private_json(path: Path) -> dict[str, object]:
    """Read bounded, root-owned private evidence without following linked ancestry."""
    h.require(path.is_file() and not path.is_symlink() and path.resolve() == path.absolute(),
              "Protected regular evidence file is unavailable")
    info = path.stat()
    h.require(info.st_uid == 0 and info.st_mode & 0o077 == 0 and info.st_size <= 2 * 1024 * 1024,
              "Evidence must be root-owned0600 and bounded")
    try: result = json.loads(path.read_bytes())
    except ValueError: raise h.DeploymentError("Private evidence JSON is malformed") from None
    h.require(isinstance(result, dict), "Private evidence object is required")
    return result


def validate_manifest(manifest: Mapping[str, object], before: Mapping[str, object]) -> None:
    """Validate frozen exact native identities and GET-only UI evidence."""
    h.require(manifest.get("operation") == OP and manifest.get("before_id") == before.get("Id")
              and manifest.get("before_image") == before.get("Image"), "Fresh native identity differs")
    h._identity(str(manifest.get("before_id", "")))
    h._identity(str(manifest.get("before_image", "")), image=True)
    h.require(manifest.get("candidate_image") == CANDIDATE_IMAGE
              and manifest.get("policy_sha256") == POLICY_SHA256
              and manifest.get("pricing_source_sha256") == PRICING_SHA256
              and manifest.get("original_main_sha256") == MAIN_SHA256,
              "Candidate/source scope differs from reviewed native-only image policy")
    h._identity(str(manifest.get("binary_sha256", "")))
    version = manifest.get("version")
    empty_attestation = {"serving_status_version": "", "frozen_source_version_sha256": hashlib.sha256(b"").hexdigest()}
    h.require(isinstance(version, str) and (re.fullmatch(r"[A-Za-z0-9_.+-]{1,128}", version) is not None
              or (version == "" and manifest.get("explicit_empty_version_attestation") == empty_attestation)),
              "Frozen version evidence is missing")
    assets = manifest.get("public_assets")
    h.require(isinstance(assets, dict) and "/" in assets and 2 <= len(assets) <= 32,
              "Complete public entry/index asset evidence is required")
    for path, digest in assets.items():
        h.require(isinstance(path, str) and (path == "/" or ASSET_PATH.fullmatch(path) is not None)
                  and ".." not in path and "//" not in path, "Only frozen GET-only entry JS/CSS paths are permitted")
        h._identity(str(digest))
    for key in ("original_nginx_sha256", "gated_nginx_sha256", "dependency_evidence_sha256", "route_audit_sha256"):
        h._identity(str(manifest.get(key, "")))
    h.require(manifest.get("admission_coverage") == "operator-reviewed-all-native-mutating-and-generation-entrypoints",
              "Text/image-only gates cannot protect this non-graceful native runtime")


def assert_approval(approval: Mapping[str, object], *, now: float) -> None:
    """Require current explicit human approval; unanswered questions grant nothing."""
    h.require(approval.get("operation") == OP and approval.get("decision") == "A"
              and approval.get("scope") == APPROVAL_SCOPE and approval.get("source") == "direct-human-user-reply",
              "Explicit human maintenance approval is required; native promotion stays blocked")
    approved = approval.get("approved_at"); expires = approval.get("expires_at")
    h.require(type(approved) in {int, float} and type(expires) in {int, float}
              and 0 <= now - approved <= 3600 and now < expires <= approved + 3600,
              "Maintenance approval is stale/future-dated or outside its one-hour window")


def assert_deployment_evidence(manifest: Mapping[str, object], video: Mapping[str, Mapping[str, object]]) -> None:
    """Bind operator-reviewed source/dependency/route audit to this exact release.

    These root-only artifacts are produced after the operator reviews live source,
    router and callback preservation. This script does not fabricate an audit from
    booleans or treat fixture tests as proof about the deployed runtime.
    """
    main = h.LIVE_NATIVE_SOURCE / "main.go"
    h.require(main.is_file() and not main.is_symlink() and hashlib.sha256(main.read_bytes()).hexdigest() == MAIN_SHA256,
              "Exact non-graceful production native source profile differs")
    router = h.LIVE_NATIVE_SOURCE / "router/relay-router.go"
    h.require(router.is_file() and not router.is_symlink()
              and hashlib.sha256(router.read_bytes()).hexdigest() == NATIVE_ROUTER_SHA256,
              "Exact native paid route inventory differs from maintenance review")
    for filename, key in (("native-dependency-evidence.json", "dependency_evidence_sha256"),
                          ("native-route-audit.json", "route_audit_sha256")):
        path = ROOT / filename
        evidence = private_json(path)
        h.require(hashlib.sha256(path.read_bytes()).hexdigest() == manifest[key]
                  and evidence.get("operation") == OP and evidence.get("original_main_sha256") == MAIN_SHA256
                  and evidence.get("native_candidate_image") == CANDIDATE_IMAGE,
                  "Exact native dependency/route evidence is missing or mismatched")
        h.require(evidence.get("video_instances") == {name: info["Id"] for name, info in video.items()},
                  "Reviewed video dependency identities differ from currently serving processes")
        h.require(evidence.get("review_status") == "complete" and evidence.get("unaddressed_findings") == 0,
                  "Native-wide route/dependency audit is not complete")


def probe_maintenance() -> None:
    """Require actual503/no-submit for all representative native paid/create paths.

    These anonymous empty requests do not prove an unknown router's exhaustiveness;
    exact live router audit is independently required before admission mutation.
    They never carry user/API credentials or prompt/material/paid test payloads.
    """
    for method, path in GATE_PROBES:
        connection = a.LocalNginxTLS("api.aixingtuyun.com", timeout=3)
        try:
            connection.request(method, path, body=b"{}" if method == "POST" else None,
                               headers={"Content-Type": "application/json", "Host": "api.aixingtuyun.com"})
            response = connection.getresponse(); raw = response.read(16385)
            h.require(len(raw) <= 16384, "Native admission probe exceeded its bound")
            try: body = json.loads(raw)
            except ValueError: raise h.DeploymentError("Native admission response is not proven") from None
            headers = {key.lower(): value for key, value in response.getheaders()}
            error = body.get("error") if isinstance(body, dict) else None
            h.require(response.status == 503 and headers.get("x-xingtu-image-submission-state") == "not_submitted"
                      and bool(headers.get("x-xingtu-relay-request-id")) and isinstance(error, dict)
                      and error.get("code") == "relay_maintenance" and OP in str(error.get("message", "")),
                      "Actual native-wide no-submit admission gate is not active/owned")
        finally: connection.close()


def version_payload(manifest: Mapping[str, object]) -> dict[str, object]:
    """Create an inert --version payload, disconnected from all live stores/networks."""
    return {"Image": manifest["candidate_image"], "Entrypoint": ["/new-api"], "Cmd": ["--version"],
            "Env": [], "WorkingDir": "/tmp", "Labels": {h.OP_LABEL: OP},
            "HostConfig": {"NetworkMode": "none", "Binds": [], "ReadonlyRootfs": True,
                           "RestartPolicy": {"Name": "no"}, "CapDrop": ["ALL"],
                           "SecurityOpt": ["no-new-privileges:true"], "Memory": 268435456,
                           "PidsLimit": 32}}


def verify_inert_version(state: dict[str, object], manifest: Mapping[str, object]) -> None:
    """Verify the pinned binary can execute --version with no financial connection."""
    h.require(a.inspect_optional(VERSION_NAME) is None, "Existing version candidate requires manual reconciliation")
    state["version_create_intent"] = True; save(state)
    created = a.docker("/containers/create?name=" + VERSION_NAME, "POST", version_payload(manifest))
    identity = h._identity(created.get("Id", "")); state["version_id"] = identity; save(state)
    a.docker("/containers/" + identity + "/start", "POST")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        info = a.inspect(identity)
        h.require(info.get("Id") == identity and info.get("Image") == CANDIDATE_IMAGE
                  and info.get("Name") == "/" + VERSION_NAME
                  and info.get("Config", {}).get("Labels", {}).get(h.OP_LABEL) == OP,
                  "Inert version candidate ownership changed")
        if info.get("State", {}).get("Running") is False:
            h.require(info["State"].get("ExitCode") == 0, "Inert candidate --version failed")
            output = a.command(["docker", "logs", identity], timeout=5)
            h.require(output.strip() == str(manifest["version"]).encode(), "Candidate version differs from freeze")
            state["version_verified"] = True; save(state)
            # Only this exact stopped, owned, no-mount fixture is removed.
            a.docker("/containers/" + identity + "?v=false", "DELETE")
            state["version_fixture_removed"] = True; save(state)
            return
        time.sleep(0.25)
    raise h.DeploymentError("Inert candidate state is unknown; no production promotion")


def native_get(identity: str, path: str, limit: int = 16 * 1024 * 1024) -> tuple[int, bytes]:
    """Read fixed native status or frozen UI paths; no credentials/redirects/proxies."""
    h._identity(identity)
    h.require(path == "/api/status" or path == "/" or
              (ASSET_PATH.fullmatch(path) is not None
               and ".." not in path and "//" not in path), "Unapproved native GET probe")
    info = a.inspect(identity)
    network = info.get("NetworkSettings", {}).get("Networks", {}).get("app-net", {})
    address = str(network.get("IPAddress", ""))
    h.require(ipaddress.ip_address(address).is_private, "Native private app network is unavailable")
    request = urllib.request.Request("http://" + address + ":3000" + path, method="GET")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=5) as response:
            body = response.read(limit + 1)
            h.require(len(body) <= limit, "Native response exceeded its bound")
            return response.status, body
    except urllib.error.HTTPError as error:
        error.close()
        raise h.DeploymentError("Native status/UI GET is not verified") from None


def assert_financial_mode(native: Mapping[str, object], status: Mapping[str, object]) -> None:
    """Never replace a native process holding deferred quota writes in memory."""
    config = native.get("Config")
    h.require(isinstance(config, dict), "Native configuration is unavailable")
    h._environment(config)
    environment = dict(row.split("=", 1) for row in config.get("Env") or [])
    h.require(environment.get("QUOTA_DB_AUTHORITATIVE") in {"true", "1"}
              and environment.get("BATCH_UPDATE_ENABLED") in {"false", "0"},
              "Native quota mode must explicitly use the database without batching")
    data = status.get("data")
    h.require(status.get("success") is True and isinstance(data, dict)
              and data.get("quota_db_authoritative") is True and data.get("enable_batch_update") is False,
              "Serving native quota mode differs from protected runtime evidence")


def verify_native_http(identity: str, manifest: Mapping[str, object]) -> None:
    """Verify serving quota authority and identical frontend, without generating."""
    status, raw = native_get(identity, "/api/status")
    h.require(status == 200, "Native status is not healthy")
    try: payload = json.loads(raw)
    except ValueError: raise h.DeploymentError("Native status JSON cannot be verified") from None
    h.require(isinstance(payload, dict), "Native status object is unavailable")
    assert_financial_mode(a.inspect(identity), payload)
    h.require(payload["data"].get("version") == manifest["version"], "Serving native version differs from freeze")
    if manifest["version"] == "":
        source_version = h.LIVE_NATIVE_SOURCE / "VERSION"
        h.require(source_version.is_file() and not source_version.is_symlink()
                  and source_version.resolve() == source_version.absolute()
                  and hashlib.sha256(source_version.read_bytes()).hexdigest()
                  == manifest["explicit_empty_version_attestation"]["frozen_source_version_sha256"],
                  "Explicit empty source version attestation differs")
    for path, expected in manifest["public_assets"].items():
        code, content = native_get(identity, path)
        h.require(code == 200 and hashlib.sha256(content).hexdigest() == expected,
                  "Native frontend differs from frozen live public entry assets")


def assert_video_unchanged(before: Mapping[str, Mapping[str, object]]) -> None:
    """Keep exact four video instances/config/mounts/network attachments untouched."""
    h.require(set(before) == set(VIDEO_CONTAINERS), "Video dependency inventory is incomplete")
    for name, old in before.items():
        current = a.inspect(name)
        for key in ("Id", "Image", "Name", "Config", "HostConfig"):
            h.require(current.get(key) == old.get(key), "Video instance/config changed during native operation")
        h.require(current.get("State", {}).get("Running") is True,
                  "Existing video dependency is not running")
        h.require(h._canonical_mounts(current.get("Mounts")) == h._canonical_mounts(old.get("Mounts"))
                  and h.network_endpoints(current) == h.network_endpoints(old),
                  "Existing video storage/network profile changed")


def pending_sqlite(path: Path, *, image: bool = False) -> int:
    """Read a known store with fixed count SQL; unknown/missing is never zero."""
    h.require(path.is_file() and not path.is_symlink() and path.resolve() == path.absolute(),
              "Known task ledger is missing/linked")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("PRAGMA query_only=ON")
        sql = ("SELECT count(*) FROM image_jobs WHERE status IN ('queued','submitting','running')" if image else
               "SELECT count(*) FROM video_jobs WHERE status NOT IN ('succeeded','failed') OR billing_status NOT IN ('settled','refunded') OR status IS NULL OR billing_status IS NULL")
        value = connection.execute(sql).fetchone()[0]
        h.require(type(value) is int and value >= 0, "Task pending count is unknown")
        return value
    finally: connection.close()


def pending_postgres() -> dict[str, int]:
    """Fixed SELECT-only native/public-video unsettled counts; no account fields."""
    sql = ("SELECT json_build_object('public_video_unsettled',"
           "(SELECT count(*) FROM public_video_tasks WHERE state IS NULL OR state <> 'settled'),"
           "'native_tasks_unsettled',(SELECT count(*) FROM tasks WHERE status IS NULL OR status NOT IN ('SUCCESS','FAILURE')))::text;")
    raw = a.command(["docker", "exec", PG, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "newapi", "-d", "new-api", "-At", "-c", sql], timeout=10)
    h.require(len(raw) <= 4096, "Pending evidence exceeded its bound")
    try: result = json.loads(raw)
    except ValueError: raise h.DeploymentError("Native/public-video pending evidence is unavailable") from None
    h.require(isinstance(result, dict) and set(result) == {"public_video_unsettled", "native_tasks_unsettled"}
              and all(type(value) is int and value >= 0 for value in result.values()), "Pending evidence is malformed")
    return result


def native_connections(identity: str) -> int:
    """Count established port3000 connections in the owned process namespace."""
    h._identity(identity)
    raw = a.command(["docker", "exec", identity, "sh", "-c",
                     "awk 'FNR>1 && $2 ~ /:0BB8$/ && $4 == \"01\" {n++} END {print n+0}' /proc/net/tcp /proc/net/tcp6"], timeout=5)
    h.require(re.fullmatch(rb"[0-9]+\n?", raw) is not None, "Native connection count is unknown")
    return int(raw)


def assert_owned_admission(state: Mapping[str, object], *, native_identity: str | None = None) -> None:
    """Revalidate owned no-submit ingress/drain, identities and video preservation."""
    h.require(not CONF.is_symlink() and hashlib.sha256(CONF.read_bytes()).hexdigest() == state["gated_nginx_sha256"],
              "Native admission configuration changed externally")
    h.require(not DRAIN.is_symlink() and DRAIN.read_bytes() == MARKER, "Native drain belongs to another operation")
    for name, key in ((NGINX, "nginx_id"), (h.IMAGE_GATEWAY, "gateway_id")):
        info = a.inspect(name)
        h.require(info.get("Id") == state[key] and info.get("State", {}).get("Running") is True,
                  "Admission gateway identity changed")
    ready = a.local_get(h.IMAGE_GATEWAY, 8090, "/ready")
    h.require(ready.get("draining") is True and ready.get("accepting") is False, "Image drain was not acknowledged")
    probe_maintenance()
    assert_video_unchanged(state["video_before"])
    if native_identity is not None:
        info = a.inspect(native_identity)
        h.require(info.get("Id") == native_identity and info.get("State", {}).get("Running") is True,
                  "Serving native identity is unavailable")
        a.assert_internal_submitters(info)


def idle_sample(state: Mapping[str, object], identity: str) -> dict[str, object]:
    """Produce independent admission/socket/ledger evidence immediately before TERM."""
    assert_owned_admission(state, native_identity=identity)
    activity = a.command(["docker", "exec", NGINX, "wget", "-qO-", "http://127.0.0.1:18090/"], timeout=5)
    match = re.search(rb"Reading: (\d+) Writing: (\d+)", activity)
    h.require(match is not None, "Nginx activity is unknown")
    # At most one Writing is the stub-status request itself. Native port3000
    # separately must have zero ESTABLISHED connections, not merely one writer.
    return {"image_jobs_active": pending_sqlite(IMAGE_STORE, image=True),
            "video_jobs_unsettled": {key: pending_sqlite(path) for key, path in VIDEO_STORES.items()},
            **pending_postgres(), "native_tcp_connections": native_connections(identity),
            "native_reading": int(match[1]), "native_writing": int(match[2]),
            "admission_verified": True, "internal_submitters_excluded": True}


def assert_idle_sample(sample: Mapping[str, object]) -> None:
    """Reject missing, busy or malformed evidence instead of treating it as idle."""
    for key in ("image_jobs_active", "native_tcp_connections", "native_reading",
                "public_video_unsettled", "native_tasks_unsettled"):
        h.require(type(sample.get(key)) is int and sample[key] == 0, "Accepted or unknown native/task work remains")
    h.require(type(sample.get("native_writing")) is int and 0 <= sample["native_writing"] <= 1,
              "Native ingress requests have not drained")
    video = sample.get("video_jobs_unsettled")
    h.require(isinstance(video, dict) and set(video) == set(VIDEO_STORES)
              and all(type(value) is int and value == 0 for value in video.values()), "Video unsettled evidence prevents native promotion")
    h.require(sample.get("admission_verified") is True and sample.get("internal_submitters_excluded") is True,
              "Owned admission/internal submitter proof is missing")


def assert_quiet(state: Mapping[str, object], identity: str) -> dict[str, object]:
    """Require three fresh consecutive observations, bounded to45seconds."""
    samples: list[dict[str, object]] = []
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        try:
            sample = idle_sample(state, identity); assert_idle_sample(sample)
            samples = (samples + [sample])[-3:]
            if len(samples) == 3:
                return {"checked_at": time.time(), "operation": OP, "samples": samples}
        except (h.DeploymentError, OSError, sqlite3.Error, urllib.error.URLError):
            samples = []
        time.sleep(1)
    raise h.DeploymentError("Native/task drain did not become proven quiet; no native stop")


def assert_process_profile(info: Mapping[str, object], expected: Mapping[str, object], *,
                           names: set[str]) -> None:
    """Keep the complete private process profile fixed across a stop observation."""
    h.require(info.get("Id") == expected.get("Id") and info.get("Image") == expected.get("Image")
              and info.get("Name") in names, "Native process ownership drifted during stop proof")
    for key in ("Config", "HostConfig"):
        h.require(info.get(key) == expected.get(key), "Native private runtime drifted during stop proof")
    h.require(h._canonical_mounts(info.get("Mounts")) == h._canonical_mounts(expected.get("Mounts")),
              "Native financial mounts drifted during stop proof")


def process_state(info: Mapping[str, object], *, restart_count: int,
                  allow_created: bool = False) -> str:
    """Never mistake missing/dead/restarting state for an exited process.

    No restart-policy mutation is made. The expected counter is pinned before
    TERM (zero for a freshly created replacement), not adopted after a restart.
    Created is permitted only for an owned candidate proven never to have run;
    it grants retention, never permission to start that failed candidate.
    """
    count = info.get("RestartCount"); status = info.get("State")
    h.require(type(restart_count) is int and restart_count >= 0
              and type(count) is int and count == restart_count,
              "Native restart count changed or cannot be proven")
    h.require(isinstance(status, dict) and type(status.get("Running")) is bool
              and status.get("Restarting") is False and type(status.get("Pid")) is int
              and status["Pid"] >= 0, "Native process state is unknown or restarting")
    if status.get("Status") == "running":
        h.require(status["Running"] is True and status["Pid"] > 0,
                  "Native running state is inconsistent")
        return "running"
    h.require(status["Running"] is False and status["Pid"] == 0,
              "Native stopped state is inconsistent")
    if status.get("Status") == "exited": return "exited"
    if allow_created and status.get("Status") == "created":
        h.require(count == 0 and status.get("StartedAt") == "0001-01-01T00:00:00Z",
                  "Created candidate has prior start/restart evidence")
        return "created"
    raise h.DeploymentError("Native process has no proven exited/never-started state")


def await_stopped(identity: str, expected: Mapping[str, object], *, names: set[str],
                  restart_count: int, allow_created: bool = False,
                  timeout: float = 150) -> Mapping[str, object]:
    """Read two consecutive stopped observations; never signal or escalate.

    A process running again after the first exited/created sample is drift, not
    a reason to reset the evidence and send another TERM. Unknown observations
    immediately preserve maintenance instead of becoming an invented stop.
    """
    deadline = time.monotonic() + timeout; stopped: str | None = None
    while time.monotonic() < deadline:
        info = a.inspect(identity); assert_process_profile(info, expected, names=names)
        mode = process_state(info, restart_count=restart_count, allow_created=allow_created)
        if mode == "running":
            h.require(stopped is None, "Native restarted after a stopped observation; preserve maintenance")
        elif stopped is not None:
            h.require(mode == stopped, "Native stopped state changed during proof; preserve maintenance")
            return info
        else:
            stopped = mode
        time.sleep(0.5)
    raise h.DeploymentError("Native stop did not become proven; current stores retained, no second signal or SIGKILL")


def candidate_profile(state: Mapping[str, object], manifest: Mapping[str, object]) -> dict[str, object]:
    """Bind rollback observations to the exact frozen create profile, not new drift."""
    payload = h.clone_create_config(state["before"], manifest["candidate_image"], OP,
        image_labels={"com.aixingtuyun.image-fixes-policy-sha256": manifest["policy_sha256"]})
    return {"Id": state["new_id"], "Image": manifest["candidate_image"],
            "Config": {key: value for key, value in payload.items() if key not in {"HostConfig", "NetworkingConfig"}},
            "HostConfig": payload["HostConfig"], "Mounts": state["before"]["Mounts"]}


def terminate_original(state: dict[str, object]) -> None:
    """Terminate once and prove exited/nonrestarting; runtime is NOT graceful."""
    h.require(not state.get("terminate_intent"), "Prior TERM intent forbids another signal; reconcile privately")
    old = state["before"]; count = old.get("RestartCount")
    h.require(process_state(old, restart_count=count) == "running", "Original native was not proven running")
    state["terminate_intent"] = True; save(state)
    try: a.docker("/containers/" + old["Id"] + "/kill?signal=SIGTERM", "POST")
    except Exception:
        state["terminate_outcome_unknown"] = True; save(state)
    await_stopped(old["Id"], old, names={old["Name"]}, restart_count=count)
    state["original_stopped"] = True; state["original_exit_observations"] = 2; save(state)


def assert_candidate_owned(info: Mapping[str, object], manifest: Mapping[str, object]) -> None:
    """Match exact native image/source/operation identity, including retained failures."""
    h._identity(str(info.get("Id", "")))
    labels = info.get("Config", {}).get("Labels") or {}
    h.require(info.get("Name") in {"/" + h.NATIVE, "/" + FAILED_NAME}
              and info.get("Image") == manifest["candidate_image"] and labels.get(h.OP_LABEL) == OP
              and labels.get("com.aixingtuyun.image-fixes-policy-sha256") == manifest["policy_sha256"],
              "Native candidate is not owned by the frozen operation")


def reconcile_create(state: dict[str, object], manifest: Mapping[str, object]) -> str | None:
    """Resolve a lost create response by exact identity, never create a second native."""
    info = a.inspect_optional(h.NATIVE)
    if info is None or info.get("Id") == state["before"]["Id"]: return None
    assert_candidate_owned(info, manifest)
    h.require(state.get("new_id") in (None, info["Id"]), "Native create journal identity conflicts")
    state["new_id"] = info["Id"]; state["create_reconciled"] = True; save(state)
    return info["Id"]


def assert_replacement_idle(state: Mapping[str, object], manifest: Mapping[str, object]) -> None:
    """Never terminate an unknown/busy replacement during rollback."""
    identity = state["new_id"]; expected = candidate_profile(state, manifest)
    deadline = time.monotonic() + 45; quiet = 0; stopped: str | None = None
    while time.monotonic() < deadline:
        info = a.inspect(identity); assert_candidate_owned(info, manifest)
        assert_process_profile(info, expected, names={"/" + h.NATIVE, "/" + FAILED_NAME})
        mode = process_state(info, restart_count=0, allow_created=True)
        assert_owned_admission(state)
        if mode != "running":
            if stopped is not None:
                h.require(mode == stopped, "Replacement stopped state changed; preserve maintenance")
                return
            stopped = mode; time.sleep(0.5); continue
        h.require(stopped is None, "Replacement ran after a stopped observation; preserve maintenance")
        a.assert_internal_submitters(info)
        quiet = quiet + 1 if native_connections(identity) == 0 else 0
        if quiet == 3: return
        time.sleep(1)
    raise h.DeploymentError("Replacement still has unknown/active requests; preserve maintenance")


def retain_failed_candidate(state: dict[str, object], manifest: Mapping[str, object]) -> None:
    """Retain only a proven idle owned candidate; do not revert any mounted data."""
    assert_replacement_idle(state, manifest)
    identity = state["new_id"]; expected = candidate_profile(state, manifest)
    info = a.inspect(identity); assert_candidate_owned(info, manifest)
    names = {"/" + h.NATIVE, "/" + FAILED_NAME}
    assert_process_profile(info, expected, names=names)
    mode = process_state(info, restart_count=0, allow_created=True)
    if mode == "running":
        h.require(not state.get("rollback_term_intent"), "Prior rollback TERM intent forbids another signal")
        state["rollback_term_intent"] = True; save(state)
        try: a.docker("/containers/" + identity + "/kill?signal=SIGTERM", "POST")
        except Exception:
            state["rollback_term_unknown"] = True; save(state)
        info = await_stopped(identity, expected, names=names, restart_count=0)
    else:
        info = await_stopped(identity, expected, names=names, restart_count=0, allow_created=True)
    h.require(process_state(info, restart_count=0, allow_created=True) != "running", "Replacement stop is unconfirmed")
    a.docker("/containers/" + identity + "/rename?name=" + FAILED_NAME, "POST")
    for network in a.inspect(identity)["NetworkSettings"]["Networks"]:
        a.docker("/networks/" + network + "/disconnect", "POST", {"Container": identity, "Force": False})


def restore_original(state: dict[str, object], manifest: Mapping[str, object]) -> None:
    """Restore the retained exact instance/config; financial files remain current."""
    if state.get("terminate_intent") and not state.get("original_stopped"):
        # A SIGTERM response/timeout does not mean the still-running process is
        # safe to release. It may exit later, dropping otherwise reopened work.
        old = state["before"]
        await_stopped(old["Id"], old, names={old["Name"], "/" + ROLLBACK_NAME}, restart_count=old.get("RestartCount"))
        state["original_stopped"] = True; state["original_exit_observations"] = 2; save(state)
    if state.get("create_intent") and not state.get("new_id"): reconcile_create(state, manifest)
    if state.get("new_id"): retain_failed_candidate(state, manifest)
    old = state["before"]; current = a.inspect(old["Id"])
    h.require(current.get("Id") == old["Id"] and current.get("Image") == old["Image"]
              and current.get("Name") in {old["Name"], "/" + ROLLBACK_NAME}, "Retained original identity drifted")
    for key in ("Config", "HostConfig"):
        h.require(current.get(key) == old.get(key), "Retained original private runtime drifted")
    h.require(h._canonical_mounts(current.get("Mounts")) == h._canonical_mounts(old.get("Mounts")),
              "Retained original financial mounts drifted")
    mode = process_state(current, restart_count=old.get("RestartCount"))
    h.require(not state.get("terminate_intent") or mode == "exited",
              "Original ran after prior TERM/stop evidence; preserve maintenance")
    if mode == "exited":
        current = await_stopped(old["Id"], old, names={old["Name"], "/" + ROLLBACK_NAME}, restart_count=old.get("RestartCount"))
    if current["Name"] != old["Name"]:
        a.docker("/containers/" + old["Id"] + "/rename?name=" + h.NATIVE, "POST")
    for network, endpoint in h.network_endpoints(old).items():
        if network not in current["NetworkSettings"]["Networks"]:
            a.docker("/networks/" + network + "/connect", "POST", {"Container": old["Id"], "EndpointConfig": endpoint})
    if mode == "exited":
        a.docker("/containers/" + old["Id"] + "/start", "POST")
    else:
        h.require(mode == "running", "Retained original running state is unknown")
    await_native(old["Id"], manifest)
    h.require(h.network_endpoints(a.inspect(old["Id"])) == h.network_endpoints(old), "Restored native DNS profile differs")


def await_native(identity: str, manifest: Mapping[str, object]) -> None:
    """Bounded free readiness/frontend verification after start or rollback."""
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try: verify_native_http(identity, manifest); return
        except (h.DeploymentError, OSError, urllib.error.URLError): time.sleep(1)
    raise h.DeploymentError("Native health/UI did not become verified within the startup bound")


def release_admission(state: Mapping[str, object], original: bytes, gated: bytes, *,
                      native_mutated: bool) -> None:
    """Release only owned configuration, distinguishing pre-stop gate failures.

    If nginx -t/reload failed before native mutation, requiring an active503 gate
    would prevent safe restoration of the still-serving original. In that case
    exact owned config bytes/drain and untouched native/video identities suffice.
    After native termination/create, actual gate proof remains mandatory.
    """
    h.require(not CONF.is_symlink() and not DRAIN.is_symlink() and DRAIN.read_bytes() == MARKER,
              "Owned native admission changed; preserve foreign state")
    current = CONF.read_bytes()
    h.require(current in (original, gated), "Foreign nginx edits cannot be overwritten")
    assert_video_unchanged(state["video_before"])
    if native_mutated:
        assert_owned_admission(state, native_identity=state.get("new_id") or state["before"]["Id"])
    a.write_conf(original, current)  # reload even original bytes to refresh Docker DNS
    h.require(not DRAIN.is_symlink() and DRAIN.read_bytes() == MARKER, "Owned native drain changed during reopen")
    DRAIN.unlink()


def run_locked() -> None:
    """Promote once under a private operation lock; never resume an unknown journal."""
    h.require(not STATE.exists() and not STATE.is_symlink(), "Existing native journal requires manual reconciliation")
    # Deliberately BEFORE Docker/config/drain access: a pending question is not
    # approval, and absence cannot be turned into inferred deployment authority.
    assert_approval(private_json(APPROVAL), now=time.time())
    manifest = private_json(MANIFEST)
    old = a.inspect(h.NATIVE); validate_manifest(manifest, old)
    a.assert_private_ingress(old); a.assert_internal_submitters(old)
    verify_native_http(old["Id"], manifest)
    for name in (ROLLBACK_NAME, FAILED_NAME):
        h.require(a.inspect_optional(name) is None, "Existing retained native name requires reconciliation")
    candidate = a.docker("/images/" + manifest["candidate_image"] + "/json")
    labels = candidate.get("Config", {}).get("Labels") or {}
    h.require(candidate.get("Id") == manifest["candidate_image"]
              and labels.get("com.aixingtuyun.image-fixes-policy-sha256") == POLICY_SHA256,
              "Native immutable image/source attestation differs")
    payload = h.clone_create_config(old, manifest["candidate_image"], OP, image_labels=labels)
    # Reject an existing drain/config collision before any admission mutation.
    h.require(not DRAIN.exists() and not DRAIN.is_symlink(), "Another operation owns image admission")
    original = CONF.read_bytes()
    h.require(not CONF.is_symlink() and b"18090" not in original and b"issue190-native" not in original,
              "Existing native maintenance identity collision")
    h.require(hashlib.sha256(original).hexdigest() == manifest["original_nginx_sha256"], "Reviewed original nginx drifted")
    h.require(GATED_CONF.is_file() and not GATED_CONF.is_symlink() and GATED_CONF.resolve() == GATED_CONF.absolute()
              and GATED_CONF.stat().st_uid == 0 and GATED_CONF.stat().st_mode & 0o077 == 0
              and GATED_CONF.stat().st_size <= 1024 * 1024,
              "Root-owned reviewed native-wide gated nginx artifact is missing")
    gated = GATED_CONF.read_bytes()
    h.require(hashlib.sha256(gated).hexdigest() == manifest["gated_nginx_sha256"]
              and ("issue190-native-" + OP).encode() in gated and b"listen 127.0.0.1:18090" in gated,
              "Native-wide gated nginx artifact differs from reviewed proof")
    video = {name: a.inspect(name) for name in VIDEO_CONTAINERS}; assert_video_unchanged(video)
    assert_deployment_evidence(manifest, video)
    state: dict[str, object] = {"operation": OP, "phase": "staged", "before": old, "new_id": None,
        "video_before": video, "nginx_id": a.inspect(NGINX)["Id"], "gateway_id": a.inspect(h.IMAGE_GATEWAY)["Id"],
        "gated_nginx_sha256": hashlib.sha256(gated).hexdigest(), "original_nginx_sha256": hashlib.sha256(original).hexdigest(),
        "paid_requests": 0, "video_configuration_changed": False}
    save(state)
    verify_inert_version(state, manifest)
    fd = os.open(ROOT / "native-nginx.before.conf", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(original); output.flush(); os.fsync(output.fileno())
    # Preparation never extends the original one-hour human authorization.
    assert_approval(private_json(APPROVAL), now=time.time())
    fd = os.open(DRAIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output: output.write(MARKER); output.flush(); os.fsync(output.fileno())
    committed = False
    try:
        assert_approval(private_json(APPROVAL), now=time.time())
        a.write_conf(gated, original)
        state["phase"] = "draining"; save(state)
        state["idle"] = assert_quiet(state, old["Id"]); save(state)
        h.assert_fresh_baseline(old, a.inspect(h.NATIVE))
        assert_owned_admission(state, native_identity=old["Id"])
        # Expiry here follows the normal owned-ingress recovery path while
        # the original is still running: no TERM or native create is allowed.
        assert_approval(private_json(APPROVAL), now=time.time())
        state["phase"] = "replacing"; save(state)
        terminate_original(state)
        a.docker("/containers/" + old["Id"] + "/rename?name=" + ROLLBACK_NAME, "POST")
        for network in old["NetworkSettings"]["Networks"]:
            a.docker("/networks/" + network + "/disconnect", "POST", {"Container": old["Id"], "Force": False})
        state["create_intent"] = True; save(state)
        try: created = a.docker("/containers/create?name=" + h.NATIVE, "POST", payload)
        except Exception:
            state["create_unknown"] = True; save(state); reconcile_create(state, manifest); raise
        state["new_id"] = h._identity(created.get("Id", "")); save(state)
        a.docker("/containers/" + state["new_id"] + "/start", "POST")
        await_native(state["new_id"], manifest)
        h.assert_replacement(old, a.inspect(h.NATIVE), manifest["candidate_image"], OP, image_labels=labels)
        binary = a.command(["docker", "exec", state["new_id"], "sha256sum", "/new-api"], timeout=10).decode().split()
        h.require(len(binary) == 2 and binary[0] == manifest["binary_sha256"] and binary[1] == "/new-api",
                  "Serving native binary differs from the frozen offline build")
        assert_owned_admission(state, native_identity=state["new_id"])
        state["phase"] = "verified_before_reopen"; save(state); committed = True
        release_admission(state, original, gated, native_mutated=True)
        state["phase"] = "deployed"; state["completed_at"] = int(time.time()); save(state)
        print(json.dumps({"native_deployed": True, "video_configuration_unchanged": True,
                          "financial_stores_restored": False, "paid_requests": 0, "zero_downtime_claimed": False}))
    except Exception as primary:
        if committed:
            state["phase"] = "post_commit_recovery_required"; save(state)
            raise h.DeploymentError("Verified native retained; reconcile owned ingress without replay/unsafe restart") from primary
        try:
            restore_original(state, manifest)
            # A stopped failed candidate stays recorded, but is not serving.
            restored = dict(state, new_id=None)
            release_admission(restored, original, gated, native_mutated=bool(state.get("terminate_intent")))
            state["phase"] = "rolled_back"; state["error_type"] = type(primary).__name__; save(state)
        except Exception as recovery:
            state["phase"] = "recovery_required"; save(state)
            raise h.DeploymentError("Native rollback requires reconciliation; maintenance/current stores retained") from recovery
        raise primary


def run() -> None:
    """Explicit Linux/root entry point; all validation precedes native mutations."""
    h.require(os.name == "posix" and os.geteuid() == 0, "Native rollout requires the authorized Linux root session")
    import fcntl
    a.assert_private_root()
    lock = os.open(ROOT / "native-rollout.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_locked()
    finally: os.close(lock)


if __name__ == "__main__":
    run()
