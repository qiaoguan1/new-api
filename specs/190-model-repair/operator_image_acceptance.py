"""Bounded operator acceptance; importing this module never creates a paid task.

Only root runs the CLI after independent review. This operation is a CNY0.60
allocation within the parent's CNY20 budget, not an additional budget. It never
changes user balances, existing keys, prices, channels or historical tasks.

Offline self-review (issue190/191): role/route/price evidence is required before
creation; atomic exclusive intents and stage locks protect crash/concurrency;
401/timeout/bad media or missing receipts remain unresolved, never replayed;
quotes are not relabeled actual costs or inferred from a shared balance delta;
only the one-hour limited root key may be closed, with unknown expiry audit;
PNG-only validation is intentional and never claims unsupported JPEG decoding.
All seven review criteria were checked. Mock-only tests and compile checks pass;
independent root review and production execution remain separate release gates.

Legacy read-only reconciliation may use an owned, SHA256-pinned original response
when native has not yet persisted its upstream ID: native own-token/model/request
must exactly match response relay-ID, its distinct provider-ID must be returned
by the owned Nody adapter, current expected route/token/group must verify, and
the authenticated provider receipt must match that exact ID uniquely. Conflicts
reject; timestamps, balance deltas and model-only candidates never substitute.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import functools
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import subprocess
import sys
import time
import threading
from typing import Callable, Protocol
import urllib.error
import urllib.request
import uuid
import zlib

MODELS = ("gpt-image-2.5-flare", "gpt-image-2.5-sunburst")
RATE = Decimal("1.5")
MAX_PER_IMAGE = Decimal("0.30")
ALLOCATION = Decimal("0.60")
TOKEN_QUOTA = 600000
EXPECTED_NATIVE_QUOTA = 225000
NATIVE = "https://api.aixingtuyun.com"
NODY = "https://nodyhub.com"


class SafetyError(RuntimeError):
    """Evidence or ownership is insufficient; no new paid request is allowed."""


def require(condition: bool, message: str) -> None:
    """Stop safely without inventing authorization, charges or successful results."""
    if not condition:
        raise SafetyError(message)


def decimal(value: object) -> Decimal:
    """Parse exact bounded money, rejecting booleans and non-finite values."""
    require(not isinstance(value, bool), "Invalid price evidence")
    try:
        result = Decimal(str(value))
    except Exception:
        raise SafetyError("Invalid price evidence") from None
    require(result.is_finite() and result >= 0, "Invalid price evidence")
    return result


def private_path(path: Path) -> None:
    """Reject symlink ancestors before any secret read or owned artifact write."""
    require(".." not in path.absolute().parts, "Unsafe private path")
    for item in (path, *path.absolute().parents):
        require(not item.is_symlink(), "Private path contains a symlink")


def read_json(path: Path) -> dict:
    """Read a small private JSON object without logging its credentials."""
    private_path(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= 2 * 1024 * 1024, "Invalid private JSON")
    if os.name != "nt":
        require(stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid in {0, 10001, 10002}, "Private JSON ownership differs")
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), "Private JSON object required")
    return value


def write_private(path: Path, value: dict, *, replace: bool = False) -> None:
    """Fsync one owned 0600 intent before external action; new intents are exclusive."""
    private_path(path)
    if path.exists():
        read_json(path)
        require(replace, "Owned operation artifact already exists")
    target = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp") if replace else path
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            os.replace(target, path)
        if os.name != "nt":
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
    finally:
        if replace and target.exists(): target.unlink()


class Backend(Protocol):
    """Only the current operator, scoped token and read-only receipts are exposed."""
    def operator(self) -> dict: ...
    def quote_facts(self) -> dict: ...
    def create_token(self, name: str, key: str, expiry: int) -> dict: ...
    def token(self, token_id: int) -> dict: ...
    def post(self, body: dict, headers: dict) -> tuple[int, dict, bytes]: ...
    def native_logs(self, token_id: int) -> list[dict]: ...
    def upstream_logs(self) -> list[dict]: ...
    def disable_token(self, record: dict) -> None: ...
    def image_url_bytes(self, url: str) -> bytes: ...


def quote(facts: dict) -> dict:
    """Quote only the verified n=1, 1024 image tariffs and known CNY conversion."""
    token = facts.get("production_token", {})
    require(facts.get("native_routes_verified") is True, "Exact owned Nody route binding is unverified")
    require(type(token.get("id")) is int and token["id"] > 0 and type(token.get("status")) is int and facts.get("key_match_count") == 1 and token["status"] == 1, "Production key binding is not uniquely active")
    require(type(token.get("expired_time")) is int and (token["expired_time"] == -1 or token["expired_time"] > facts.get("observed_at", 0)), "Production key lifetime is unverified or expired")
    require(type(token.get("unlimited_quota")) is bool, "Production key quota scope is unavailable")
    if not token["unlimited_quota"]:
        require(type(token.get("remain_quota")) is int and token["remain_quota"] >= 100000, "Existing production key quota is insufficient")
    group = token.get("group")
    provider = facts.get("upstream_pricing", {})
    native = facts.get("native_pricing", {})
    require(decimal(provider.get("group_ratio", {}).get(group)) == 1, "Verified provider group factor changed")
    require(decimal(native.get("group_ratio", {}).get("图")) == Decimal("0.15"), "Native image group tariff changed")
    require(type(facts.get("nody_self", {}).get("quota")) is int and facts["nody_self"]["quota"] >= 100000, "Existing upstream funds are insufficient")
    allowed = set(str(token.get("model_limits", "")).split(","))
    if token.get("model_limits_enabled") is True:
        require(set(MODELS) <= allowed, "Production key does not allow both exact image SKUs")
    models = {}
    for model in MODELS:
        upstream = [row for row in provider.get("data", []) if row.get("model_name") == model]
        retail = [row for row in native.get("data", []) if row.get("model_name") == model]
        require(len(upstream) == len(retail) == 1, "Exact model tariff is missing or ambiguous")
        require(type(upstream[0].get("quota_type")) is int and type(retail[0].get("quota_type")) is int and upstream[0]["quota_type"] == retail[0]["quota_type"] == 1, "Per-call tariff evidence required")
        require(all(row.get("billing_mode") in {None, "", "fixed"} for row in (upstream[0], retail[0])), "Dynamic price is not quoted")
        require(decimal(upstream[0].get("model_price")) == Decimal("0.2") and decimal(retail[0].get("model_price")) == 3, "Verified image tariff changed")
        amount = decimal(upstream[0]["model_price"]) * RATE
        require(amount <= MAX_PER_IMAGE, "Approved image allocation exceeded")
        models[model] = {"max_cost_cny_exact": format(amount, ".6f"), "native_quota": EXPECTED_NATIVE_QUOTA}
    return {"currency": "CNY", "source_unit": "display_credit_per_image", "verified_cny_per_credit": "1.500000", "group": group, "production_token_id": token["id"], "allocation_cny_exact": "0.600000", "parent_total_budget_cny_exact": "20.000000", "models": models}


def validate_image(image: bytes) -> dict:
    """Validate full PNG structure/CRC/scanline length, not just its magic prefix.

    Non-PNG output remains unverified rather than silently claiming JPEG decode
    success without a decoder. It is preserved privately and never regenerated.
    """
    require(0 < len(image) <= 20 * 1024 * 1024 and image.startswith(b"\x89PNG\r\n\x1a\n"), "Image decoding is unverified")
    position, compressed, ended, dimensions = 8, bytearray(), False, None
    while position + 12 <= len(image):
        length = struct.unpack(">I", image[position:position + 4])[0]
        require(position + 12 + length <= len(image), "Incomplete PNG result")
        kind, data = image[position + 4:position + 8], image[position + 8:position + 8 + length]
        checksum = struct.unpack(">I", image[position + 8 + length:position + 12 + length])[0]
        require(zlib.crc32(kind + data) & 0xffffffff == checksum, "PNG integrity mismatch")
        if kind == b"IHDR":
            require(dimensions is None and length == 13, "Invalid PNG dimensions")
            dimensions = struct.unpack(">IIBBBBB", data)
        elif kind == b"IDAT": compressed.extend(data)
        elif kind == b"IEND": ended = True
        position += 12 + length
        if ended: break
    require(ended and position == len(image) and dimensions is not None, "Incomplete PNG result")
    width, height, depth, colour, compression, filtering, interlace = dimensions
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(colour)
    require((width, height, depth, compression, filtering, interlace) == (1024, 1024, 8, 0, 0, 0) and channels is not None, "PNG specification is unverified")
    expected = (width * channels + 1) * height
    decoder = zlib.decompressobj()
    raw = decoder.decompress(bytes(compressed), expected + 1)
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(raw) == expected, "PNG pixel stream is invalid")
    require(all(raw[index] <= 4 for index in range(0, expected, width * channels + 1)), "PNG scanline filter is invalid")
    return {"content_type": "image/png", "width": width, "height": height, "size_bytes": len(image), "sha256": hashlib.sha256(image).hexdigest()}


def serialized_stage(method: Callable) -> Callable:
    """Serialize all stages, so a second process cannot race the unresolved guard."""
    @functools.wraps(method)
    def execute(operation, *args, **kwargs):
        with operation.stage_lock():
            return method(operation, *args, **kwargs)
    return execute


class OperatorAcceptance:
    """Durable one-submit-per-model workflow; unresolved attempts block new POSTs."""
    def __init__(self, root: Path, backend: Backend, operation: str, clock: Callable[[], float] = time.time):
        require(re.fullmatch(r"[0-9a-f]{32}", operation) is not None, "Invalid operation identity")
        private_path(root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            require(root.stat().st_uid == 0 and stat.S_IMODE(root.stat().st_mode) == 0o700, "Operation root must be root-only")
        self.root, self.backend, self.operation, self.clock = root, backend, operation, clock
        self._lock_state = threading.local()
        owner = root / "OWNER.json"
        if owner.exists(): require(read_json(owner).get("operation") == operation, "Operation ownership differs")
        else: write_private(owner, {"operation": operation, "schema": "xtai-operator-image-acceptance-v1"})

    @contextmanager
    def stage_lock(self):
        """Lock one owned operation across processes; nested audit is reentrant."""
        if getattr(self._lock_state, "held", False):
            yield
            return
        path = self.root / "stage.lock"
        private_path(path)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode), "Invalid operation lock")
            if os.name != "nt":
                import fcntl
                require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, "Operation lock ownership differs")
                try: fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: raise SafetyError("Another operation stage is active") from None
            else:
                import msvcrt
                if info.st_size == 0: os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try: msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError: raise SafetyError("Another operation stage is active") from None
            self._lock_state.held = True
            yield
        finally:
            self._lock_state.held = False
            os.close(descriptor)

    def operator(self) -> None:
        """Only the already-funded production operator root1 is permitted."""
        user = self.backend.operator()
        require((user.get("id"), user.get("username"), user.get("role"), user.get("status")) == (1, "qiaoguan", 100, 1), "Required operator identity is unavailable")
        require(type(user.get("quota")) is int and user["quota"] >= TOKEN_QUOTA, "Existing operator quota is insufficient")

    def owned_token(self) -> dict:
        """Reject altered, expired or unrelated keys without touching their quota."""
        saved = read_json(self.root / "token.json")
        current = self.backend.token(saved["id"])
        for field in ("id", "user_id", "key", "name", "expired_time", "group", "model_limits", "model_limits_enabled", "unlimited_quota", "cross_group_retry"):
            require(current.get(field) == saved.get(field), "Operator token ownership or restrictions changed")
        require(current["user_id"] == 1 and current["group"] == "图" and current["remain_quota"] + current["used_quota"] == TOKEN_QUOTA, "Operator token quota scope changed")
        require(current.get("unlimited_quota") is False and current.get("model_limits_enabled") is True and current.get("cross_group_retry") is False and current.get("model_limits") == ",".join(MODELS), "Scoped key policy differs")
        intent = read_json(self.root / "prepare.intent.json")
        require(current["name"] == intent["name"] and current["key"] == intent["key"] and current["expired_time"] == intent["expiry"], "Scoped key differs from its pre-creation intent")
        return current

    @serialized_stage
    def prepare(self) -> dict:
        """Save authorization/quote/key intent before creating one limited root key."""
        self.operator()
        quoted = quote(self.backend.quote_facts())
        if (self.root / "token.json").exists():
            self.owned_token()
            return {"state": "prepared", "created": False}
        intent = self.root / "prepare.intent.json"
        if intent.exists():
            prepared = read_json(intent)
            require(prepared["quote"] == quoted and self.clock() < prepared["expiry"], "Prepared quote or lifetime changed")
        else:
            prepared = {"operation": self.operation, "name": "ops190-nody-" + self.operation, "key": secrets.token_hex(24), "expiry": int(self.clock()) + 3600, "quote": quoted}
            write_private(intent, prepared)
        token = self.backend.create_token(prepared["name"], prepared["key"], prepared["expiry"])
        write_private(self.root / "token.json", token)
        self.owned_token()
        require(self.backend.native_logs(token["id"]) == [], "New operator key has pre-existing log history")
        return {"state": "prepared", "created": True, "upstream_allocation_cny_exact": "0.600000"}

    @serialized_stage
    def submit(self, model: str) -> dict:
        """Persist one exclusive intent, perform one POST, never repeat uncertainty."""
        require(model in MODELS, "Only the two verified image SKUs are allowed")
        self.operator()
        token = self.owned_token()
        require(token["status"] == 1 and self.clock() < token["expired_time"], "Operator test token is inactive or expired")
        for candidate in MODELS:
            intent = self.root / (candidate + ".intent.json")
            result = self.root / (candidate + ".reconcile.json")
            if intent.exists():
                require(candidate != model and result.exists() and read_json(result)["state"] == "reconciled", "An existing paid attempt must not be replayed or followed while unresolved")
        fresh = quote(self.backend.quote_facts())
        require(fresh == read_json(self.root / "prepare.intent.json")["quote"], "Fresh quote differs from authorization")
        require(self.clock() < token["expired_time"], "Scoped key expired during quotation")
        require(token["remain_quota"] >= EXPECTED_NATIVE_QUOTA, "Remaining scoped test quota is insufficient")
        request_id = "ops190-" + self.operation + "-" + model
        body = {"model": model, "prompt": "A plain blue circle on a white background. No text.", "size": "1024x1024", "n": 1, "quality": "auto", "response_format": "b64_json"}
        write_private(self.root / (model + ".intent.json"), {"operation": self.operation, "model": model, "request_id": request_id, "created_at": self.clock(), "request": body, "quote": fresh, "maximum_exposure_cny_exact": "0.300000"})
        result = {"model": model, "state": "uncertain", "actual_cost_cny_exact": None}
        try:
            status, headers, raw = self.backend.post(body, {"Authorization": "Bearer sk-" + token["key"], "Content-Type": "application/json", "Idempotency-Key": request_id, "X-Request-ID": request_id})
            require(len(raw) <= 32 * 1024 * 1024, "Bounded image response exceeded")
            response = json.loads(raw)
            write_private(self.root / (model + ".response.json"), {"http": status, "headers": headers, "body": response})
            require(status == 200 and isinstance(response.get("data"), list) and len(response["data"]) == 1, "Image generation result is unconfirmed")
            row = response["data"][0]
            image = base64.b64decode(row["b64_json"], validate=True) if row.get("b64_json") else self.backend.image_url_bytes(row["url"])
            proof = validate_image(image)
            descriptor = os.open(self.root / (model + ".png"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as output: output.write(image); output.flush(); os.fsync(output.fileno())
            result.update(state="awaiting_reconcile", image=proof)
        except Exception as error:
            result["error_type"] = type(error).__name__
        write_private(self.root / (model + ".outcome.json"), result)
        return result

    def response_request_binding(self, model: str, native: dict, intent: dict) -> dict:
        """Bind a legacy missing native provider ID to the immutable owned response.

        The owned Nody adapter returns only an actual provider response ID in
        X-Oneapi-Request-Id. Ordinary native IDs and approximate observations are
        never used as provider receipts. Existing native claims must agree.
        """
        request = intent.get("request", {})
        require(intent.get("operation") == self.operation and intent.get("model") == model and request.get("model") == model and request.get("size") == "1024x1024" and type(request.get("n")) is int and request["n"] == 1 and request.get("quality") == "auto", "Original paid intent scope differs")
        claimed = native.get("upstream_request_id") or ""
        path = self.root / (model + ".response.json")
        if not path.exists():
            return {"request_id": claimed, "request_id_evidence": "native_log"} if claimed else {"reason": "original_response_missing"}
        private_path(path)
        before = path.stat()
        require(stat.S_ISREG(before.st_mode) and before.st_size <= 32 * 1024 * 1024, "Original response is not a bounded regular file")
        if os.name != "nt": require(before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o600, "Original response must remain operator-owned0600")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), "Original response changed before read")
            raw = handle.read(32 * 1024 * 1024 + 1)
            after = os.fstat(handle.fileno())
        current = path.stat()
        require(len(raw) <= 32 * 1024 * 1024 and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) == (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns), "Original response changed during read")
        digest = hashlib.sha256(raw).hexdigest()
        response = json.loads(raw)
        require(isinstance(response, dict) and isinstance(response.get("headers"), dict), "Original response headers are unavailable")
        headers = response["headers"]
        selected = {}
        for name in ("X-XingTu-Relay-Request-ID", "X-Oneapi-Request-Id"):
            values = [value for key, value in headers.items() if key.lower() == name.lower()]
            require(len(values) <= 1, "Original response contains ambiguous request headers")
            value = values[0] if values else ""
            selected[name] = value if isinstance(value, str) and not value.startswith("sk-") and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) else ""
        relay = selected["X-XingTu-Relay-Request-ID"]
        provider = selected["X-Oneapi-Request-Id"]
        pin = self.root / (model + ".response-binding.json")
        if pin.exists():
            saved = read_json(pin)
            require(saved.get("operation") == self.operation and saved.get("model") == model and saved.get("response_sha256") == digest and saved.get("native_request_id") == native.get("request_id") and saved.get("provider_request_id") == provider, "Pinned original response evidence changed")
        if relay != native.get("request_id") or not relay:
            return {"reason": "original_response_relay_native_id_mismatch"}
        if provider and provider != relay and claimed:
            require(claimed == provider, "Native upstream ID conflicts with the owned provider response header")
        if claimed:
            return {"request_id": claimed, "request_id_evidence": "native_log", "response_sha256": digest}
        if not provider or provider == relay:
            return {"reason": "original_response_has_no_distinct_provider_id"}
        facts = self.backend.quote_facts()
        if facts.get("native_routes_verified") is not True:
            return {"reason": "expected_nody_provider_route_unverified"}
        production = facts.get("production_token", {})
        if production.get("id") != intent["quote"]["production_token_id"] or production.get("group") != intent["quote"]["group"]:
            return {"reason": "expected_production_token_or_group_differs"}
        binding = {"operation": self.operation, "model": model, "response_sha256": digest, "native_request_id": relay, "provider_request_id": provider, "expected_provider_route_verified": True}
        if not pin.exists(): write_private(pin, binding)
        return {"request_id": provider, "request_id_evidence": "owned_response_exact_chain", "response_sha256": digest}

    @serialized_stage
    def reconcile(self, model: str) -> dict:
        """Use exact native-token/request and authenticated provider request receipts."""
        require(model in MODELS and (self.root / (model + ".intent.json")).exists(), "No owned original attempt exists")
        token = self.owned_token()
        native = [row for row in self.backend.native_logs(token["id"]) if row.get("type") in {2, 5} and row.get("user_id") == 1 and row.get("token_id") == token["id"] and row.get("model_name") == model]
        result = {"model": model, "state": "pending_cost", "actual_cost_cny_exact": None, "observed_at": self.clock()}
        intent = read_json(self.root / (model + ".intent.json"))
        if len(native) == 1:
            binding = self.response_request_binding(model, native[0], intent)
            result.update({key: value for key, value in binding.items() if key != "request_id"})
        else:
            binding = {}
            result["reason"] = "native_owned_consume_or_error_record_missing_or_ambiguous"
        if binding.get("request_id"):
            request_id = binding["request_id"]
            provider = [row for row in self.backend.upstream_logs() if row.get("type") == 2 and row.get("request_id") == request_id and row.get("model_name") == model]
            if len(provider) == 1:
                receipt = provider[0]
                require(receipt.get("token_id", intent["quote"]["production_token_id"]) == intent["quote"]["production_token_id"], "Provider receipt belongs to another token")
                require(receipt.get("group", intent["quote"]["group"]) == intent["quote"]["group"], "Provider receipt group differs")
                require(type(receipt.get("quota")) is int and receipt["quota"] > 0, "Actual billing quota is unavailable")
                amount = Decimal(receipt["quota"]) / Decimal(500000) * RATE
                result.update(actual_cost_cny_exact=format(amount, ".6f"), native_quota=native[0]["quota"], native_request_id=native[0].get("request_id"), upstream_request_id=request_id, provider_log_id=receipt.get("id"))
                outcome = self.root / (model + ".outcome.json")
                if amount > MAX_PER_IMAGE: result["state"] = "billing_bound_exceeded"
                elif native[0].get("type") == 2 and native[0].get("quota") != EXPECTED_NATIVE_QUOTA: result["state"] = "native_billing_mismatch"
                elif native[0].get("type") == 2 and outcome.exists() and read_json(outcome)["state"] == "awaiting_reconcile": result["state"] = "reconciled"
                else: result["state"] = "cost_confirmed_delivery_unknown"
            else:
                result["reason"] = "authenticated_exact_provider_receipt_missing_or_ambiguous"
        write_private(self.root / (model + ".reconcile.json"), result, replace=True)
        return result

    @serialized_stage
    def close(self) -> dict:
        """Disable only this temporary root key after terminal evidence or expiry audit."""
        token = self.owned_token()
        unresolved = []
        for model in MODELS:
            if (self.root / (model + ".intent.json")).exists():
                path = self.root / (model + ".reconcile.json")
                if not path.exists() or read_json(path)["state"] != "reconciled": unresolved.append(model)
        if unresolved:
            require(self.clock() >= token["expired_time"], "Unknown attempt retains its scoped key until expiry and audit")
            for model in unresolved:
                self.reconcile(model)
            unresolved = [model for model in unresolved if read_json(self.root / (model + ".reconcile.json"))["state"] != "reconciled"]
        require(token["status"] in {1, 2}, "Operator key status changed")
        if token["status"] == 1: self.backend.disable_token(token)
        result = {"state": "closed", "token_id": token["id"], "unresolved": unresolved, "wallet_adjustment": False}
        write_private(self.root / "closed.json", result, replace=True)
        return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


class LiveBackend:
    """Fixed-host server-local workflow; no login, recharge or arbitrary API access."""
    def __init__(self):
        require(sys.platform.startswith("linux") and os.geteuid() == 0, "CLI must run as production operator root")
        self.configuration = read_json(Path("/opt/xtai-nodyhub-image-adapter/config.json"))
        require(self.configuration["providers"]["nodyhub"]["base_url"].rstrip("/") == NODY, "Production image provider host differs")
        self.session = read_json(Path("/opt/xtai/secrets/video-billing/nodyhub-session.json"))
        self.headers = {name: str(self.session[field]) for name, field in (("Cookie", "cookie"), ("Authorization", "authorization"), ("New-Api-User", "new_api_user")) if self.session.get(field)}

    def sql(self, statement: str) -> list[dict]:
        command = ["docker", "exec", "-i", "ai-api-stack-postgres-1", "psql", "-U", "newapi", "-d", "new-api", "-XAt", "-v", "ON_ERROR_STOP=1"]
        value = subprocess.run(command, input=statement, capture_output=True, text=True, timeout=30)
        require(value.returncode == 0, "Scoped database operation failed")
        lines = [line for line in value.stdout.splitlines() if line.startswith("[")]
        require(len(lines) == 1, "Scoped database result is ambiguous")
        result = json.loads(lines[0]); require(isinstance(result, list), "Scoped rows required")
        return result

    @staticmethod
    def rows_query(query: str) -> str:
        return "SELECT coalesce(jsonb_agg(to_jsonb(r)),'[]'::jsonb) FROM (" + query + ") r;"

    @staticmethod
    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def operator(self) -> dict:
        rows = self.sql(self.rows_query("SELECT id,username,role,status,quota FROM users WHERE id=1"))
        require(len(rows) == 1, "Operator identity missing"); return rows[0]

    def request(self, method: str, url: str, headers: dict, body: dict | None = None) -> tuple[int, dict, bytes]:
        allowed = {NODY + path for path in ("/api/user/self", "/api/pricing", "/api/token/?p=1&size=100", "/api/log/self?p=1&page_size=100")} | {NATIVE + "/api/pricing", NATIVE + "/v1/images/generations"}
        require(url in allowed and (method == "GET" or (method == "POST" and url == NATIVE + "/v1/images/generations")), "Endpoint is outside the scoped workflow")
        request = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try: response = opener.open(request, timeout=240 if method == "POST" else 25)
        except urllib.error.HTTPError as error: response = error
        with response:
            content = response.read(32 * 1024 * 1024 + 1)
            require(len(content) <= 32 * 1024 * 1024, "Bounded response exceeded")
            selected = {name: response.headers.get(name) for name in ("X-XingTu-Relay-Request-ID", "X-Oneapi-Request-Id", "X-XingTu-Image-Submission-State") if response.headers.get(name)}
            return response.code, selected, content

    def get(self, url: str, headers: dict) -> dict:
        status, _, content = self.request("GET", url, headers)
        require(status == 200, "Authenticated evidence source unavailable")
        value = json.loads(content); require(isinstance(value, dict), "Evidence object required"); return value

    def quote_facts(self) -> dict:
        account = self.get(NODY + "/api/user/self", self.headers)
        listing = self.get(NODY + "/api/token/?p=1&size=100", self.headers)
        require(account.get("success") is True and listing.get("success") is True, "Upstream authorization unavailable")
        data = listing.get("data", {})
        rows = data.get("items", []) if isinstance(data, dict) else data
        require(isinstance(rows, list) and (not isinstance(data, dict) or data.get("total", len(rows)) <= 100), "Complete key inventory is unavailable")
        key = self.configuration["providers"]["nodyhub"]["key"].removeprefix("sk-")
        matched = [row for row in rows if str(row.get("key", "")).removeprefix("sk-") == key]
        production = {field: matched[0].get(field) for field in ("id", "status", "group", "model_limits_enabled", "model_limits", "expired_time", "unlimited_quota", "remain_quota")} if len(matched) == 1 else {}
        pricing = self.get(NODY + "/api/pricing", self.headers)
        native = self.get(NATIVE + "/api/pricing", {})
        require(pricing.get("success") is True and native.get("success") is True, "Current tariff source unavailable")
        routes = self.sql(self.rows_query('SELECT a.model,c.id,c.base_url,c.key,c.model_mapping FROM abilities a JOIN channels c ON c.id=a.channel_id WHERE a.enabled=true AND c.status=1 AND a."group"=\'图\' AND a.model IN (' + ",".join(self.literal(model) for model in MODELS) + ")"))
        verified = len(routes) == 2 and {row["model"] for row in routes} == set(MODELS) and len({row["id"] for row in routes}) == 1
        for row in routes:
            mapping = json.loads(row.get("model_mapping") or "{}")
            verified = verified and row["base_url"] == "http://xtai-nodyhub-image-adapter:8097/nodyhub" and row["key"] == self.configuration["adapter_token"] and isinstance(mapping, dict) and mapping.get(row["model"], row["model"]) == row["model"]
        return {"key_match_count": len(matched), "production_token": production, "observed_at": int(time.time()), "native_routes_verified": verified, "nody_self": account.get("data", {}), "upstream_pricing": pricing, "native_pricing": native}

    def create_token(self, name: str, key: str, expiry: int) -> dict:
        require(re.fullmatch(r"ops190-nody-[0-9a-f]{32}", name) is not None and re.fullmatch(r"[0-9a-f]{48}", key) is not None, "Scoped key identity invalid")
        existing = self.sql(self.rows_query("SELECT * FROM tokens WHERE user_id=1 AND name=" + self.literal(name)))
        if existing:
            require(len(existing) == 1 and existing[0]["key"] == key, "Prepared key outcome requires inspection")
            return existing[0]
        now = int(time.time()); require(now < expiry <= now + 3600, "Token lifetime exceeds one hour")
        fields = 'user_id,key,status,name,created_time,accessed_time,expired_time,remain_quota,unlimited_quota,model_limits_enabled,model_limits,allow_ips,used_quota,"group",cross_group_retry'
        values = "1," + self.literal(key) + ",1," + self.literal(name) + f",{now},{now},{expiry},600000,false,true," + self.literal(",".join(MODELS)) + ",'',0,'图',false"
        statement = "BEGIN;SELECT pg_advisory_xact_lock(hashtext(" + self.literal(name) + "));WITH inserted AS (INSERT INTO tokens(" + fields + ") SELECT " + values + " FROM users WHERE id=1 AND username='qiaoguan' AND role=100 AND status=1 AND quota>=600000 AND NOT EXISTS(SELECT 1 FROM tokens WHERE name=" + self.literal(name) + ") RETURNING *) SELECT coalesce(jsonb_agg(to_jsonb(inserted)),'[]'::jsonb) FROM inserted;COMMIT;"
        result = self.sql(statement); require(len(result) == 1, "Scoped token creation outcome requires inspection"); return result[0]

    def token(self, token_id: int) -> dict:
        require(type(token_id) is int and token_id > 0, "Invalid scoped token id")
        rows = self.sql(self.rows_query("SELECT * FROM tokens WHERE id=" + str(token_id) + " AND user_id=1"))
        require(len(rows) == 1, "Scoped token unavailable"); return rows[0]

    def post(self, body: dict, headers: dict) -> tuple[int, dict, bytes]:
        require(set(body) == {"model", "prompt", "size", "n", "quality", "response_format"} and body.get("model") in MODELS and type(body.get("n")) is int and body["n"] == 1 and body.get("size") == "1024x1024" and body.get("quality") == "auto" and body.get("response_format") == "b64_json", "Unapproved paid tuple")
        return self.request("POST", NATIVE + "/v1/images/generations", headers, body)

    def native_logs(self, token_id: int) -> list[dict]:
        return self.sql(self.rows_query("SELECT type,user_id,token_id,model_name,request_id,upstream_request_id,quota FROM logs WHERE user_id=1 AND token_id=" + str(int(token_id))))

    def upstream_logs(self) -> list[dict]:
        payload = self.get(NODY + "/api/log/self?p=1&page_size=100", self.headers)
        require(payload.get("success") is True, "Authenticated consumption records unavailable")
        data = payload.get("data", {}); rows = data.get("items", []) if isinstance(data, dict) else data
        require(isinstance(rows, list), "Authenticated consumption rows unavailable"); return rows

    def disable_token(self, record: dict) -> None:
        statement = "WITH changed AS (UPDATE tokens SET status=2 WHERE id=" + str(record["id"]) + " AND user_id=1 AND key=" + self.literal(record["key"]) + " AND name=" + self.literal(record["name"]) + " AND status=1 RETURNING id) SELECT coalesce(jsonb_agg(to_jsonb(changed)),'[]'::jsonb) FROM changed;"
        rows = self.sql(statement); require(len(rows) == 1, "Owned token deactivation requires inspection")

    def image_url_bytes(self, url: str) -> bytes:
        path = Path("/opt/xtai-nodyhub-image-adapter/image_io.py")
        private_path(path)
        require(hashlib.sha256(path.read_bytes()).hexdigest() == "7834a058614143542dd979fb57fa83d06676a4ed53beff9449c48f45381196d1", "Verified URL reader unavailable")
        spec = importlib.util.spec_from_file_location("owned_nody_image_io", path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module.image_bytes(url)


def main() -> int:
    """Execute one explicitly requested root stage, never an automatic test loop."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "submit", "reconcile", "close"])
    parser.add_argument("--operation", required=True)
    parser.add_argument("--model", choices=MODELS)
    args = parser.parse_args()
    require(re.fullmatch(r"[0-9a-f]{32}", args.operation) is not None, "Invalid operation")
    backend = LiveBackend()
    operation = OperatorAcceptance(Path("/opt/ai-api-stack/backups/issue190-image-acceptance-" + args.operation), backend, args.operation)
    if args.action in {"submit", "reconcile"}: require(args.model in MODELS, "Model required")
    result = getattr(operation, args.action)(args.model) if args.action in {"submit", "reconcile"} else getattr(operation, args.action)()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"needs_attention": True, "error_type": type(error).__name__, "new_paid_retry": False}))
        raise SystemExit(1)
