"""Build a reversible, main-host-only native maintenance gate.

Task polling, media delivery and sidecar settlement endpoints stay open. The
native process has no graceful shutdown, so all native write methods, native
GET side effects and realtime admission are paused until the verified swap.
Only a protected candidate file is built here; this module never applies it.
"""
from __future__ import annotations

import re

HOST_ANCHOR = b"    server_name api.aixingtuyun.com aixingtuyun.com www.aixingtuyun.com;\n"
PAUSE_PATTERNS = (
    r"^(POST|PUT|PATCH|DELETE):/(api|pg|v1|v1beta|mj|suno)(/|$)",
    r"^(POST|PUT|PATCH|DELETE):/[^/]+/mj(/|$)",
    r"^(GET|HEAD):/v1/realtime(/|$)",
    r"^(GET|HEAD):/api/(oauth(/|$)|verification(/|$)|reset_password(/|$))",
    r"^(GET|HEAD):/api/user/(logout|token|aff|wechatpay/order|epay/notify)(/|$)",
    r"^(GET|HEAD):/api/channel/(test|update_balance|fetch_models)(/|$)",
    r"^(GET|HEAD):/api/subscription/epay/(notify|return)(/|$)",
    r"^(GET|HEAD):/api/models/sync_upstream/preview(/|$)",
    r"^POST:/internal/xtai-image-jobs/v1/(image-jobs|images/generations)/?$",
    r"^POST:/internal/xtai-video-jobs/v1/(video-jobs|videos)/?$",
)


def is_paused(method: str, canonical_uri: str) -> bool:
    """Model the gate for an already canonicalized Nginx URI, without I/O."""
    value = method + ":" + canonical_uri
    return any(re.search(pattern, value) for pattern in PAUSE_PATTERNS)


def build_gate(original: bytes, operation: str) -> bytes:
    """Insert one reviewed gate without rewriting unrelated server blocks."""
    if re.fullmatch(r"[0-9a-f]{32}", operation) is None:
        raise ValueError("A fixed operator identity is required")
    if original.count(HOST_ANCHOR) != 1 or b"issue190_native_pause" in original or b"18090" in original:
        raise ValueError("Ambiguous main host or pre-existing maintenance state")
    if original.count(b"proxy_pass http://new-api:3000;") != 1:
        raise ValueError("Other native ingress needs separate review")
    variable = "$issue190_native_pause"
    mapping = 'map "$request_method:$uri" ' + variable + " {\n    default 0;\n"
    mapping += "".join('    ~"' + pattern + '" 1;\n' for pattern in PAUSE_PATTERNS)
    mapping += "}\n\n"
    # Named error redirect preserves the original method and allows response
    # headers in a location (add_header is invalid inside a server-level if).
    gate = (
        "\n    # issue190-native-" + operation + "\n"
        "    error_page 418 = @issue190_native_pause;\n"
        "    if (" + variable + " = 1) { return 418; }\n"
        "    location @issue190_native_pause {\n"
        "        internal;\n"
        "        default_type application/json;\n"
        "        add_header Cache-Control no-store always;\n"
        "        add_header Retry-After 2 always;\n"
        "        add_header X-XingTu-Image-Submission-State not_submitted always;\n"
        "        add_header X-XingTu-Relay-Request-ID $request_id always;\n"
        "        return 503 '{\"error\":{\"code\":\"relay_maintenance\",\"message\":\"issue190-native-" + operation + ": new work not submitted; retry after maintenance\"}}';\n"
        "    }\n"
    ).encode()
    position = original.index(HOST_ANCHOR) + len(HOST_ANCHOR)
    body = original[:position] + gate + original[position:]
    return mapping.encode() + body + b"\nserver { listen 127.0.0.1:18090; location / { stub_status; } }\n"
