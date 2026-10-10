"""Issue190 preparation and verification library, NOT an automatic rollout.

Only the native process and three image adapters may be replacement targets.
Image-gateway code, video configuration, ACL, routes, prices and financial state
are never written here. Functions prepare owned inert contexts and verify exact
runtime identities. The operator must separately establish a bounded no-submit
maintenance gate, an owned existing image-gateway drain and fresh idle evidence.
There is intentionally no promote/stop/restart, upstream HTTP, SSH or paid job
function. Never import/execute the historical issue183 deploy/reverify actions.

Adapter contexts derive from the inspected immutable image and retain its USER,
entrypoint and dependencies. Native compilation uses only the exact issue187
source, changing service/image_route_policy.go; its existing embedded frontend is
not rebuilt here. Missing embedded assets are a hard stop, NOT permission to use
the builder image's older frontend. Candidate builds must use --network none and
locally pinned images; a bare Go image with no module cache needs a separately
verified cache preparation before this offline compilation can succeed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import time
from collections.abc import Mapping

NATIVE = "ai-api-stack-new-api-1"
BANANA = "xtai-banana-chat-adapter"
IMAGE25 = "xtai-image25-adapter"
NODY = "xtai-nodyhub-image-adapter"
ADAPTERS = (BANANA, IMAGE25, NODY)
TARGETS = (NATIVE, *ADAPTERS)
IMAGE_GATEWAY = "xtai-image-job-gateway-image-job-gateway-1"
LIVE_NATIVE_SOURCE = pathlib.Path("/opt/ai-api-stack/releases/issue187-fable/source")
NATIVE_POLICY = "service/image_route_policy.go"
NATIVE_POLICY_BASELINE_SHA256 = "73f4cb93890457d09c6228cd0a3687671a9e66e532a2144b9d97c43994cecfa9"
IMAGE_IO_SHA256 = "7834a058614143542dd979fb57fa83d06676a4ed53beff9449c48f45381196d1"
OP_LABEL = "com.aixingtuyun.image-fixes-operation"
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCE_FILES = 100000


class DeploymentError(RuntimeError):
    """A failed safety boundary; preserve partial artifacts and live state."""


def require(condition: bool, message: str) -> None:
    """Raise sanitized evidence without exposing environment or secret values."""
    if not condition:
        raise DeploymentError(message)


def _identity(value: str, *, image: bool = False) -> str:
    pattern = r"sha256:[0-9a-f]{64}" if image else r"[0-9a-f]{64}"
    require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
            "A full immutable identity is required; mutable tags are forbidden")
    return value


def _operation(value: str) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None,
            "A fresh operation identity is required")
    return value


def _target(info: Mapping[str, object]) -> str:
    name = str(info.get("Name", "")).removeprefix("/")
    require(name in TARGETS, "Replacement target is outside the fixed four-service scope")
    return name


def _environment(config: Mapping[str, object]) -> None:
    rows = config.get("Env") or []
    require(isinstance(rows, list) and all(isinstance(row, str) and "=" in row and
            "\n" not in row and "\r" not in row for row in rows),
            "Malformed environment requires review")
    keys = [row.split("=", 1)[0] for row in rows]
    require(all(keys) and len(set(keys)) == len(keys), "Duplicate environment keys require review")


def _represented_mounts(info: Mapping[str, object]) -> None:
    host = info["HostConfig"]
    require(isinstance(host, dict), "Host configuration is missing")
    destinations: dict[str, tuple[str, bool]] = {}
    for bind in host.get("Binds") or []:
        require(isinstance(bind, str), "Invalid explicit bind")
        parts = bind.split(":")
        require(len(parts) in (2, 3) and parts[1].startswith("/"), "Unexpected bind syntax requires review")
        require(parts[1] not in destinations, "Duplicate explicit mount destination requires review")
        flags = parts[2].split(",") if len(parts) == 3 else []
        require(not ({"ro", "rw"} <= set(flags)), "Conflicting mount write flags")
        destinations[parts[1]] = (parts[0], "ro" not in flags)
    for mount in host.get("Mounts") or []:
        require(isinstance(mount, dict) and isinstance(mount.get("Target"), str), "Invalid explicit mount")
        require(mount["Target"] not in destinations, "Duplicate explicit mount destination requires review")
        destinations[mount["Target"]] = (str(mount.get("Source", "")), mount.get("ReadOnly") is not True)
    for mount in info.get("Mounts") or []:
        require(isinstance(mount, dict), "Runtime mount identity is missing")
        # Recreating Config.Volumes alone allocates NEW anonymous volumes. Never
        # call that preservation; require an existing explicit bind/mount first.
        if mount.get("Type") in {"bind", "volume"}:
            require(mount.get("Destination") in destinations,
                    "Persistent runtime mount is not explicitly represented; refuse empty replacement storage")
            source, writable = destinations[mount["Destination"]]
            require(source == (mount.get("Name") if mount.get("Type") == "volume" else mount.get("Source"))
                    and type(mount.get("RW")) is bool and mount["RW"] is writable,
                    "Persistent runtime mount source/write mode differs from create configuration")
        else:
            require(mount.get("Type") == "tmpfs" and
                    (mount.get("Destination") in (host.get("Tmpfs") or {}) or mount.get("Destination") in destinations),
                    "Unsupported/unrepresented runtime mount type requires review")


def network_endpoints(info: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Retain aliases/static IPAM/driver/links; strip only old automatic ID aliases.

    Dynamic IP may change after recreation. Consumers caching Docker DNS must be
    reloaded under the maintenance gate; this does not promise IP preservation.
    Never start an inert test candidate with these production aliases or ports.
    """
    identity = _identity(str(info.get("Id", "")))
    settings = info.get("NetworkSettings")
    require(isinstance(settings, dict) and isinstance(settings.get("Networks"), dict), "Runtime network identity is missing")
    require(bool(settings["Networks"]), "No production network attachment was recorded")
    result: dict[str, dict[str, object]] = {}
    for name, network in settings["Networks"].items():
        require(isinstance(name, str) and isinstance(network, dict), "Malformed network attachment")
        aliases = network.get("Aliases") or []
        require(isinstance(aliases, list) and all(isinstance(alias, str) for alias in aliases), "Malformed network aliases")
        endpoint: dict[str, object] = {"Aliases": [alias for alias in aliases if alias not in {identity, identity[:12]}]}
        for key in ("IPAMConfig", "DriverOpts", "Links"):
            if network.get(key) is not None:
                endpoint[key] = copy.deepcopy(network[key])
        result[name] = endpoint
    return result


