"""Pure validation and stable identity helpers for the gated v2.2 contract."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import re
import secrets
import socket
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SHA256 = re.compile(r"[0-9a-f]{64}")
SIX_DECIMALS = re.compile(r"(?:0|[1-9][0-9]*)\.[0-9]{6}")
MAX_VIDEO_COUNT = 3
MAX_AUDIO_COUNT = 3
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_AUDIO_BYTES = 15 * 1024 * 1024
MIN_DURATION = Decimal("2.000000")
MAX_DURATION = Decimal("15.000000")
AUDIO_FORMATS = {
    ("audio/mpeg", "mp3"),
    ("audio/wav", "wav"),
    ("audio/x-wav", "wav"),
    ("audio/aac", "aac"),
    ("audio/mp4", "m4a"),
    ("audio/x-m4a", "m4a"),
}


class ReferenceContractError(ValueError):
    """A deterministic pre-freeze v2.2 validation error."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class ReferenceMediaVerifier:
    """Fetch and probe v2.2 media before any quota is reserved or task is created."""

    def __init__(self, allowed_hosts: tuple[str, ...], *, timeout_seconds: int = 60) -> None:
        self.allowed_hosts = tuple(sorted({item.strip().lower().rstrip(".") for item in allowed_hosts if item.strip()}))
        self.timeout_seconds = timeout_seconds
        self.tls_context = ssl.create_default_context()

    def verify(self, references: dict[str, Any]) -> None:
        if not self.allowed_hosts:
            raise ReferenceContractError("reference_media_host_unavailable", "参考素材来源域名尚未配置。")
        for kind, maximum in (("reference_videos", MAX_VIDEO_BYTES), ("reference_audios", MAX_AUDIO_BYTES)):
            for item in references.get(kind) or []:
                self._verify_item(item, "video" if kind == "reference_videos" else "audio", maximum)

    def verify_image_origins(self, urls: list[str]) -> None:
        """Require companion images to use the same approved public object-storage origin."""
        for url in urls:
            parsed = urllib.parse.urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            try:
                port = parsed.port
            except ValueError as error:
                raise ReferenceContractError("video_image_url_invalid", "参考图片地址端口无效。") from error
            if (
                parsed.scheme != "https"
                or not host
                or parsed.username
                or parsed.password
                or parsed.fragment
                or port not in (None, 443)
                or host not in self.allowed_hosts
            ):
                raise ReferenceContractError("video_image_url_invalid", "参考图片必须来自中转站允许的私有对象存储域名。")
            self._public_dns_addresses(host, "video_image")

    def _verify_item(self, item: dict[str, Any], kind: str, maximum: int) -> None:
        prefix = f"reference_{kind}"
        url = str(item["url"])
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in self.allowed_hosts:
            raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材不在中转站允许的私有对象存储域名中。")
        addresses = self._public_dns_addresses(host, prefix)
        headers = {"Accept": str(item["mime_type"]), "User-Agent": "xtai-reference-verifier/2.2"}
        suffix = ".mp4" if kind == "video" else f".{item.get('codec') or 'audio'}"
        try:
            with self._open_pinned(url, host, addresses, headers, prefix) as response, tempfile.NamedTemporaryFile(
                prefix="xtai-ref-", suffix=suffix, delete=True
            ) as target:
                response_type = str(response.headers.get_content_type() or "").lower()
                allowed_types = {str(item["mime_type"]).lower()}
                if item.get("codec") == "wav":
                    allowed_types.update({"audio/wav", "audio/x-wav"})
                if item.get("codec") == "m4a":
                    allowed_types.update({"audio/mp4", "audio/x-m4a"})
                if response_type not in allowed_types:
                    raise ReferenceContractError(f"{prefix}_format_invalid", "参考素材响应类型与声明不一致。")
                declared_length = response.headers.get("Content-Length")
                if declared_length:
                    try:
                        content_length = int(declared_length)
                    except (TypeError, ValueError) as error:
                        raise ReferenceContractError(f"{prefix}_size_invalid", "参考素材响应字节数无效。") from error
                    if content_length != int(item["size_bytes"]):
                        raise ReferenceContractError(f"{prefix}_size_invalid", "参考素材响应字节数与声明不一致。")
                digest = hashlib.sha256()
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, maximum + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > maximum:
                        raise ReferenceContractError(f"{prefix}_size_invalid", "参考素材超过中转站安全上限。")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                if total != int(item["size_bytes"]):
                    raise ReferenceContractError(f"{prefix}_size_invalid", "参考素材实际字节数与声明不一致。")
                if not secrets.compare_digest(digest.hexdigest(), str(item["sha256"])):
                    raise ReferenceContractError(f"{prefix}_identity_mismatch", "参考素材SHA-256与实际内容不一致。")
                self._probe(Path(target.name), item, kind)
        except ReferenceContractError:
            raise
        except (OSError, ValueError, InvalidOperation, urllib.error.URLError, subprocess.SubprocessError) as error:
            raise ReferenceContractError(f"{prefix}_probe_failed", "中转站无法安全读取或探测参考素材。") from error

    @staticmethod
    def _public_dns_addresses(host: str, prefix: str) -> tuple[str, ...]:
        try:
            answers = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材域名无法解析。") from error
        if not answers:
            raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材域名没有可用地址。")
        addresses: list[str] = []
        for answer in answers:
            value = str(answer[4][0]).split("%", 1)[0]
            address = ipaddress.ip_address(value)
            if not address.is_global:
                raise ReferenceContractError(f"{prefix}_url_invalid", "参考素材域名解析到非公网地址。")
            if value not in addresses:
                addresses.append(value)
        return tuple(addresses)

    @contextmanager
    def _open_pinned(
        self,
        url: str,
        host: str,
        addresses: tuple[str, ...],
        headers: dict[str, str],
        prefix: str,
    ):
        parsed = urllib.parse.urlsplit(url)
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        last_error: OSError | None = None
        for address in addresses:
            connection = _PinnedHTTPSConnection(
                host,
                address,
                timeout=self.timeout_seconds,
                context=self.tls_context,
            )
            try:
                connection.request("GET", target, headers=headers)
                response = connection.getresponse()
                if response.status != 200:
                    response.close()
                    raise ReferenceContractError(
                        f"{prefix}_probe_failed",
                        "参考素材地址未返回可读取的成功响应。",
                    )
                try:
                    yield response
                finally:
                    response.close()
                return
            except ReferenceContractError:
                raise
            except OSError as error:
                last_error = error
            finally:
                connection.close()
        raise OSError("all pinned reference media addresses failed") from last_error

    @staticmethod
    def _probe(path: Path, item: dict[str, Any], kind: str) -> None:
        completed = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=format_name,duration:stream=codec_type,codec_name,width,height,sample_rate,channels",
                "-of", "json", str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        payload = json.loads(completed.stdout)
        streams = [row for row in payload.get("streams") or [] if row.get("codec_type") == kind]
        if not streams:
            raise ReferenceContractError(f"reference_{kind}_format_invalid", "参考素材中未发现声明的媒体轨道。")
        stream = streams[0]
        media_format = str((payload.get("format") or {}).get("format_name") or "").lower()
        actual_duration = Decimal(str((payload.get("format") or {}).get("duration") or "0"))
        declared_duration = Decimal(str(item["duration_seconds"]))
        if abs(actual_duration - declared_duration) > Decimal("0.100000"):
            raise ReferenceContractError(f"reference_{kind}_duration_invalid", "参考素材实际时长与声明不一致。")
        if kind == "video":
            if not any(name in media_format.split(",") for name in ("mov", "mp4", "m4a", "3gp", "3g2", "mj2")):
                raise ReferenceContractError("reference_video_format_invalid", "参考视频容器不是MP4。")
            if stream.get("codec_name") not in {"h264", "hevc"}:
                raise ReferenceContractError("reference_video_format_invalid", "参考视频编码不在当前安全白名单中。")
            if int(stream.get("width") or 0) != int(item["width_pixels"]) or int(stream.get("height") or 0) != int(item["height_pixels"]):
                raise ReferenceContractError("reference_video_dimension_invalid", "参考视频实际尺寸与声明不一致。")
        else:
            declared_codec = str(item.get("codec") or "")
            if declared_codec == "mp3" and "mp3" not in media_format:
                raise ReferenceContractError("reference_audio_format_invalid", "参考音频实际容器与声明不一致。")
            if declared_codec == "wav" and "wav" not in media_format:
                raise ReferenceContractError("reference_audio_format_invalid", "参考音频实际容器与声明不一致。")
            if declared_codec == "aac" and stream.get("codec_name") != "aac":
                raise ReferenceContractError("reference_audio_format_invalid", "参考音频实际编码与声明不一致。")
            if declared_codec == "m4a" and (
                stream.get("codec_name") != "aac"
                or not any(name in media_format.split(",") for name in ("mov", "mp4", "m4a", "3gp", "3g2", "mj2"))
            ):
                raise ReferenceContractError("reference_audio_format_invalid", "参考音频实际容器或编码与声明不一致。")
            if int(stream.get("sample_rate") or 0) != int(item["sample_rate_hz"]) or int(stream.get("channels") or 0) != int(item["channels"]):
                raise ReferenceContractError("reference_audio_properties_invalid", "参考音频实际采样率或声道数与声明不一致。")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Resolve once, connect to that exact address, and retain TLS SNI/hostname verification."""

    def __init__(self, host: str, address: str, **kwargs: Any) -> None:
        super().__init__(host, port=443, **kwargs)
        self.pinned_address = address

    def _create_connection(self, _address, timeout=None, source_address=None):  # noqa: ANN001
        return socket.create_connection(
            (self.pinned_address, 443),
            timeout,
            source_address,
        )


def validate_reference_payload(raw: Any) -> dict[str, Any]:
    """Validate candidate v2.2 metadata without fetching or persisting media URLs."""
    if not isinstance(raw, dict):
        raise ReferenceContractError("payload_invalid", "请求体必须是JSON对象。")
    videos = _array(raw.get("reference_videos"), "reference_video_count_invalid", MAX_VIDEO_COUNT)
    audios = _array(raw.get("reference_audios"), "reference_audio_count_invalid", MAX_AUDIO_COUNT)
    if not videos and not audios:
        raise ReferenceContractError("reference_input_combination_unsupported", "至少需要一个参考视频或参考音频。")
    has_image = bool(raw.get("image")) or bool(raw.get("images"))
    if audios and not videos and not has_image:
        raise ReferenceContractError("reference_audio_only_unsupported", "参考音频必须同时提供至少一张图片或一段参考视频。")
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
    if value in (None, "", []):
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
    if audio_format not in AUDIO_FORMATS:
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
