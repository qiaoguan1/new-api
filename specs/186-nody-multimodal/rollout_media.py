"""Fresh server-local media rollout; no paid calls, old journal, or DB restore.

The operator supplies a reviewed manifest, immutable image/source identities,
private profile hashes and expected public retail rows. This helper does not
select a billing policy. Linux-only actions are explicit; importing is safe on
Windows. ``launch`` starts one detached, time-bounded ``run`` under an op lock.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse


GATEWAYS = ("xtai-video-public-execution", "xtai-video-job-gateway-v2-production")
PUBLIC = "xtai-public-video-catalog176"
TARGETS = (*GATEWAYS, PUBLIC)
NATIVE = "ai-api-stack-new-api-1"
NGINX = "ai-api-stack-nginx-1"
PG = "ai-api-stack-postgres-1"
DATABASE = "new-api"
SECRETS = "/opt/xtai/secrets/video-billing"
DATA = {GATEWAYS[0]: "/opt/xtai/state/public-video-execution/data", GATEWAYS[1]: "/opt/xtai/state/video-billing-v2-production/data"}
SOURCE_LABEL = "com.aixingtuyun.video.source-sha256"
NODY_MODELS = ("wan3.0-video", "wan3.0-video-prime", "grok-imagine-1.5-video", "grok-video-3", "grok-imagine-video-official", "omni-flash", "flux-3-video")
LEGACY_IMAGES = {"grok-video-3": ("720p", 6), "grok-imagine-1.5-video": ("720p", 6), "grok-imagine-video-official": ("480p", 1)}
ACTIVE = {"queued", "submitting", "running", "reconciling", "uncertain", "pending_review"}
PRIVATE_FIELDS = {"actual_cost_cny_exact", "reference_cost_cny_exact", "estimated_cost_cny_exact", "evidence_task_id", "evidence_source", "media_contract_evidence", "image_contract_evidence", "operator_policy_evidence", "policy_digest", "source_row_sha256"}
CAP_ADDITIONS = {"media_reference", "image_reference", "reference_video", "reference_audio", "reference_video_audio", "operator_testing", "operation_modes", "resolutions", "aspect_ratios", "durations", "duration_min", "duration_max", "max_images", "max_videos", "max_audios", "max_total_assets", "audio_mode", "generate_audio_required"}
UNORDERED_CATALOG_LISTS = {"models", "specifications", "enable_groups", "supported_endpoint_types", "operation_modes", "resolutions", "aspect_ratios", "durations", "mime_types", "audio_codecs", "video_codecs", "roles"}


class RolloutError(RuntimeError):
    """Fail closed with a credential-free operator diagnostic."""


class RolloutInterrupted(RolloutError):
    """A bounded runner was interrupted; owned recovery gets its grace period."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RolloutError(message)


def validate_manifest(value: object) -> dict:
    """Restrict targets, paths, config changes and immutable release identities."""
    require(isinstance(value, dict), "Manifest must be an object")
    allowed = {"schema_version", "operation_id", "private_root", "candidate_images", "candidate_sources", "source_labels", "image_profile", "media_profile", "operator_testing_profile", "expected_media_prices", "expected_operator_rules", "gateway_env", "allow_untested"}
    require(not set(value) - allowed and value.get("schema_version") == "xtai-nody-media-rollout-v1", "Unknown manifest schema/fields")
    testing = value.get("allow_untested", False)
    require(type(testing) is bool, "Testing authorization must be explicit boolean")
    rules = value.get("expected_operator_rules", [])
    require(isinstance(rules, list), "Expected operator rules must be an array")
    if testing:
        require(isinstance(value.get("operator_testing_profile"), dict) and 1 <= len(rules) <= 7, "Approved testing needs a separate policy and public rules")
        require(all(isinstance(row, dict) and row.get("model") in NODY_MODELS and row.get("verification_status") == "unverified"
                    and row.get("pricing_kind") == "estimated_reservation" and row.get("admission_mode") == "operator_testing" and row.get("is_upper_bound") is False for row in rules), "Operator rules must never claim verification or a price upper bound")
        require(len({row["model"] for row in rules}) == len(rules) and not contains_private_fields(rules), "Operator public rules are duplicate/private")
    else:
        require(not rules and not value.get("operator_testing_profile"), "Unapproved operator policy cannot be activated")
    operation = value.get("operation_id")
    require(isinstance(operation, str) and re.fullmatch(r"[0-9a-f]{32}", operation) is not None, "Fresh 32-hex operation ID required")
    require(value.get("private_root") == "/opt/ai-api-stack/backups/nody-media-rollout-" + operation, "Operation directory is outside exact scope")
    for field, pattern in (("candidate_images", r"sha256:[0-9a-f]{64}"), ("candidate_sources", r"[0-9a-f]{64}")):
        values = value.get(field)
        require(isinstance(values, dict) and set(values) == set(TARGETS), "Candidate targets must be exactly the three video services")
        require(all(isinstance(item, str) and re.fullmatch(pattern, item) for item in values.values()), "Immutable candidate identities required")
    labels = value.get("source_labels", {})
    require(isinstance(labels, dict) and not set(labels) - set(TARGETS)
            and all(isinstance(label, str) and re.fullmatch(r"com\.aixingtuyun\.[a-z0-9.-]*source-sha256", label) for label in labels.values()), "Source-label mapping is invalid")
    for kind in ("image_profile", "media_profile", *(("operator_testing_profile",) if testing else ())):
        row = value.get(kind)
        require(isinstance(row, dict) and set(row) == {"host_path", "container_path", "sha256"}, "Profile descriptor is invalid")
        require(isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "Profile hash is invalid")
        basename = "nody-image-input-186.json" if kind == "image_profile" else Path(str(row["host_path"])).name
        if kind == "media_profile":
            require(re.fullmatch(r"nody-media-input-[a-z0-9-]{1,80}\.json", basename) is not None, "Media profile filename is invalid")
        if kind == "operator_testing_profile":
            require(re.fullmatch(r"nody-operator-testing-[a-z0-9-]{1,80}\.json", basename) is not None, "Operator policy filename is invalid")
        require(row["host_path"] == SECRETS + "/" + basename and row["container_path"] == "/run/secrets/video-billing/" + basename, "Profile paths differ from fixed secrets scope")
    environment = value.get("gateway_env", {})
    require(isinstance(environment, dict) and not set(environment) - {"VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE", "VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE"}, "Unreviewed gateway environment field")
    require(environment.get("VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE", value["media_profile"]["container_path"]) == value["media_profile"]["container_path"], "Media config path mismatch")
    if testing:
        require(environment.get("VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE") == value["operator_testing_profile"]["container_path"], "Approved operator config path mismatch")
    else:
        require("VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE" not in environment, "Unapproved operator config path")
    prices = value.get("expected_media_prices")
    require(isinstance(prices, list) and len(prices) <= 500 and all(isinstance(row, dict) and row.get("model") in NODY_MODELS for row in prices), "Expected retail profile rows are invalid")
    require(not contains_private_fields(prices), "Private evidence cannot be expected public metadata")
    return copy.deepcopy(value)