def clone_create_config(info: Mapping[str, object], image: str, operation: str, *,
                        image_labels: Mapping[str, str] | None = None) -> dict[str, object]:
    """Return an exact production replacement Docker-create payload, without creating it.

    Only Image, reviewed source attestation and one operation label change. Full Config/HostConfig, environment
    order, UID, command, healthcheck, security flags and represented mounts remain.
    A before snapshot contains secrets and must stay in a root-only artifact.
    """
    name = _target(info); _identity(image, image=True); _operation(operation)
    config = info.get("Config")
    require(isinstance(config, dict) and isinstance(info.get("HostConfig"), dict), "Runtime configuration is incomplete")
    _environment(config); _represented_mounts(info)
    require(not str(info["HostConfig"].get("NetworkMode", "")).startswith("container:"),
            "Shared foreign container network namespace requires review")
    result = copy.deepcopy(config)
    result["Image"] = image
    labels = result.get("Labels") or {}
    require(isinstance(labels, dict), "Malformed runtime labels")
    candidate_labels: dict[str, str] = {}
    if image_labels is not None:
        require(isinstance(image_labels, Mapping), "Candidate source labels are malformed")
        source_label = "com.aixingtuyun.image-fixes-policy-sha256" if name == NATIVE else "com.aixingtuyun.image-fixes-app-sha256"
        for key, value in image_labels.items():
            require(isinstance(key, str) and isinstance(value, str), "Malformed candidate label")
            if key in labels:
                require(value == labels[key], "Candidate changed an existing runtime label")
            else:
                require(key == source_label, "Candidate added an unreviewed runtime label")
                _identity(value)
                candidate_labels[key] = value
    result["Labels"] = {**labels, **candidate_labels, OP_LABEL: operation}
    result["HostConfig"] = copy.deepcopy(info["HostConfig"])
    result["NetworkingConfig"] = {"EndpointsConfig": network_endpoints(info)}
    return result


def _canonical_mounts(value: object) -> list[str]:
    """Compare all mount attributes; Docker's list order is not configuration."""
    require(isinstance(value, list) and all(isinstance(item, dict) for item in value),
            "Complete runtime mounts are required")
    return sorted(json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value)


