"""Pure v2 video bodies from installed Nody model-specific request builders.

Evidence: ``D:/Nody/resources/backend-dist/main.js`` (read, never executed),
Wan ``assembleWan3VideoBody``, Omni ``assembleOmniFlashVideoBody``, FLUX
``assembleFlux3VideoBody``, and the three Grok parameter mappings/transforms.
This only describes candidate wire contracts; it does not grant production
admission, verify downloaded media, establish prices, or authorize a paid call.
Legacy text requests deliberately remain outside this module.
"""

from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import re
from urllib.parse import urlsplit


_WAN_MODELS = {"wan3.0-video", "wan3.0-video-prime"}
_GROK_MODELS = {"grok-video-3", "grok-imagine-1.5-video", "grok-imagine-video-official"}
_FRAME_ROLES = {
    "first_frame": ("first_frame",),
    "last_frame": ("last_frame",),
    "first_last_frame": ("first_frame", "last_frame"),
}
_REFERENCE_MODES = {"reference", "all_reference"}
_ASPECT_RATIOS = {
    "wan3.0-video": {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"},
    "wan3.0-video-prime": {"adaptive", "16:9", "4:3", "1:1", "3:4", "9:16"},
    "omni-flash": {"16:9", "9:16"},
    "flux-3-video": {"adaptive", "21:9", "2:1", "16:9", "4:3", "1:1", "3:4", "9:16"},
    "grok-video-3": {"16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "1:1"},
    "grok-imagine-1.5-video": {"16:9", "9:16", "1:1", "3:2", "2:3"},
    "grok-imagine-video-official": {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"},
}


def _public_https_url(value: object) -> str:
    """Reject unsafe URL syntax and literal/local destinations without DNS I/O."""
    if (not isinstance(value, str) or not value or len(value) > 4096
            or "\\" in value or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("asset url must be a public HTTPS URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError as error:
        raise ValueError("asset URL is malformed") from error
    if (parsed.scheme != "https" or not host or parsed.username is not None or parsed.password is not None
            or parsed.fragment or "%" in host or port not in (None, 443)):
        raise ValueError("asset url must be a public HTTPS URL without credentials or fragments")
    host = host.lower().rstrip(".")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global or address.is_multicast:
            raise ValueError("asset URL cannot target a private or reserved address")
    else:
        labels = host.split(".")
        if (len(labels) < 2 or labels[-1].isdigit()
                or host.endswith((".localhost", ".local", ".internal", ".lan"))
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
            raise ValueError("asset URL must use a public hostname")
    return value


def _canonical_assets(payload: Mapping[str, object], field: str) -> list[dict[str, str]]:
    """Read canonical arrays without silently dropping invalid entries/roles."""
    raw = payload.get(field, [])
    if not isinstance(raw, list):
        raise ValueError(f"{field} must be an array")
    result = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{field} entries must be canonical asset objects")
        role = item.get("role")
        if not isinstance(role, str) or role not in {"reference", "first_frame", "last_frame"}:
            raise ValueError(f"{field} contains an unsupported role")
        result.append({"url": _public_https_url(item.get("url")), "role": role})
    return result


def build_media_body(model: str, payload: Mapping[str, object]) -> dict[str, object]:
    """Build a strict Nody v2 JSON body for normalized, non-text inputs.

    ``payload`` uses lowercase canonical resolution, bounded integer duration,
    explicit boolean ``generate_audio``, ``aspect_ratio``, and ``images`` /
    ``videos`` / ``audios`` arrays of objects with ``url`` and ``role``. Extra
    asset metadata (including content identity) stays local. ``reference`` is
    exactly one material and ``all_reference`` at least two materials. Frame
    roles are explicit and ordered by semantic role, not input array position.

    Raises ``ValueError`` instead of coercing, truncating, defaulting invalid
    parameters, or dropping unsupported assets. DNS/content/quote verification
    belongs to the caller. No input is mutated and no I/O is performed.
    """
    if not isinstance(model, str) or model not in _ASPECT_RATIOS:
        raise ValueError("unsupported Nody media model")
    if not isinstance(payload, Mapping) or payload.get("model", model) != model:
        raise ValueError("canonical payload model mismatch")
    if any(field in payload for field in ("file_url", "link_url")):
        raise ValueError("unverified file/link inputs are not part of this media contract")
    mode = payload.get("mode")
    supported_modes = _REFERENCE_MODES
    if model in _WAN_MODELS:
        supported_modes = _REFERENCE_MODES | _FRAME_ROLES.keys()
    elif model == "flux-3-video":
        supported_modes = {"first_frame", "first_last_frame", "all_reference"}
    if not isinstance(mode, str) or mode not in supported_modes:
        raise ValueError("unsupported Nody media mode")

    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    prompt = prompt.strip()
    if not prompt and model not in _WAN_MODELS:
        raise ValueError("this model requires a nonempty prompt")
    duration = payload.get("duration")
    resolution = payload.get("resolution")
    aspect_ratio = payload.get("aspect_ratio")
    generate_audio = payload.get("generate_audio")
    if type(duration) is not int:
        raise ValueError("duration must be a bounded integer")
    if not isinstance(resolution, str) or resolution != resolution.lower():
        raise ValueError("resolution must be canonical lowercase")
    if not isinstance(aspect_ratio, str) or aspect_ratio not in _ASPECT_RATIOS[model]:
        raise ValueError("unsupported aspect ratio")
    if type(generate_audio) is not bool:
        raise ValueError("generate_audio must be an explicit boolean")
    if model not in _WAN_MODELS | {"flux-3-video"} and not generate_audio:
        raise ValueError("this model has no verified generated-audio toggle")

    images = _canonical_assets(payload, "images")
    videos = _canonical_assets(payload, "videos")
    audios = _canonical_assets(payload, "audios")
    material_count = len(images) + len(videos) + len(audios)
    if mode in _FRAME_ROLES:
        expected_roles = _FRAME_ROLES[mode]
        if (videos or audios or len(images) != len(expected_roles)
                or sorted(item["role"] for item in images) != sorted(expected_roles)):
            raise ValueError("frame mode requires its exact image roles and no video/audio")
        images = [next(item for item in images if item["role"] == role) for role in expected_roles]
    else:
        if (any(item["role"] != "reference" for item in images + videos + audios)
                or (mode == "reference" and material_count != 1)
                or (mode == "all_reference" and material_count < 2)):
            raise ValueError("reference mode requires its exact material count and reference roles")

    body: dict[str, object] = {"model": model, "duration": duration}
    if prompt:
        body["prompt"] = prompt
    image_urls = [item["url"] for item in images]
    video_urls = [item["url"] for item in videos]
    audio_urls = [item["url"] for item in audios]
    if model in _WAN_MODELS:
        if resolution not in {"480p", "720p", "1080p"} or not 2 <= duration <= 30:
            raise ValueError("unsupported Wan resolution/duration; inherited duration is not priced")
        if len(images) > 10 or len(videos) > 5 or len(audios) > 5:
            raise ValueError("Wan material count exceeds the wire contract")
        body.update(resolution=resolution.upper(), audio=generate_audio, watermark=False)
        if mode in _FRAME_ROLES:
            body["generation_type"] = "frame"
            if mode == "last_frame":
                body["image_with_roles"] = [{"url": image_urls[0], "role": "last_frame"}]
            else:
                body["image_urls"] = image_urls
        else:
            body.update(generation_type="reference", size=aspect_ratio)
            for field, urls in (("image_urls", image_urls), ("video_urls", video_urls), ("audio_urls", audio_urls)):
                if urls:
                    body[field] = urls
        return body

    if model == "omni-flash":
        if (resolution not in {"720p", "1080p", "4k"} or len(images) not in {0, 1, 3}
                or len(videos) > 1 or audios or not 4 <= duration <= 30
                or (not videos and duration not in {4, 6, 8, 10})):
            raise ValueError("unsupported Omni media/resolution/duration contract")
        body.update(resolution=resolution, aspect_ratio=aspect_ratio)
        if image_urls:
            body["image_urls"] = image_urls
        if video_urls:
            body["video_urls"] = video_urls
        return body

    if model == "flux-3-video":
        if (resolution not in {"720p", "1080p"} or not 5 <= duration <= 20
                or len(images) > 10 or videos or audios):
            raise ValueError("unsupported FLUX media/resolution/duration contract")
        body.update(resolution=resolution, aspect_ratio="auto" if aspect_ratio == "adaptive" else aspect_ratio,
                    audio=generate_audio, safety_tolerance=4, image_urls=image_urls)
        return body

    if model in _GROK_MODELS:
        if not 1 <= len(images) <= 7 or videos or audios:
            raise ValueError("Grok requires one to seven reference images and no video/audio")
        if model == "grok-video-3":
            if resolution != "720p" or duration not in {6, 10, 15, 20, 25, 30}:
                raise ValueError("unsupported Grok Video 3 resolution/duration")
            body.update(resolution="720P", images=image_urls)
            if len(images) > 1:
                body["ratio"] = aspect_ratio
        elif model == "grok-imagine-1.5-video":
            if resolution not in {"480p", "720p"} or not 6 <= duration <= 30:
                raise ValueError("unsupported Grok Imagine 1.5 resolution/duration")
            body.update(quality=resolution, image_urls=image_urls)
            if len(images) > 1:
                body["size"] = aspect_ratio
        else:
            if resolution not in {"480p", "720p"} or not 1 <= duration <= 15:
                raise ValueError("unsupported official Grok resolution/duration")
            body["resolution"] = resolution
            if len(images) == 1:
                body["image"] = {"url": image_urls[0]}
            else:
                body["reference_images"] = [{"url": url} for url in image_urls]
                body["aspect_ratio"] = aspect_ratio
        return body

    raise ValueError("unsupported Nody media model")