def contains_private_fields(value: object) -> bool:
    """Check nested public projections, not only their top-level fields."""
    if isinstance(value, dict):
        return bool(set(value) & PRIVATE_FIELDS) or any(contains_private_fields(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_private_fields(item) for item in value)
    return False


def environment(info: dict) -> dict[str, str]:
    rows = info["Config"].get("Env") or []
    require(all(isinstance(row, str) and "=" in row and "\n" not in row and "\r" not in row for row in rows), "Container environment is malformed")
    result = dict(row.split("=", 1) for row in rows)
    require(len(result) == len(rows), "Duplicate environment keys need review")
    return result


def replacement_names(name: str, operation: str) -> tuple[str, str]:
    require(name in TARGETS and re.fullmatch(r"[0-9a-f]{32}", operation) is not None, "Invalid replacement identity")
    return name + "-rollback-media-" + operation, name + "-failed-media-" + operation


def network_endpoints(info: dict) -> dict:
    endpoints = {}
    for name, network in info["NetworkSettings"]["Networks"].items():
        endpoint = {"Aliases": [alias for alias in network.get("Aliases") or [] if alias not in (info["Id"], info["Id"][:12])]}
        for key in ("IPAMConfig", "DriverOpts", "Links"):
            if network.get(key) is not None:
                endpoint[key] = copy.deepcopy(network[key])
        endpoints[name] = endpoint
    return endpoints


def create_config(info: dict, manifest: dict, name: str) -> dict:
    """Change only image/op labels and the separately reviewed media config path."""
    require(name in TARGETS, "Unapproved replacement target")
    config = copy.deepcopy(info["Config"])
    config["Image"] = manifest["candidate_images"][name]
    config["Labels"] = {**(config.get("Labels") or {}), "com.aixingtuyun.media-operation": manifest["operation_id"]}
    values = environment(info)
    if name in GATEWAYS:
        require(values.get("VIDEO_JOB_NODYHUB_IMAGE_CONTRACT_FILE") == manifest["image_profile"]["container_path"], "Existing IMAGE contract must remain unchanged")
        values["VIDEO_JOB_NODYHUB_MEDIA_CONTRACT_FILE"] = manifest["media_profile"]["container_path"]
        if manifest.get("allow_untested") is True:
            values["VIDEO_JOB_NODYHUB_OPERATOR_TESTING_FILE"] = manifest["operator_testing_profile"]["container_path"]
    config["Env"] = [key + "=" + value for key, value in values.items()]
    config["HostConfig"] = copy.deepcopy(info["HostConfig"])
    config["NetworkingConfig"] = {"EndpointsConfig": network_endpoints(info)}
    return config


def new_media_payload(payload: dict) -> bool:
    """Distinguish expansion jobs from the already-supported six Grok tuples."""
    if payload.get("_nody_media_contract") is True or payload.get("_nody_operator_testing") is True or "_public_reservation" in payload:
        return True
    if payload.get("model") not in NODY_MODELS:
        return False
    if any(payload.get(key) for key in ("videos", "audios", "reference_videos", "reference_audios")):
        return True
    if payload.get("mode", "text") == "text" and not payload.get("images") and not payload.get("reference_images"):
        return False
    spec = LEGACY_IMAGES.get(payload.get("model"))
    images = payload.get("images") or payload.get("reference_images") or []
    return not (spec and isinstance(images, list) and (payload.get("resolution"), payload.get("duration")) == spec
                and payload.get("aspect_ratio", "16:9") == "16:9" and payload.get("generate_audio") is True
                and ((payload.get("mode") == "reference" and len(images) == 1) or (payload.get("mode") == "all_reference" and len(images) == 2)))


def assert_inflight(rows: list[dict], *, rollback: bool = False) -> None:
    """Stopping must never interrupt an in-flight or unidentified submission."""
    for row in rows:
        require(row.get("status") not in {"queued", "submitting"}, "Queued/submitting work must finish before stopping services")
        if row.get("status") == "running":
            require(bool(row.get("upstream_task_id")), "Running job has no confirmed upstream identity")
        if row.get("status") == "reconciling" and not row.get("upstream_task_id"):
            require(row.get("provider_id") == "nodyhub", "Uncertain non-Nody work needs manual review")
        payload = row.get("payload")
        require(isinstance(payload, dict), "Active job payload is malformed")
        if rollback and row.get("status") in ACTIVE:
            require(not new_media_payload(payload), "New media job must finish before old-code rollback")


def semantic_metadata(value: object, key: str = "") -> object:
    """Normalize only known unordered catalog lists and version placement."""
    if isinstance(value, dict):
        return {field: semantic_metadata(item, field) for field, item in value.items() if field != "pricing_version"}
    if isinstance(value, list):
        rows = [semantic_metadata(item) for item in value]
        unordered = key in UNORDERED_CATALOG_LISTS or (key == "data" and all(isinstance(row, dict) and ("id" in row or "model_name" in row) for row in value))
        return sorted(rows, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))) if unordered else rows
    return value