def assert_fresh_baseline(saved: Mapping[str, object], current: Mapping[str, object]) -> None:
    """Fail closed on identity, runtime, persistent mount or network drift before any stop."""
    require(_target(saved) == _target(current), "Production target identity changed")
    for key in ("Id", "Image", "Name", "Config", "HostConfig"):
        require(saved.get(key) == current.get(key), "Production baseline drift; refresh and review private snapshot")
    require(_canonical_mounts(saved.get("Mounts")) == _canonical_mounts(current.get("Mounts")),
            "Production persistent mounts drifted")
    _identity(str(current.get("Id", ""))); _identity(str(current.get("Image", "")), image=True)
    require(isinstance(current.get("State"), dict) and current["State"].get("Running") is True,
            "Production process is not running")
    require(saved.get("NetworkSettings", {}).get("Networks") == current.get("NetworkSettings", {}).get("Networks"),
            "Production network attachment drift")


def assert_replacement(before: Mapping[str, object], after: Mapping[str, object], image: str, operation: str, *,
                       image_labels: Mapping[str, str] | None = None) -> None:
    """Verify a separately promoted replacement without weakening config or restoring data."""
    expected = clone_create_config(before, image, operation, image_labels=image_labels)
    require(_target(before) == _target(after), "Replacement target name differs")
    _identity(str(after.get("Id", "")))
    require(after.get("Id") != before.get("Id") and after.get("Image") == image,
            "Replacement immutable image/instance differs")
    require(isinstance(after.get("State"), dict) and after["State"].get("Running") is True,
            "Replacement is not running")
    expected_config = {key: value for key, value in expected.items() if key not in {"HostConfig", "NetworkingConfig"}}
    require(after.get("Config") == expected_config, "Replacement private configuration differs")
    require(after.get("HostConfig") == before.get("HostConfig"), "Replacement security/resource/runtime flags differ")
    require(_canonical_mounts(after.get("Mounts")) == _canonical_mounts(before.get("Mounts")),
            "Replacement persistent mounts differ")
    require(network_endpoints(after) == expected["NetworkingConfig"]["EndpointsConfig"], "Replacement network aliases/static settings differ")


def adapter_dockerfile(name: str, image: str, app_sha256: str) -> str:
    """Build only app plus the exact helper over a pinned inspected image.

    Legacy Docker builds have no COPY --chmod support. The prepared app/helper
    are explicitly mode0644 inside an owned mode0700 context; ordinary COPY keeps
    code readable to the inherited UID without adding USER or runtime RUN steps.
    """
    require(name in ADAPTERS, "Only fixed image adapters have an adapter context")
    _identity(image, image=True); _identity(app_sha256)
    helper = "COPY image_io.py /app/image_io.py\n" if name != BANANA else ""
    return (f"FROM {image}\nCOPY app.py /app/app.py\n" + helper +
            f'LABEL com.aixingtuyun.image-fixes-app-sha256="{app_sha256}"\n')


def native_dockerfile(runtime_image: str, builder_image: str, policy_sha256: str) -> str:
    """Compile the copied exact source, preserve embedded assets and replace only /new-api.

    Resolve the locally available Go builder to its full image ID first. Build
    with --network none. Missing cached dependencies abort before promotion; no
    package or frontend upgrade is performed. Existing production build flags
    (including greenteagc) are retained explicitly.
    """
    _identity(runtime_image, image=True); _identity(builder_image, image=True); _identity(policy_sha256)
    return (f"FROM {builder_image} AS builder\n"
            "ENV GOMAXPROCS=2 GOMEMLIMIT=1500MiB GO111MODULE=on CGO_ENABLED=0 GOEXPERIMENT=greenteagc GOFLAGS=-mod=readonly\n"
            "RUN test ! -e /issue190-source\n"
            "WORKDIR /issue190-source\nCOPY native-source/ /issue190-source/\n"
            'RUN go test ./service -count=1 && go vet ./service && go build -ldflags "-s -w -X \'github.com/QuantumNous/new-api/common.Version=$(cat VERSION)\'" -o /new-api . && test -x /new-api\n'
            f"FROM {runtime_image}\nCOPY --from=builder /new-api /new-api\n"
            f'LABEL com.aixingtuyun.image-fixes-policy-sha256="{policy_sha256}"\n')


