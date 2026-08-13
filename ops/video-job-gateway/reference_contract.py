"""Pure validation and stable identity helpers for the gated v2.2 contract."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.parse
from decimal import Decimal, InvalidOperation
from typing import Any


SHA256 = re.compile(r"[0-9a-f]{64}")
SIX_DECIMALS = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{6}")
MAX_VIDEO_COUNT = 3
MAX_AUDIO_COUNT = 3
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MIN_DURATION = Decimal("2.000000")
MAX_DURATION = Decimal("15.000000")


class ReferenceContractError(ValueError):
    """A deterministic pre-freeze v2.2 validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_reference_payload(raw: Any) -> dict[str, Any]:
    """Validate candidate v2.2 metadata without fetching or persisting media URLs."""
    if not isinstance(raw, dict):
        raise ReferenceContractError("payload_invalid", "请求体必须是JSON对象。")
    videos = _array(raw.get("reference_videos"), "reference_video_count_invalid", MAX_VIDEO_COUNT)
    audios = _array(raw.get("reference_audios"), "reference_audio_count_invalid", MAX_AUDIO_COUNT)
    if not videos and not audios:
        raise ReferenceContractError("reference_input_combination_unsupported", "至少需要一个参考视频或参考音频。")
    normalized_videos = [_video(item) for item in videos]
    normalized_audios = [_audio(item) for item in audios]
    if sum(Decimal(item["duration_seconds"]) for item in normalized_videos) > MAX_DURATION:
        raise ReferenceContractError("reference_video_duration_invalid", "参考视频总时长超过15秒。")
    if sum(Decimal(item["duration_seconds"]) for item in normalized_audios) > MAX_DURATION:
        raise ReferenceContractError("reference_audio_duration_invalid", "参考音频总时长超过15秒。")
    return {"reference_videos": normalized_videos, "reference_audios": normalized_audios}


def stable_reference_identity(raw: Any) -> dict[str, Any]:
    """Return ordered fingerprint metadata; signed URLs are deliberately excluded."""
    normalized = validate_reference_payload(raw)
    return {
        kind: [{key: value for key, value in item.items() if key != "url"} for item in items]
        for kind, items in normalized.items()
    }


def reference_digest(raw: Any) -> str:
    """Hash the URL-free ordered reference identity."""
    canonical = json.dumps(stable_reference_identity(raw), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _array(value: Any, code: str, maximum: int) -> list[Any]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ReferenceContractError(code, "参考素材数量无效。")
    return value


def _video(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("role") != "reference_video":
        raise ReferenceContractError("reference_video_format_invalid", "参考视频角色无效。")
    item = _common(value, "video", MAX_VIDEO_BYTES)
    if value.get("mime_type") != "video/mp4":
        raise ReferenceContractError("reference_video_format_invalid", "参考视频只接受能力目录允许的MP4。")
    item["mime_type"] = "video/mp4"
    item["width_pixels"] = _positive_int(value.get("width_pixels"), "reference_video_dimension_invalid")
    item["height_pixels"] = _positive_int(value.get("height_pixels"), "reference_video_dimension_invalid")
    return item


def _audio(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("role") != "reference_audio":
        raise ReferenceContractError("reference_audio_format_invalid", "参考音频角色无效。")
    item = _common(value, "audio", MAX_AUDIO_BYTES)
    audio_format = (value.get("mime_type"), value.get("codec"))
    if audio_format not in {("audio/mpeg", "mp3"), ("audio/wav", "wav")}:
        raise ReferenceContractError("reference_audio_format_invalid", "参考音频格式不在当前官方能力白名单中。")
    item["mime_type"] = str(value["mime_type"])
    item["codec"] = str(value["codec"])
    item["sample_rate_hz"] = _positive_int(value.get("sample_rate_hz"), "reference_audio_properties_invalid")
    item["channels"] = _positive_int(value.get("channels"), "reference_audio_properties_invalid")
    return item


def _common(value: dict[str, Any], kind: str, maximum_bytes: int) -> dict[str, Any]:
    prefix = f"reference_{kind}"
    url = str(value.get("url") or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材必须使用安全的公网HTTPS地址。")
    try:
        port = parsed.port
    except ValueError as error:
        raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材HTTPS端口无效。") from error
    if port not in (None, 443):
        raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材HTTPS地址只允许443端口。")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified):
        raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材地址不能指向私有网络。")
    sha256 = str(value.get("sha256") or "")
    if not SHA256.fullmatch(sha256):
        raise ReferenceContractError(f"{prefix}_identity_mismatch", "参考素材SHA-256无效。")
    size_bytes = _positive_int(value.get("size_bytes"), f"{prefix}_size_invalid")
    if size_bytes > maximum_bytes:
        raise ReferenceContractError(f"{prefix}_size_invalid", "参考素材字节数超过平台安全上限。")
    duration = str(value.get("duration_seconds") or "")
    if not SIX_DECIMALS.fullmatch(duration):
        raise ReferenceContractError(f"{prefix}_duration_invalid", "参考素材时长必须是六位小数字符串。")
    try:
        duration_decimal = Decimal(duration)
    except InvalidOperation as error:
        raise ReferenceContractError(f"{prefix}_duration_invalid", "参考素材时长无效。") from error
    if duration_decimal < MIN_DURATION or duration_decimal > MAX_DURATION:
        raise ReferenceContractError(f"{prefix}_duration_invalid", "参考素材单段时长必须在2至15秒之间。")
    return {
        "role": value["role"],
        "url": url,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "duration_seconds": duration,
    }


def _positive_int(value: Any, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReferenceContractError(code, "参考素材探测属性必须是正整数。")
    return value
