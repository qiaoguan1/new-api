#!/usr/bin/env python3
"""Translate OpenAI image-generation requests to validated chat-image upstreams."""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import stat
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping


MAX_REQUEST_BYTES = 64 * 1024
MAX_PROMPT_CHARACTERS = 32_000
MAX_UPSTREAM_RESPONSE_BYTES = 2 * 1024 * 1024
SUPPORTED_MODELS = ("banana-flash", "banana-pro")
SAFE_REJECTION_MARKERS = (
    "no available channel",
    "no available compatible accounts",
    "pool unavailable",
    "temporarily unavailable",
    "currently overloaded",
    "model_not_found",
)
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((https://[^\s)]+|data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+)\)",
    re.IGNORECASE,
)
DATA_IMAGE_RE = re.compile(
    r"data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+", re.IGNORECASE
)
SIZE_RE = re.compile(r"^(\d{2,4})x(\d{2,4})$")


class AdapterError(Exception):
    """A safe client-visible adapter failure."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class UpstreamRejected(AdapterError):
    """An explicit fast rejection proving that upstream generation did not start."""

    def __init__(self, provider: str, upstream_status: int, message: str, elapsed: float) -> None:
        super().__init__(HTTPStatus.BAD_GATEWAY, "upstream_rejected", "upstream rejected the request")
        self.provider = provider
        self.upstream_status = upstream_status
        self.upstream_message = message
        self.elapsed = elapsed


class UpstreamUncertain(AdapterError):
    """A transport outcome that must not be replayed automatically."""

    def __init__(self, provider: str, reason: str) -> None:
        super().__init__(
            HTTPStatus.BAD_GATEWAY,
            "upstream_outcome_uncertain",
            "upstream outcome is uncertain; request was not retried",
        )
        self.provider = provider
        self.reason = reason


@dataclass(frozen=True)
class Route:
    provider: str
    base_url: str
    api_key: str
    upstream_model: str


@dataclass(frozen=True)
class GenerationRequest:
    model: str
    prompt: str
    size: str


@dataclass(frozen=True)
class UpstreamResult:
    provider: str
    image_references: list[str]
    status: int
    elapsed: float


def read_private_secret(path_value: str) -> str:
    """Read a small regular 0600 secret file without following symlinks."""
    path = Path(path_value)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise RuntimeError("secret path must be a regular file")
    if os.name == "posix" and stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("secret file must use mode 0600")
    if info.st_size < 8 or info.st_size > 4096:
        raise RuntimeError("secret file size is invalid")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < 8:
        raise RuntimeError("secret value is invalid")
    return value


def validate_https_base_url(value: str, label: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip().rstrip("/"))
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} must be an HTTPS URL")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    adapter_token: str
    upstream_timeout_seconds: float
    max_active_requests: int
    routes: dict[str, tuple[Route, ...]]

    @classmethod
    def from_env(cls, environment: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if environment is None else environment
        adapter_token = read_private_secret(str(env.get("BANANA_ADAPTER_TOKEN_FILE") or ""))
        haina_key = read_private_secret(str(env.get("BANANA_HAINA_KEY_FILE") or ""))
        rolldek_key = read_private_secret(str(env.get("BANANA_ROLLDEK_KEY_FILE") or ""))
        haina_base = validate_https_base_url(
            str(env.get("BANANA_HAINA_BASE_URL") or "https://ai.0809.one/v1"), "Haina base URL"
        )
        rolldek_base = validate_https_base_url(
            str(env.get("BANANA_ROLLDEK_BASE_URL") or "https://rolldek.com/v1"),
            "Rolldek base URL",
        )
        timeout = float(env.get("BANANA_UPSTREAM_TIMEOUT_SECONDS") or 180)
        if not 10 <= timeout <= 300:
            raise RuntimeError("upstream timeout must be between 10 and 300 seconds")
        max_active = int(env.get("BANANA_MAX_ACTIVE_REQUESTS") or 8)
        if not 1 <= max_active <= 64:
            raise RuntimeError("max active requests must be between 1 and 64")
        port = int(env.get("BANANA_ADAPTER_PORT") or 8093)
        if not 1 <= port <= 65535:
            raise RuntimeError("adapter port is invalid")
        return cls(
            host=str(env.get("BANANA_ADAPTER_HOST") or "0.0.0.0"),
            port=port,
            adapter_token=adapter_token,
            upstream_timeout_seconds=timeout,
            max_active_requests=max_active,
            routes={
                "banana-flash": (
                    Route("haina", haina_base, haina_key, "gemini-3.1-flash-image-preview"),
                    Route("rolldek", rolldek_base, rolldek_key, "gemini-3.1-flash-image-preview"),
                ),
                "banana-pro": (
                    Route("rolldek", rolldek_base, rolldek_key, "gemini-3-pro-image-preview"),
                ),
            },
        )


def validate_generation_request(raw: Any) -> GenerationRequest:
    if not isinstance(raw, dict):
        raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_request", "request body must be an object")
    model = str(raw.get("model") or "").strip()
    if model not in SUPPORTED_MODELS:
        raise AdapterError(HTTPStatus.BAD_REQUEST, "unsupported_model", "unsupported model")
    prompt = raw.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARACTERS:
        raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_prompt", "prompt is missing or too long")
    if isinstance(raw.get("n", 1), bool) or raw.get("n", 1) != 1:
        raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_n", "only n=1 is supported")
    if str(raw.get("response_format") or "url") != "url":
        raise AdapterError(
            HTTPStatus.BAD_REQUEST,
            "unsupported_response_format",
            "only URL responses are supported",
        )
    size = str(raw.get("size") or "1024x1024").strip().lower()
    match = SIZE_RE.fullmatch(size)
    if not match or any(not 256 <= int(value) <= 4096 for value in match.groups()):
        raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_size", "image size is invalid")
    return GenerationRequest(model=model, prompt=prompt.strip(), size=size)


def validate_content_length(value: str) -> int:
    try:
        content_length = int(value or 0)
    except ValueError as error:
        raise AdapterError(
            HTTPStatus.BAD_REQUEST, "invalid_request_size", "request size is invalid"
        ) from error
    if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
        raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_request_size", "request size is invalid")
    return content_length


def valid_image_reference(value: str) -> bool:
    if value.startswith("https://"):
        parsed = urllib.parse.urlsplit(value)
        return bool(parsed.hostname) and not parsed.username and not parsed.password
    if DATA_IMAGE_RE.fullmatch(value):
        try:
            base64.b64decode(value.split(",", 1)[1], validate=True)
        except (ValueError, TypeError):
            return False
        return True
    return False


def extract_image_references(content: Any) -> list[str]:
    """Extract validated HTTPS or data-URI images from a chat message."""
    candidates: list[str] = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            image_url = item.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            value = image_url or item.get("url")
            if isinstance(value, str):
                candidates.append(value.strip())
    elif isinstance(content, str):
        candidates.extend(MARKDOWN_IMAGE_RE.findall(content))
        if not candidates:
            candidates.extend(DATA_IMAGE_RE.findall(content))
        if not candidates and content.strip().startswith("https://") and "\n" not in content.strip():
            candidates.append(content.strip())
    result: list[str] = []
    for candidate in candidates:
        if valid_image_reference(candidate) and candidate not in result:
            result.append(candidate)
    if not result:
        raise AdapterError(HTTPStatus.BAD_GATEWAY, "upstream_image_empty", "upstream returned no image")
    return result


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def extract_upstream_error(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("code") or "")[:500]
    return str(payload.get("message") or error or "")[:500]


def safe_explicit_rejection(status: int, message: str, elapsed: float) -> bool:
    lowered = message.lower()
    return (
        elapsed <= 5
        and status in {400, 404, 409, 429, 500, 502, 503}
        and any(marker in lowered for marker in SAFE_REJECTION_MARKERS)
    )


def call_upstream(route: Route, request: GenerationRequest, timeout: float) -> UpstreamResult:
    prompt = request.prompt + f"\n\nRequested output canvas: {request.size} pixels. Return one image."
    body = {
        "model": route.upstream_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    upstream_request = urllib.request.Request(
        route.base_url + "/chat/completions",
        data=encoded,
        method="POST",
        headers={
            "Authorization": "Bearer " + route.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "xingtu-banana-adapter/1.0",
        },
    )
    opener = urllib.request.build_opener(NoRedirectHandler())
    started = time.monotonic()
    status = 0
    response_body = b""
    try:
        with opener.open(upstream_request, timeout=timeout) as response:
            status = int(response.status)
            response_body = response.read(MAX_UPSTREAM_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        response_body = error.read(MAX_UPSTREAM_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UpstreamUncertain(route.provider, type(error).__name__) from error
    elapsed = round(time.monotonic() - started, 3)
    if len(response_body) > MAX_UPSTREAM_RESPONSE_BYTES:
        raise AdapterError(
            HTTPStatus.BAD_GATEWAY,
            "upstream_response_too_large",
            "upstream response exceeded the safety limit",
        )
    try:
        payload = json.loads(response_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AdapterError(
            HTTPStatus.BAD_GATEWAY, "upstream_invalid_response", "upstream returned invalid JSON"
        ) from error
    if status != HTTPStatus.OK:
        message = extract_upstream_error(payload)
        if safe_explicit_rejection(status, message, elapsed):
            raise UpstreamRejected(route.provider, status, message, elapsed)
        raise AdapterError(
            HTTPStatus.BAD_GATEWAY, "upstream_failed", "upstream request failed without safe fallback"
        )
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise AdapterError(HTTPStatus.BAD_GATEWAY, "upstream_invalid_response", "upstream returned no choice")
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return UpstreamResult(route.provider, extract_image_references(content), status, elapsed)


def generate_with_routes(
    request: GenerationRequest,
    routes: Mapping[str, tuple[Route, ...]],
    caller: Callable[[Route, GenerationRequest], UpstreamResult],
) -> UpstreamResult:
    candidates = routes.get(request.model) or ()
    if not candidates:
        raise AdapterError(HTTPStatus.SERVICE_UNAVAILABLE, "model_unavailable", "model has no route")
    last_rejection: UpstreamRejected | None = None
    for index, route in enumerate(candidates):
        try:
            return caller(route, request)
        except UpstreamRejected as error:
            last_rejection = error
            if index + 1 >= len(candidates):
                raise
    if last_rejection is not None:
        raise last_rejection
    raise AdapterError(HTTPStatus.BAD_GATEWAY, "upstream_failed", "upstream request failed")


def image_response(result: UpstreamResult) -> dict[str, Any]:
    data = []
    for reference in result.image_references:
        if reference.startswith("data:image/"):
            data.append({"b64_json": reference.split(",", 1)[1]})
        else:
            data.append({"url": reference})
    return {"created": int(time.time()), "data": data}


def error_response(error: AdapterError) -> dict[str, Any]:
    """Return only the adapter's bounded message, never raw upstream details."""
    return {"error": {"message": error.message, "type": "adapter_error", "code": error.code}}


def authorized(authorization: str, expected_token: str) -> bool:
    prefix = "Bearer "
    return authorization.startswith(prefix) and hmac.compare_digest(
        authorization[len(prefix) :].strip(), expected_token
    )


class Runtime:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.semaphore = threading.BoundedSemaphore(config.max_active_requests)
        self.lock = threading.Lock()
        self.active = 0
        self.completed = 0
        self.failed = 0

    def readiness(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "service": "banana-chat-adapter",
                "accepting": True,
                "active_requests": self.active,
                "completed": self.completed,
                "failed": self.failed,
                "models": list(SUPPORTED_MODELS),
            }

    def start_request(self) -> bool:
        if not self.semaphore.acquire(blocking=False):
            return False
        with self.lock:
            self.active += 1
        return True

    def finish_request(self, success: bool) -> None:
        with self.lock:
            self.active -= 1
            if success:
                self.completed += 1
            else:
                self.failed += 1
        self.semaphore.release()


def handler_class(runtime: Runtime) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "banana-chat-adapter"
        sys_version = ""

        def log_message(self, format: str, *args: object) -> None:
            return

        def send_json(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def require_authorization(self) -> None:
            if not authorized(self.headers.get("Authorization", ""), runtime.config.adapter_token):
                raise AdapterError(HTTPStatus.UNAUTHORIZED, "invalid_api_key", "invalid API key")

        def do_GET(self) -> None:
            try:
                path = urllib.parse.urlsplit(self.path).path
                if path == "/health":
                    self.send_json(HTTPStatus.OK, {"ok": True, "service": "banana-chat-adapter"})
                    return
                if path == "/ready":
                    self.send_json(HTTPStatus.OK, runtime.readiness())
                    return
                if path == "/v1/models":
                    self.require_authorization()
                    self.send_json(
                        HTTPStatus.OK,
                        {
                            "object": "list",
                            "data": [
                                {"id": model, "object": "model", "owned_by": "xingtu"}
                                for model in SUPPORTED_MODELS
                            ],
                        },
                    )
                    return
                raise AdapterError(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
            except AdapterError as error:
                self.send_json(error.status, error_response(error))

        def do_POST(self) -> None:
            success = False
            acquired = False
            request_id = uuid.uuid4().hex
            try:
                if urllib.parse.urlsplit(self.path).path != "/v1/images/generations":
                    raise AdapterError(HTTPStatus.NOT_FOUND, "not_found", "resource not found")
                self.require_authorization()
                content_length = validate_content_length(self.headers.get("Content-Length") or "")
                try:
                    raw = json.loads(self.rfile.read(content_length).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise AdapterError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body is invalid JSON") from error
                request = validate_generation_request(raw)
                acquired = runtime.start_request()
                if not acquired:
                    raise AdapterError(HTTPStatus.SERVICE_UNAVAILABLE, "adapter_busy", "adapter is busy")
                result = generate_with_routes(
                    request,
                    runtime.config.routes,
                    lambda route, generation: call_upstream(
                        route, generation, runtime.config.upstream_timeout_seconds
                    ),
                )
                success = True
                print(
                    json.dumps(
                        {
                            "event": "generation_completed",
                            "request_id": request_id,
                            "model": request.model,
                            "provider": result.provider,
                            "elapsed_seconds": result.elapsed,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                self.send_json(HTTPStatus.OK, image_response(result))
            except AdapterError as error:
                print(
                    json.dumps(
                        {
                            "event": "generation_failed",
                            "request_id": request_id,
                            "code": error.code,
                        },
                        separators=(",", ":"),
                    ),
                    flush=True,
                )
                self.send_json(
                    error.status,
                    error_response(error),
                )
            finally:
                if acquired:
                    runtime.finish_request(success)

    return Handler


def main() -> None:
    config = Config.from_env()
    runtime = Runtime(config)
    server = ThreadingHTTPServer((config.host, config.port), handler_class(runtime))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