def _regular(path: pathlib.Path) -> pathlib.Path:
    require(path.is_file() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode), "Expected a regular non-linked source file")
    require(path.resolve() == path.absolute(), "Linked source ancestry is not permitted")
    return path


def _digest(path: pathlib.Path, *, deadline: float | None = None) -> str:
    _regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            if deadline is not None:
                require(time.monotonic() < deadline, "Source verification exceeded its bounded deadline")
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: pathlib.Path) -> dict[str, str]:
    """Hash a bounded complete regular-file tree, including current frontend assets."""
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root.absolute(), "Exact non-linked source root is required")
    deadline = time.monotonic() + 120
    result: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        require(time.monotonic() < deadline, "Source tree traversal exceeded its bounded deadline")
        require(not path.is_symlink(), "Linked source member is not permitted")
        if path.is_dir():
            continue
        _regular(path)
        total += path.stat().st_size
        require(total <= MAX_SOURCE_BYTES and len(result) < MAX_SOURCE_FILES, "Source tree exceeds preparation bounds")
        result[path.relative_to(root).as_posix()] = _digest(path, deadline=deadline)
    require(bool(result), "Empty source tree is not a production baseline")
    return result


def _new_destination(destination: pathlib.Path, source: pathlib.Path) -> None:
    require(not destination.exists() and not destination.is_symlink(), "Destination already exists; preserve and reconcile it")
    resolved = destination.absolute().resolve()
    require(source.resolve() not in resolved.parents and resolved != source.resolve(), "Candidate destination must be outside the live source")
    require(destination.parent.is_dir() and destination.parent.resolve() == destination.parent.absolute(), "Owned non-linked destination parent is required")


def assert_native_assets(manifest: Mapping[str, str], verified_assets: Mapping[str, str] | None) -> None:
    """Require complete independently compared default/classic embedded asset digests.

    The operator must compare generated same-source entry/assets to the current
    production public artifact before supplying this proof. The existence of an
    older builder's /build/web assets is not proof. This helper never generates,
    borrows or silently changes frontend assets.
    """
    prefixes = ("web/default/dist/", "web/classic/dist/")
    assets = {path: digest for path, digest in manifest.items() if path.startswith(prefixes)}
    require(all(prefix + "index.html" in assets for prefix in prefixes),
            "Exact embedded default/classic frontend assets are missing; native preparation is blocked")
    require(isinstance(verified_assets, Mapping) and bool(verified_assets) and dict(verified_assets) == assets,
            "Complete independently verified production frontend asset evidence is required")
    require("VERSION" in manifest, "Exact production VERSION is missing")


def prepare_native_source(destination: pathlib.Path, reviewed_policy: pathlib.Path,
                          expected_manifest: Mapping[str, str], policy_sha256: str, *,
                          assets_manifest: Mapping[str, str] | None = None) -> dict[str, object]:
    """Backport ONE frozen policy onto the exact issue187 source; never modify live source.

    expected_manifest must be captured and reviewed from LIVE_NATIVE_SOURCE in
    this fresh operation, not a historical Git checkout. Missing/unverified
    default/classic frontend assets block copying; use no old builder asset.
    Compile only in a new empty /issue190-source directory, never on top of the
    builder's old /build tree where removed Go files could survive. On post-copy drift the
    partial owned context is deliberately preserved for diagnosis, not deleted.
    """
    _identity(policy_sha256)
    _new_destination(destination, LIVE_NATIVE_SOURCE)
    before = tree_manifest(LIVE_NATIVE_SOURCE)
    require(before == dict(expected_manifest), "Exact live source drifted after freeze")
    assert_native_assets(before, assets_manifest)
    require(before.get(NATIVE_POLICY) == NATIVE_POLICY_BASELINE_SHA256, "Native policy baseline is not the reviewed production source")
    require(_digest(reviewed_policy) == policy_sha256, "Reviewed replacement policy drifted")
    policy = reviewed_policy.read_bytes()
    require(hashlib.sha256(policy).hexdigest() == policy_sha256, "Reviewed policy changed during preparation")
    shutil.copytree(LIVE_NATIVE_SOURCE, destination, copy_function=shutil.copy2)
    os.chmod(destination, 0o700)
    require(tree_manifest(destination) == before and tree_manifest(LIVE_NATIVE_SOURCE) == before, "Source changed during copy; preserve partial preparation")
    (destination / NATIVE_POLICY).write_bytes(policy)
    after = tree_manifest(destination)
    expected = dict(before); expected[NATIVE_POLICY] = policy_sha256
    require(after == expected, "Candidate changed beyond the one reviewed image policy")
    return {"changed_paths": [NATIVE_POLICY], "policy_sha256": policy_sha256,
            "source_files": len(after), "production_source_unchanged": True}