def canonical_rows(rows: list[dict]) -> list[str]:
    return sorted(json.dumps(semantic_metadata(row), sort_keys=True, ensure_ascii=False, separators=(",", ":")) for row in rows)


def compare_snapshot(before: dict, after: dict, expected_prices: list[dict], *, rollback: bool = False, operator_rules: list[dict] | None = None) -> None:
    """Allow exact additive Nody media metadata, never baseline prices/ACL drift."""
    if rollback:
        for path in ("/v1/capabilities", "/v1/video-prices", "/v1/models", "/api/pricing"):
            if path in before:
                require(semantic_metadata(after.get(path)) == semantic_metadata(before[path]), "Rollback did not restore the fresh baseline")
        return
    old_prices, new_prices = before["/v1/video-prices"], after["/v1/video-prices"]
    require(canonical_rows(old_prices["pricing"]["models"]) == canonical_rows(new_prices["pricing"]["models"]), "Existing text prices changed")
    old_images = old_prices.get("image_reference_pricing", {}).get("models", [])
    new_images = new_prices.get("image_reference_pricing", {}).get("models", [])
    expected_images = {(model, mode, count, resolution, seconds) for model, (resolution, seconds) in LEGACY_IMAGES.items() for mode, count in (("reference", 1), ("all_reference", 2))}
    tuples = {(row["model"], row["operation_mode"], row["image_count"], row["resolution"], row["duration"]) for row in old_images}
    require(len(old_images) == 6 and tuples == expected_images and canonical_rows(old_images) == canonical_rows(new_images), "The six deployed Grok image prices changed")
    media = new_prices.get("media_reference_pricing", {}).get("models", [])
    require(isinstance(media, list) and canonical_rows(media) == canonical_rows(expected_prices) and not contains_private_fields(media), "Published media prices differ or expose private evidence")
    expected_rules = operator_rules or []
    pricing_operator = new_prices.get("operator_testing", {})
    if expected_rules:
        require(isinstance(pricing_operator, dict) and pricing_operator.get("enabled") is True
                and pricing_operator.get("verification_status") == "unverified" and pricing_operator.get("admission_mode") == "operator_testing"
                and canonical_rows(pricing_operator.get("rules", [])) == canonical_rows(expected_rules)
                and not contains_private_fields(pricing_operator), "Estimated operator pricing is missing, misleading or private")
    else:
        require(not pricing_operator or (pricing_operator.get("enabled") is not True and not pricing_operator.get("rules")), "Unapproved operator testing pricing was enabled")
    old_rows = {row["id"]: row for row in before["/v1/capabilities"]["capabilities"]["video"]["models"]}
    new_rows = {row["id"]: row for row in after["/v1/capabilities"]["capabilities"]["video"]["models"]}
    require(set(old_rows) == set(new_rows) and set(NODY_MODELS).issubset(new_rows), "Existing capability model IDs changed")
    for model, old in old_rows.items():
        new = new_rows[model]
        if model not in NODY_MODELS:
            require(semantic_metadata(old) == semantic_metadata(new), "Non-Nody capabilities changed")
            continue
        require(semantic_metadata({key: value for key, value in old.items() if key not in CAP_ADDITIONS}) == semantic_metadata({key: value for key, value in new.items() if key not in CAP_ADDITIONS}), "Existing Nody core fields changed")
        for field in ("operation_modes", "resolutions", "aspect_ratios", "durations"):
            require(set(old.get(field) or []).issubset(set(new.get(field) or [])), "Existing Nody capability disappeared")
        for field in ("max_images", "max_videos", "max_total_assets", "duration_max"):
            require(new.get(field, old.get(field, 0)) >= old.get(field, 0), "Existing Nody limits narrowed")
        if model in LEGACY_IMAGES:
            new_image = new.get("image_reference")
            if isinstance(new_image, dict):
                new_image = dict(new_image)
                new_image.pop("operator_testing", None)
            require(semantic_metadata(old.get("image_reference")) == semantic_metadata(new_image), "Deployed Grok image capabilities changed")
        profiles = [row for row in expected_prices if row["model"] == model]
        candidates = [row for row in expected_rules if row["model"] == model]
        operator = new.get("operator_testing", {})
        if candidates:
            require(isinstance(operator, dict) and operator.get("supported") is True
                    and operator.get("available") is (new.get("available") is True)
                    and operator.get("verification_status") == "unverified" and operator.get("admission_mode") == "operator_testing"
                    and canonical_rows(operator.get("rules", [])) == canonical_rows(candidates), "Operator capabilities do not match explicitly approved rules")
        else:
            require(not operator or (operator.get("supported") is not True and not operator.get("rules")), "Unapproved operator capability appeared")
        projection = new.get("media_reference")
        require(isinstance(projection, dict) and canonical_rows(projection.get("specifications", [])) == canonical_rows(profiles), "Model media specifications differ from exact expected prices")
        covered = bool(profiles) or bool(candidates)
        require(projection.get("supported") is covered and projection.get("available") is (covered and new.get("available") is True), "Media availability differs from profile or approved candidate coverage")
        for field, count in (("reference_video", "video_count"), ("reference_audio", "audio_count"), ("reference_video_audio", "video_count")):
            matches = [row for row in profiles if row.get(count, 0) > 0 and (field != "reference_video_audio" or row.get("audio_count", 0) > 0)]
            testing_supported = any(row.get("max_videos" if field != "reference_audio" else "max_audios", 0) > 0
                                    and (field != "reference_video_audio" or row.get("max_audios", 0) > 0) for row in candidates)
            value = new.get(field)
            require(isinstance(value, dict) and canonical_rows(value.get("specifications", [])) == canonical_rows(matches), "AV metadata advertises an unpriced combination")
            supported = bool(matches) or testing_supported
            require(value.get("supported") is supported and value.get("available") is (supported and new.get("available") is True), "AV availability differs from exact or explicitly approved candidate coverage")
            if testing_supported:
                require(value.get("verification_status") == "unverified" and value.get("admission_mode") == "operator_testing", "Candidate AV capability incorrectly claims verification")
        require(not contains_private_fields(new), "Private media evidence leaked into capabilities")
    if "/v1/models" in before:
        require({row["id"] for row in before["/v1/models"]["data"]}.issubset({row["id"] for row in after["/v1/models"]["data"]}), "Existing public model disappeared")
    if "/api/pricing" in before:
        def normalized(payload: dict) -> dict:
            result = {}
            for row in payload["data"]:
                value = {key: item for key, item in row.items() if key not in {"media_reference", "media_reference_pricing", "image_reference", "image_reference_pricing", "operator_testing", "reference_video", "reference_audio", "reference_video_audio", "pricing_version"} or (key in {"reference_video", "reference_audio", "reference_video_audio"} and row.get("model_name") not in NODY_MODELS)}
                if row.get("model_name") in NODY_MODELS and isinstance(value.get("description"), str):
                    value["description"] = value["description"].split("另支持已验证媒体模式；", 1)[0]
                    value["description"] = value["description"].split("另开放未验收手测候选模式；", 1)[0]
                for key in ("enable_groups", "supported_endpoint_types"):
                    if isinstance(value.get(key), list):
                        value[key] = sorted(value[key], key=lambda item: json.dumps(item, sort_keys=True))
                result[row["model_name"]] = semantic_metadata(value)
            return result
        require(normalized(before["/api/pricing"]) == normalized(after["/api/pricing"]), "Native/market prices or access groups changed")