def prepare_adapter_context(name: str, source: pathlib.Path, destination: pathlib.Path,
                            image: str, app_sha256: str) -> dict[str, object]:
    """Create an inert app-only context; never copy config, keys, tests or other files."""
    dockerfile = adapter_dockerfile(name, image, app_sha256)
    _new_destination(destination, source)
    require(_digest(source / "app.py") == app_sha256, "Reviewed adapter source drifted")
    files = {"app.py": app_sha256}
    if name != BANANA:
        require(_digest(source / "image_io.py") == IMAGE_IO_SHA256, "Exact unchanged image helper drifted")
        files["image_io.py"] = IMAGE_IO_SHA256
    destination.mkdir(mode=0o700)
    for filename, digest in files.items():
        shutil.copy2(source / filename, destination / filename)
        os.chmod(destination / filename, 0o644)
        require(_digest(destination / filename) == digest, "Adapter source changed during copy")
    (destination / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    os.chmod(destination / "Dockerfile", 0o600)
    return {"target": name, "base_image": image, "app_sha256": app_sha256,
            **({"image_io_sha256": IMAGE_IO_SHA256} if name != BANANA else {}),
            "paid_requests": 0, "production_modified": False}


def maintenance_location(operation: str) -> str:
    """Return only a reviewed text/image no-submit gate; do not write or reload nginx.

    Integrate inside the exact existing TLS server after saving its fresh digest.
    Run nginx -t before reload. Existing video locations and callbacks remain.
    """
    _operation(operation)
    return ("    location ~ ^/(v1/(edits|images/(generations|edits)|responses(/compact)?|chat/completions|completions|messages)|pg/chat/completions|api/channel/test(/[0-9]+)?)/?$ {\n"
            "        add_header X-XingTu-Image-Submission-State not_submitted always;\n"
            "        add_header X-XingTu-Relay-Request-ID $request_id always;\n"
            "        default_type application/json;\n"
            f'        return 503 \'{{"error":{{"code":"relay_maintenance","message":"No generation submitted; scoped upgrade {operation}"}}}}\';\n'
            "    }\n")


def assert_idle_evidence(evidence: Mapping[str, object], operation: str, *, now: float) -> None:
    """Validate fresh three-sample zero-inflight evidence, NOT mere HTTP health.

    Nody/Image25 have no active counter. Their exact Python process is single-
    threaded while idle; require /proc task evidence of one handler-process
    thread, collected without overlapping health probes. This is valid only for
    these reviewed implementations and after public admission is gated. Direct
    internal submitters must also be excluded by the operator; absent evidence
    cannot authorize a stop. Recheck immediately before each individual swap.
    """
    _operation(operation)
    require(evidence.get("operation_id") == operation and evidence.get("maintenance_active") is True
            and evidence.get("owned_image_drain") is True and evidence.get("internal_submitters_excluded") is True,
            "Owned maintenance/drain/internal-submitter evidence is incomplete")
    checked = evidence.get("checked_at")
    require(isinstance(checked, (int, float)) and not isinstance(checked, bool) and 0 <= now - checked <= 10,
            "Idle evidence is stale or future-dated")
    samples = evidence.get("samples")
    require(isinstance(samples, list) and len(samples) >= 3, "At least three consecutive quiet observations are required")
    for sample in samples:
        require(isinstance(sample, dict), "Malformed idle observation")
        limits = {"image_jobs_active": 0, "banana_active_requests": 0,
                  "nody_handler_threads": 1, "image25_handler_threads": 1, "native_reading": 0}
        for key, expected in limits.items():
            value = sample.get(key)
            require(type(value) is int and value == expected, "Active/unknown request evidence prohibits stopping")
        writing = sample.get("native_writing")
        require(type(writing) is int and 0 <= writing <= 1, "Accepted native writes have not drained")


if __name__ == "__main__":
    raise SystemExit("Preparation/verification library only: no automatic promotion, SSH or paid generation action exists.")