class DockerConnection(http.client.HTTPConnection):
    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/var/run/docker.sock")


class HostRuntime:
    """Bounded local Docker/backup/free-read operations, never generation APIs."""

    def __init__(self, manifest: dict):
        self.manifest = manifest
        self.root = Path(manifest["private_root"])

    def ensure_root(self) -> None:
        if not self.root.exists():
            self.root.mkdir(mode=0o700)
        require(not self.root.is_symlink() and self.root.is_dir() and self.root.stat().st_uid == os.geteuid()
                and self.root.stat().st_mode & 0o077 == 0, "Private operation directory ownership/mode differs")

    def exists(self, name: str) -> bool:
        require(Path(name).name == name, "Invalid private artifact name")
        return (self.root / name).exists()

    def write(self, name: str, value: object, *, exclusive: bool = False) -> None:
        require(Path(name).name == name, "Invalid private artifact name")
        self.ensure_root()
        path = self.root / name
        require(not path.is_symlink(), "Private artifact is a symlink")
        target = path if exclusive else self.root / (name + ".tmp-" + str(time.time_ns()))
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True)
            output.flush(); os.fsync(output.fileno())
        if not exclusive:
            os.replace(target, path)
        directory = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def read(self, name: str) -> dict:
        path = self.root / name
        require(Path(name).name == name and path.is_file() and not path.is_symlink() and path.stat().st_size <= 16 * 1024 * 1024, "Private artifact is missing/invalid")
        return json.loads(path.read_text(encoding="utf-8"))

    def command(self, args: list[str], *, text: str | None = None, timeout: int = 30) -> str:
        result = subprocess.run(args, input=text, capture_output=True, text=True, timeout=timeout)
        require(result.returncode == 0 and len(result.stdout) <= 16 * 1024 * 1024, "Bounded local command failed; private state preserved")
        return result.stdout.strip()

    def inspect(self, name: str) -> dict:
        return json.loads(self.command(["docker", "inspect", "--type", "container", name]))[0]

    def image(self, identity: str) -> dict:
        return json.loads(self.command(["docker", "image", "inspect", identity]))[0]

    def names(self) -> list[str]:
        return self.command(["docker", "ps", "-a", "--format", "{{.Names}}"]).splitlines()

    def profile_digest(self, value: str) -> str:
        path = Path(value)
        require(path.is_file() and not path.is_symlink() and path.stat().st_size <= 2 * 1024 * 1024, "Private profile is missing/invalid")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def sql(self, statement: str) -> str:
        require(statement.lstrip().upper().startswith("SELECT ") and ";" not in statement.rstrip().rstrip(";"), "Production SQL must be one read-only SELECT")
        return self.command(["docker", "exec", "-i", PG, "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "newapi", "-d", DATABASE, "-At"], text=statement)

    def ordinary_key(self) -> str:
        value = self.sql("SELECT t.key FROM tokens t JOIN users u ON u.id=t.user_id WHERE u.status=1 AND u.role=1 AND t.status IN (1,4) AND t.deleted_at IS NULL AND u.deleted_at IS NULL AND (t.expired_time=-1 OR t.expired_time>extract(epoch from now())) AND coalesce(t.allow_ips,'')='' AND t.model_limits_enabled=false AND coalesce(t.\"group\",'') IN ('','auto') ORDER BY t.id DESC LIMIT 1;")
        require(bool(value) and "\n" not in value and len(value) <= 128, "No authorized ordinary unrestricted user token")
        return value if value.startswith("sk-") else "sk-" + value

    def snapshot(self, name: str) -> dict:
        require(name in TARGETS, "Unapproved discovery target")
        info = self.inspect(name)
        address = str(info["NetworkSettings"]["Networks"]["app-net"]["IPAddress"])
        require(ipaddress.ip_address(address).is_private, "Unexpected Docker internal address")
        token = self.ordinary_key() if name == PUBLIC else environment(info).get("VIDEO_JOB_GATEWAY_TOKEN")
        require(isinstance(token, str) and bool(token), "Discovery token unavailable")
        paths = ("/ready", "/v1/models", "/v1/capabilities", "/v1/video-prices", "/api/pricing") if name == PUBLIC else ("/health", "/v1/capabilities", "/v1/video-prices")
        result = {}
        for path in paths:
            connection = http.client.HTTPConnection(address, 8098 if name == PUBLIC else 8091, timeout=20)
            try:
                connection.request("GET", path, headers={"Authorization": "Bearer " + token, "X-XingTu-Contract-Version": "xtai-video-billing-v2.2"})
                response = connection.getresponse()
                raw = response.read(4 * 1024 * 1024 + 1)
                require(response.status == 200 and len(raw) <= 4 * 1024 * 1024, "Free authenticated discovery is unavailable")
                result[path] = json.loads(raw)
            finally:
                connection.close()
        return result

    def backup(self, name: str, label: str) -> None:
        require(name in GATEWAYS and label in {"before", "drained"}, "Unapproved SQLite backup")
        source = Path(DATA[name]) / "video-jobs.sqlite3"
        require(source.is_file() and not source.is_symlink(), "Exact gateway state file missing")
        target = self.root / (name + "." + label + ".sqlite3")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
        reader = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)
        writer = sqlite3.connect(target, timeout=30)
        deadline = time.monotonic() + 60
        def progress(*args):
            require(time.monotonic() < deadline, "WAL-aware backup exceeded time limit")
        try:
            reader.backup(writer, pages=256, progress=progress)
            require(writer.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite backup integrity failed")
        finally:
            writer.close(); reader.close()

    def postgres_backup(self) -> None:
        path = self.root / "production.before.dump"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            result = subprocess.run(["docker", "exec", PG, "pg_dump", "-U", "newapi", "-d", DATABASE, "-Fc"], stdout=output, stderr=subprocess.DEVNULL, timeout=180)
        require(result.returncode == 0 and path.stat().st_size > 0, "Read-only PostgreSQL backup failed")

    def inflight(self, *, rollback: bool = False) -> None:
        for name in GATEWAYS:
            connection = sqlite3.connect((Path(DATA[name]) / "video-jobs.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=15)
            try:
                rows = [{"status": row[0], "upstream_task_id": row[1], "provider_id": row[2], "payload": json.loads(row[3])}
                        for row in connection.execute("SELECT status,upstream_task_id,provider_id,payload_json FROM video_jobs WHERE status IN ('queued','submitting','running','reconciling','uncertain','pending_review')")]
            finally:
                connection.close()
            assert_inflight(rows, rollback=rollback)
        public = self.sql("SELECT json_build_object('state',state,'backend_id',backend_id,'body',body)::text FROM public_video_tasks WHERE state IN ('reserved','submitted','running','pending_review');")
        for line in public.splitlines():
            row = json.loads(line)
            require(bool(row.get("backend_id")), "Unconfirmed public backend identity prevents replacement")
            if rollback:
                body = row["body"] if isinstance(row["body"], dict) else json.loads(row["body"])
                require(not new_media_payload(body), "Unsettled new media wallet job prevents rollback")

    def drain(self, name: str, operation: str, *, remove: bool = False) -> None:
        require(name in GATEWAYS and re.fullmatch(r"[0-9a-f]{32}", operation), "Unapproved drain target")
        path = Path(DATA[name]) / "DRAIN"
        content = ("issue186-media-operation:" + operation + "\n").encode()
        require(not path.is_symlink(), "Drain path is a symlink")
        if path.exists():
            require(path.read_bytes() == content, "Drain belongs to another operation")
            if remove:
                path.unlink()
            return
        if remove:
            return
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(content); output.flush(); os.fsync(output.fileno())
        os.chown(path, 10002, 10002)

    def docker(self, path: str, body: dict | None = None) -> dict:
        connection = DockerConnection("localhost", timeout=90)
        try:
            connection.request("POST", path, body=json.dumps(body).encode() if body is not None else None, headers={"Content-Type": "application/json"})
            response = connection.getresponse(); raw = response.read(4 * 1024 * 1024 + 1)
            require(response.status in {200, 201, 204, 304} and len(raw) <= 4 * 1024 * 1024, "Scoped Docker action failed")
            return json.loads(raw) if raw else {}
        finally:
            connection.close()

    def stop(self, identity: str) -> None:
        self.docker("/containers/" + urllib.parse.quote(identity, safe="") + "/stop?t=30")

    def rename(self, identity: str, name: str) -> None:
        self.docker("/containers/" + urllib.parse.quote(identity, safe="") + "/rename?name=" + urllib.parse.quote(name, safe=""))

    def disconnect(self, info: dict) -> None:
        for network in info["NetworkSettings"]["Networks"]:
            self.docker("/networks/" + urllib.parse.quote(network, safe="") + "/disconnect", {"Container": info["Id"], "Force": False})

    def create(self, name: str, config: dict) -> str:
        return self.docker("/containers/create?name=" + urllib.parse.quote(name, safe=""), config)["Id"]

    def start(self, identity: str) -> None:
        self.docker("/containers/" + urllib.parse.quote(identity, safe="") + "/start")

    def connect(self, identity: str, endpoints: dict) -> None:
        current = self.inspect(identity)["NetworkSettings"]["Networks"]
        for name, endpoint in endpoints.items():
            if name not in current:
                self.docker("/networks/" + urllib.parse.quote(name, safe="") + "/connect", {"Container": identity, "EndpointConfig": endpoint})

    def wait_health(self, name: str) -> None:
        for attempt in range(10):
            try:
                self.snapshot(name)
                return
            except (OSError, ValueError, RolloutError):
                if attempt == 9:
                    raise RolloutError("Replacement did not become healthy") from None
                time.sleep(1)

    def reload_routes(self) -> None:
        for attempt in range(15):
            checked = subprocess.run(["docker", "exec", NGINX, "timeout", "15s", "nginx", "-t"], capture_output=True, text=True, timeout=20)
            self.write("nginx-test.json", {"attempt": attempt, "returncode": checked.returncode, "diagnostic": checked.stderr[-2000:]})
            if checked.returncode == 0:
                break
            require("host not found in upstream" in checked.stderr.lower() and attempt < 14, "Nginx validation needs review")
            time.sleep(2)
        self.command(["docker", "exec", NGINX, "timeout", "15s", "nginx", "-s", "reload"], timeout=20)


class Rollout:
    """Journal every replacement boundary against the freshly captured baseline."""

    def __init__(self, manifest: dict, runtime: HostRuntime):
        self.manifest = validate_manifest(manifest)
        self.runtime = runtime
        self.digest = hashlib.sha256(json.dumps(self.manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.recovering = False

    def profiles(self) -> None:
        for kind in ("image_profile", "media_profile", *(("operator_testing_profile",) if self.manifest.get("allow_untested") is True else ())):
            row = self.manifest[kind]
            require(self.runtime.profile_digest(row["host_path"]) == row["sha256"], "Reviewed profile bytes changed")

    def check(self, state: dict, *, rollback: bool = False) -> None:
        require(state.get("operation_id") == self.manifest["operation_id"] and state.get("manifest_sha256") == self.digest, "Fresh operation manifest differs")
        native = self.runtime.inspect(NATIVE)
        require(native["Id"] == state["native"]["id"] and native["Image"] == state["native"]["image"] and native["State"]["Running"], "Fresh native runtime changed")
        if rollback:
            # Recovery uses saved baseline configuration, not the new MEDIA
            # profile. Its corruption must not strand the healthy old services.
            row = self.manifest["image_profile"]
            require(self.runtime.profile_digest(row["host_path"]) == row["sha256"], "Existing IMAGE contract changed")
        else:
            self.profiles()

    def save(self, state: dict) -> None:
        self.runtime.write("state.json", state)

    def stage(self) -> None:
        r = self.runtime
        require(not r.exists("state.json"), "This fresh operation already has state; inspect it, never overwrite")
        self.profiles()
        native = r.inspect(NATIVE)
        require(native["State"]["Running"], "Native production runtime is not running")
        state = {"schema_version": "xtai-nody-media-rollout-state-v1", "operation_id": self.manifest["operation_id"], "manifest_sha256": self.digest,
                 "phase": "staging", "created_at": int(time.time()), "native": {"id": native["Id"], "image": native["Image"]}, "targets": {}, "swapped": [], "drains": []}
        r.write("native.before.json", native, exclusive=True)
        r.write("state.json", state, exclusive=True)
        names = r.names()
        for name in TARGETS:
            require(not set(replacement_names(name, self.manifest["operation_id"])) & set(names), "Fresh rollback/failed name already exists")
            info = r.inspect(name)
            require(info["State"]["Running"], "Expected production video service is not running")
            if name in GATEWAYS:
                mounts = info.get("Mounts") or []
                require(any(row.get("Destination") == "/data" and row.get("Source") == DATA[name] and row.get("RW") is True for row in mounts), "Existing gateway data mount differs")
                require(any(row.get("Destination") == "/run/secrets/video-billing" and row.get("Source") == SECRETS and row.get("RW") is False for row in mounts), "Existing read-only secret mount differs")
                require(environment(info).get("VIDEO_JOB_GATEWAY_DRAIN_FILE_NAME", "DRAIN") == "DRAIN", "Nondefault drain path needs review")
                require("upload.aixingtuyun.com" in environment(info).get("VIDEO_JOB_GATEWAY_REFERENCE_MEDIA_HOSTS", "").split(","), "Approved reference origin is absent")
            create_config(info, self.manifest, name)
            if name == PUBLIC:
                require((info['Config'].get('Entrypoint') or [None])[0] == '/usr/local/bin/public-video', 'Public saved entrypoint differs from the reviewed candidate binary path')
            image = r.image(self.manifest["candidate_images"][name])
            label = self.manifest.get("source_labels", {}).get(name, SOURCE_LABEL)
            require(image["Id"] == self.manifest["candidate_images"][name] and image["Config"].get("Labels", {}).get(label) == self.manifest["candidate_sources"][name], "Candidate image/source attestation differs")
            r.write(name + ".before.json", info, exclusive=True)
            r.write(name + ".baseline.json", r.snapshot(name), exclusive=True)
            state["targets"][name] = {"before_id": info["Id"], "before_image": info["Image"], "candidate_image": image["Id"]}
            self.save(state)
        r.inflight()
        for name in GATEWAYS:
            r.backup(name, "before")
        r.postgres_backup()
        state["phase"] = "staged"; self.save(state)

    def drains(self, state: dict, *, remove: bool = False) -> None:
        for name in (tuple(state["drains"]) if remove else GATEWAYS):
            require(name in GATEWAYS, "Unapproved recorded drain")
            if not remove and name not in state["drains"]:
                state["drains"].append(name); self.save(state)
            self.runtime.drain(name, self.manifest["operation_id"], remove=remove)
            if remove:
                state["drains"].remove(name); self.save(state)

    def promote(self) -> None:
        r = self.runtime; state = r.read("state.json")
        require(state["phase"] == "staged" and not state["swapped"], "Promotion needs freshly staged state")
        self.check(state); r.inflight()
        state["phase"] = "draining"; self.save(state); self.drains(state)
        r.inflight(); r.stop(state["targets"][PUBLIC]["before_id"]); r.inflight()
        for name in GATEWAYS:
            r.backup(name, "drained")
        for name in TARGETS:
            self.check(state); r.inflight()
            target = state["targets"][name]; current = r.inspect(name)
            require(current["Id"] == target["before_id"], "Video service identity changed since staging")
            require(r.image(target["candidate_image"])["Id"] == target["candidate_image"], "Immutable candidate image disappeared")
            saved = r.read(name + ".before.json")
            state["swapped"].append(name); self.save(state)
            r.stop(current["Id"])
            r.rename(current["Id"], replacement_names(name, self.manifest["operation_id"])[0])
            r.disconnect(current)
            target["new_id"] = r.create(name, create_config(saved, self.manifest, name)); self.save(state)
            r.start(target["new_id"]); r.wait_health(name)
        state["phase"] = "replaced_drained"; self.save(state)
        r.reload_routes(); self.drains(state, remove=True); self.verify()

    def verify(self, *, rollback: bool = False) -> None:
        r = self.runtime; state = r.read("state.json"); self.check(state, rollback=rollback)
        for name in TARGETS:
            info = r.inspect(name)
            expected = state["targets"][name]["before_id"] if rollback else state["targets"][name].get("new_id")
            require(info["Id"] == expected and info["State"]["Running"], "Verified service identity/running state differs")
            if not rollback:
                require(info["Image"] == state["targets"][name]["candidate_image"] and info["Config"].get("Labels", {}).get("com.aixingtuyun.media-operation") == self.manifest["operation_id"], "Replacement ownership differs")
            current = r.snapshot(name)
            compare_snapshot(r.read(name + ".baseline.json"), current, self.manifest["expected_media_prices"], rollback=rollback,
                             operator_rules=self.manifest.get("expected_operator_rules", []))
            r.write(name + (".rollback-verification.json" if rollback else ".verification.json"), current)
        state["phase"] = "rolled_back_verified" if rollback else "promoted_verified"
        state["verified_at"] = int(time.time()); self.save(state)

    def rollback(self) -> None:
        r = self.runtime; state = r.read("state.json"); self.check(state, rollback=True)
        require(state["swapped"] or state["drains"], "No owned mutations to roll back")
        r.inflight(rollback=True); self.drains(state)
        target = state["targets"][PUBLIC]
        if PUBLIC in r.names():
            public = r.inspect(PUBLIC)
            require(public["Id"] == target["before_id"] or (public["Image"] == target["candidate_image"]
                    and public["Config"].get("Labels", {}).get("com.aixingtuyun.media-operation") == self.manifest["operation_id"]
                    and (not target.get("new_id") or public["Id"] == target["new_id"])), "Public rollback ownership differs")
            r.stop(public["Id"])
        else:
            require(PUBLIC in state["swapped"], "Missing public name has no recorded replacement intent")
        for name in TARGETS:
            if name not in state["swapped"]:
                continue
            target = state["targets"][name]
            if name in r.names():
                current = r.inspect(name)
                if current["Id"] != target["before_id"]:
                    require(current["Image"] == target["candidate_image"] and current["Config"].get("Labels", {}).get("com.aixingtuyun.media-operation") == self.manifest["operation_id"]
                            and (not target.get("new_id") or current["Id"] == target["new_id"]), "Current replacement ownership changed")
                    r.stop(current["Id"]); r.rename(current["Id"], replacement_names(name, self.manifest["operation_id"])[1]); r.disconnect(current)
            old = r.inspect(target["before_id"])
            require(old["Name"] in ("/" + name, "/" + replacement_names(name, self.manifest["operation_id"])[0]) and old["Image"] == target["before_image"], "Retained baseline identity differs")
            if old["Name"] != "/" + name:
                r.rename(old["Id"], name)
            r.connect(old["Id"], network_endpoints(r.read(name + ".before.json"))); r.start(old["Id"]); r.wait_health(name)
        if PUBLIC not in state["swapped"]:
            r.start(state["targets"][PUBLIC]["before_id"]); r.wait_health(PUBLIC)
        r.reload_routes(); self.drains(state, remove=True); self.verify(rollback=True)

    def interrupt(self) -> None:
        """Ignore repeated TERM only during the one bounded recovery attempt."""
        if not self.recovering:
            raise RolloutInterrupted("Runner interrupted; owned state needs recovery")

    def execute(self, action: str) -> None:
        """One promotion attempt and at most one safe, journal-owned rollback."""
        require(action in {"run", "stage", "promote", "verify", "rollback"}, "Unapproved rollout action")
        try:
            if action == "run":
                self.stage(); self.promote()
            elif action == "verify":
                self.verify(rollback=self.runtime.read("state.json").get("phase") == "rolled_back_verified")
            else:
                getattr(self, action)()
        except Exception as error:
            state = self.runtime.read("state.json") if self.runtime.exists("state.json") else {}
            self.runtime.write("operation-error.json", {"type": type(error).__name__, "action": action, "phase": state.get("phase"), "at": int(time.time())})
            if action in {"run", "promote"} and state.get("operation_id") == self.manifest["operation_id"] and state.get("manifest_sha256") == self.digest and (state.get("swapped") or state.get("drains") or state.get("phase") == "draining"):
                self.recovering = True
                self.runtime.write("recovery.json", {"attempts": 1, "state": "recovering", "at": int(time.time())})
                try:
                    self.rollback()
                    self.runtime.write("recovery.json", {"attempts": 1, "state": "rolled_back_verified", "at": int(time.time())})
                except Exception as recovery:
                    self.runtime.write("rollback-error.json", {"type": type(recovery).__name__, "attempts": 1, "at": int(time.time())})
                finally:
                    self.recovering = False
            raise


def runner_command(manifest_path: str) -> list[str]:
    """One detached bounded process; no shell interpolation or automatic replay."""
    return ["timeout", "--signal=TERM", "--kill-after=600s", "900s", sys.executable, str(Path(__file__).resolve()), "run", "--manifest", manifest_path, "--runner"]


def main() -> None:
    require(sys.platform.startswith("linux"), "Server-local Linux helper only")
    import fcntl

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("stage", "promote", "verify", "rollback", "run", "launch"))
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--runner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    source = Path(args.manifest)
    require(source.is_file() and not source.is_symlink() and source.stat().st_size <= 2 * 1024 * 1024, "Private manifest is missing/invalid")
    manifest = validate_manifest(json.loads(source.read_text(encoding="utf-8")))
    runtime = HostRuntime(manifest); runtime.ensure_root(); rollout = Rollout(manifest, runtime)
    import signal

    def interrupted(_signal, _frame):
        rollout.interrupt()

    signal.signal(signal.SIGTERM, interrupted)
    lock = os.open(runtime.root / "operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX if args.runner else fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "launch":
            runtime.write("launch.json", {"operation_id": manifest["operation_id"], "state": "launch_intent", "at": int(time.time())}, exclusive=True)
            fd = os.open(runtime.root / "runner.log", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as output:
                process = subprocess.Popen(runner_command(str(source.resolve())), stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
            runtime.write("launch.json", {"operation_id": manifest["operation_id"], "state": "launched", "pid": process.pid, "deadline_seconds": 900})
            print(json.dumps({"launched": True, "operation_id": manifest["operation_id"], "pid": process.pid, "paid_requests": 0}))
            return
        rollout.execute(args.action)
        print(json.dumps({"completed": True, "action": args.action, "operation_id": manifest["operation_id"], "paid_requests": 0}))
    except Exception as error:
        if not runtime.exists("operation-error.json"):
            runtime.write("operation-error.json", {"type": type(error).__name__, "action": args.action, "at": int(time.time())})
        raise
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"needs_attention": True, "error_type": type(error).__name__, "reason": str(error) if isinstance(error, RolloutError) else "Private operation diagnostics need review", "paid_requests": 0, "automatic_retry": False}))
        raise SystemExit(1)
