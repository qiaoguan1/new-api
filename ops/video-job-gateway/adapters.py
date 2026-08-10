"""Provider adapters for the durable XingTu video relay."""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Mapping


SUCCESS_STATUSES = {"completed", "complete", "succeeded", "success", "done", "finished"}
FAILURE_STATUSES = {"failed", "failure", "error", "cancelled", "canceled", "rejected", "expired"}
QUEUED_STATUSES = {"pending", "queued", "created", "submitted", "waiting"}
RUNNING_STATUSES = {"in_progress", "in-progress", "processing", "running", "generating"}


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    id: str
    base_url: str
    api_key: str
    result_hosts: tuple[str, ...]
    submit_timeout_seconds: int = 90
    poll_timeout_seconds: int = 60

    @property
    def configured(self) -> bool:
        parsed = urllib.parse.urlsplit(self.base_url)
        return bool(self.api_key and parsed.scheme in {"http", "https"} and parsed.netloc)


@dataclass(frozen=True, slots=True)
class JsonResponse:
    status: int
    headers: Mapping[str, str]
    payload: dict[str, Any]
    text: str


@dataclass(frozen=True, slots=True)
class Observation:
    status: str
    upstream_task_id: str = ""
    result_url: str = ""
    upstream_status: str = ""
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    requires_auth: bool = False


class TransportFailure(OSError):
    pass


class AdapterError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: str,
        http_status: int = 502,
        retryable: bool = False,
        uncertain: bool = False,
        category: str = "upstream",
    ) -> None:
        super().__init__(message)
        self.code = str(code or "video_adapter_error")[:80]
        self.phase = phase
        self.http_status = int(http_status or 502)
        self.retryable = bool(retryable)
        self.uncertain = bool(uncertain)
        self.category = category

    def contract(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": str(self)[:500],
            "http_status": self.http_status,
            "retryable": self.retryable,
            "uncertain": self.uncertain,
            "phase": self.phase,
        }


class HttpJsonTransport:
    def __init__(self, max_response_bytes: int = 2 * 1024 * 1024) -> None:
        self.max_response_bytes = max(64 * 1024, min(int(max_response_bytes), 10 * 1024 * 1024))

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: dict[str, Any] | None,
        timeout: int,
    ) -> JsonResponse:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if payload is not None else None
        request_headers = dict(headers)
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=request_headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(response.status)
                response_headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            status = int(error.code)
            response_headers = {str(key).lower(): str(value) for key, value in error.headers.items()}
            raw = error.read(self.max_response_bytes + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError, socket.timeout, ssl.SSLError) as error:
            raise TransportFailure(type(error).__name__) from error
        if len(raw) > self.max_response_bytes:
            raise TransportFailure("upstream_response_too_large")
        text = raw.decode("utf-8", "replace") if raw else ""
        parsed: Any = {}
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {}
        return JsonResponse(
            status=status,
            headers=response_headers,
            payload=parsed if isinstance(parsed, dict) else {},
            text=text[:1000],
        )


class VideoAdapter:
    provider_id = ""

    def __init__(self, config: ProviderConfig, transport: HttpJsonTransport | Any | None = None) -> None:
        self.config = config
        self.transport = transport or HttpJsonTransport()

    def submit(self, request_id: str, upstream_model: str, payload: dict[str, Any]) -> Observation:
        body = self.request_body(upstream_model, payload)
        try:
            response = self.transport.request_json(
                "POST",
                f"{_api_root(self.config.base_url)}/v1/videos",
                headers=self._headers(request_id),
                payload=body,
                timeout=self.config.submit_timeout_seconds,
            )
        except TransportFailure as error:
            raise AdapterError(
                f"{self.provider_id}_video_submit_uncertain",
                "上游提交连接中断，任务结果未知；系统不会自动重放本次提交。",
                phase="submit",
                retryable=True,
                uncertain=True,
            ) from error
        self._raise_submit_http(response)
        if not response.payload:
            raise AdapterError(
                f"{self.provider_id}_video_submit_invalid_response",
                "上游提交响应不是有效JSON；系统不会自动重放本次提交。",
                phase="submit",
                uncertain=True,
            )
        observation = _observation(response.payload)
        if observation.status == "failed":
            return observation
        if not observation.upstream_task_id and not observation.result_url:
            raise AdapterError(
                f"{self.provider_id}_video_task_id_missing",
                "上游提交响应缺少任务号；系统不会自动重放本次提交。",
                phase="submit",
                uncertain=True,
            )
        return observation

    @property
    def ready_for_new_jobs(self) -> bool:
        return self.config.configured

    def poll(self, upstream_task_id: str) -> Observation:
        task_id = urllib.parse.quote(str(upstream_task_id or ""), safe="")
        try:
            response = self.transport.request_json(
                "GET",
                f"{_api_root(self.config.base_url)}/v1/videos/{task_id}",
                headers=self._headers(""),
                payload=None,
                timeout=self.config.poll_timeout_seconds,
            )
        except TransportFailure as error:
            raise AdapterError(
                f"{self.provider_id}_video_poll_unavailable",
                "上游任务暂时无法查询，系统将继续查询原任务。",
                phase="poll",
                retryable=True,
                uncertain=True,
            ) from error
        if response.status == HTTPStatus.NOT_FOUND:
            return Observation(status="missing", upstream_task_id=upstream_task_id, upstream_status="not_found")
        if response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN} or response.status >= 500:
            raise AdapterError(
                f"{self.provider_id}_video_poll_unavailable",
                "上游任务暂时无法查询，系统将继续查询原任务。",
                phase="poll",
                http_status=response.status,
                retryable=True,
                uncertain=True,
            )
        if response.status >= 400:
            raise AdapterError(
                f"{self.provider_id}_video_poll_failed",
                _safe_error(response.payload, response.text, "上游任务查询失败。"),
                phase="poll",
                http_status=response.status,
                retryable=response.status in {408, 425, 429},
            )
        if not response.payload:
            raise AdapterError(
                f"{self.provider_id}_video_poll_invalid_response",
                "上游任务状态响应无效，系统将继续查询原任务。",
                phase="poll",
                retryable=True,
                uncertain=True,
            )
        observation = _observation(response.payload, fallback_task_id=upstream_task_id)
        if observation.status == "succeeded" and not observation.result_url:
            raise AdapterError(
                f"{self.provider_id}_video_result_missing",
                "上游报告成功但没有返回安全的视频地址，任务需要继续核对。",
                phase="deliver",
                retryable=True,
                uncertain=True,
            )
        return observation

    def _headers(self, request_id: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Accept": "application/json",
            "User-Agent": "XingTuVideoJobGateway/1",
        }
        if request_id:
            headers["Idempotency-Key"] = request_id
            headers["X-Request-ID"] = request_id
        return headers

    def _raise_submit_http(self, response: JsonResponse) -> None:
        if response.status < 400:
            return
        uncertain = response.status in {408, 425, 500, 502, 503, 504}
        category = "rate_limit" if response.status == 429 else "authentication" if response.status in {401, 403} else "upstream"
        raise AdapterError(
            f"{self.provider_id}_video_submit_{'uncertain' if uncertain else 'failed'}",
            _safe_error(response.payload, response.text, "上游拒绝了视频任务。"),
            phase="submit",
            http_status=response.status,
            retryable=response.status in {408, 425, 429, 500, 502, 503, 504},
            uncertain=uncertain,
            category=category,
        )

    def request_body(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class PaisioAdapter(VideoAdapter):
    provider_id = "paisio"

    def request_body(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = _base_body(upstream_model, payload)
        images = _assets(payload, "images")
        videos = _assets(payload, "videos")
        mode = str(payload.get("mode") or "text")
        if mode == "text":
            return body
        first = next((item["url"] for item in images if item["role"] == "first"), "")
        last = next((item["url"] for item in images if item["role"] == "last"), "")
        ordinary = [item["url"] for item in images if item["role"] not in {"first", "last"}]
        if mode == "first_last_frame":
            if not first or not last:
                raise _validation("paisio_video_first_last_required", "首尾帧模式需要首帧和尾帧两张图片。")
            body["start_image_url"] = first
            body["end_image_url"] = last
        else:
            ordered = ([first] if first else []) + ordinary + ([last] if last else [])
            if ordered:
                body["image_url"] = ordered[0]
                if len(ordered) > 1:
                    body["extra_images"] = ordered[1:9]
        if videos:
            body["extra_videos"] = [item["url"] for item in videos[:3]]
        return body


class ToonflowAdapter(VideoAdapter):
    provider_id = "toonflow"

    def submit(self, request_id: str, upstream_model: str, payload: dict[str, Any]) -> Observation:
        try:
            response = self.transport.request_json(
                "POST",
                f"{self.config.base_url.rstrip('/')}/video/generateVideo",
                headers=self._headers(request_id),
                payload=self.request_body(upstream_model, payload),
                timeout=self.config.submit_timeout_seconds,
            )
        except TransportFailure as error:
            raise AdapterError(
                "toonflow_video_submit_uncertain",
                "Toonflow提交连接中断，任务结果未知；系统不会自动重放。",
                phase="submit",
                retryable=True,
                uncertain=True,
            ) from error
        self._raise_submit_http(response)
        if not response.payload:
            raise AdapterError(
                "toonflow_video_submit_invalid_response",
                "Toonflow提交响应不是有效JSON。",
                phase="submit",
                uncertain=True,
            )
        if int(response.payload.get("code") or 0) not in {0, 200}:
            raise AdapterError(
                "toonflow_video_submit_failed",
                _safe_error(response.payload, response.text, "Toonflow拒绝了视频任务。"),
                phase="submit",
            )
        task_id = str(response.payload.get("data") or "").strip()
        if not task_id:
            raise AdapterError(
                "toonflow_video_task_id_missing",
                "Toonflow提交响应缺少任务号。",
                phase="submit",
                uncertain=True,
            )
        return Observation(status="running", upstream_task_id=task_id, upstream_status="submitted")

    def poll(self, upstream_task_id: str) -> Observation:
        try:
            response = self.transport.request_json(
                "POST",
                f"{self.config.base_url.rstrip('/')}/video/getVideoStatus",
                headers=self._headers(""),
                payload={"taskICode": upstream_task_id},
                timeout=self.config.poll_timeout_seconds,
            )
        except TransportFailure as error:
            raise AdapterError(
                "toonflow_video_poll_unavailable",
                "Toonflow任务暂时无法查询，系统将继续查询原任务。",
                phase="poll",
                retryable=True,
                uncertain=True,
            ) from error
        if response.status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN} or response.status >= 500:
            raise AdapterError(
                "toonflow_video_poll_unavailable",
                "Toonflow任务暂时无法查询，系统将继续查询原任务。",
                phase="poll",
                http_status=response.status,
                retryable=True,
                uncertain=True,
            )
        if response.status >= 400 or not response.payload:
            raise AdapterError(
                "toonflow_video_poll_failed",
                _safe_error(response.payload, response.text, "Toonflow任务查询失败。"),
                phase="poll",
                http_status=response.status,
            )
        return _toonflow_observation(response.payload, upstream_task_id)

    def request_body(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        images = _assets(payload, "images")
        videos = _assets(payload, "videos")
        references: list[dict[str, Any]] = []
        role_map = {
            "first": "first_frame",
            "last": "last_frame",
            "reference": "reference_image",
            "style": "reference_image",
        }
        for item in images:
            references.append({
                "type": "image_url",
                "image_url": {"url": item["url"]},
                "role": role_map.get(item["role"], "reference_image"),
            })
        for item in videos:
            references.append({
                "type": "video_url",
                "video_url": {"url": item["url"]},
                "role": "reference_video",
            })
        route = payload.get("_route") if isinstance(payload.get("_route"), dict) else {}
        body: dict[str, Any] = {
            "model": upstream_model,
            "prompt": str(payload.get("prompt") or "").strip(),
            "resolution": str(route.get("resolution") or "720p").lower(),
            "duration": int(payload.get("duration") or 0),
            "metadata": {
                "ratio": str(payload.get("aspect_ratio") or "16:9"),
                # Preserve the relay's existing default for callers that omit the switch.
                # XingTu Cloud explicitly sends false and strips any unexpected audio on delivery.
                "generate_audio": payload.get("generate_audio") is not False,
                "watermark": False,
                "seed": -1,
            },
        }
        if references:
            body["metadata"]["references"] = references
        return body

class RollDekAdapter(VideoAdapter):
    provider_id = "rolldek"

    def request_body(self, upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = _base_body(upstream_model, payload)
        images = _assets(payload, "images")
        videos = _assets(payload, "videos")
        negative_prompt = str(payload.get("negative_prompt") or "").strip()
        generate_audio = payload.get("generate_audio")
        if upstream_model.startswith("seedance-"):
            _rolldek_seedance_assets(body, images, videos)
            # RollDek defaults Seedance audio generation to true.  The XingTu
            # protocol defaults it to false, so always make that choice explicit.
            body["generate_audio"] = bool(generate_audio)
        elif upstream_model.startswith("kling-"):
            _rolldek_kling_assets(body, images, videos)
        elif upstream_model in {"sora-2", "sora-2-pro"}:
            if len(images) > 1 or videos:
                raise _validation("rolldek_sora_reference_invalid", "Sora最多支持一张参考图且不支持参考视频。")
            if images:
                body["image_url"] = images[0]["url"]
            if generate_audio is not None:
                body["generate_audio"] = bool(generate_audio)
            if negative_prompt:
                body["negative_prompt"] = negative_prompt[:2000]
        elif upstream_model.startswith("veo-3.1-") or upstream_model == "gemini-omni-flash":
            _rolldek_veo_assets(body, upstream_model, images, videos)
            if generate_audio is not None:
                body["generate_audio"] = bool(generate_audio)
            if negative_prompt:
                body["negative_prompt"] = negative_prompt[:2000]
        else:
            raise _validation("rolldek_video_model_unsupported", "RollDek适配器尚未批准该上游模型。")
        return body


def _base_body(upstream_model: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "model": upstream_model,
        "prompt": str(payload.get("prompt") or "").strip(),
        "duration": int(payload.get("duration") or 0),
        "aspect_ratio": str(payload.get("aspect_ratio") or "16:9").strip(),
    }
    route = payload.get("_route") if isinstance(payload.get("_route"), dict) else {}
    if route.get("send_resolution") and str(route.get("resolution") or "").strip():
        body["resolution"] = str(route["resolution"]).strip().lower()
    return body


def _assets(payload: dict[str, Any], name: str) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in payload.get(name) if isinstance(payload.get(name), list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            result.append({"url": url, "role": str(item.get("role") or "reference").strip().lower()})
    return result


def _rolldek_seedance_assets(body: dict[str, Any], images: list[dict[str, str]], videos: list[dict[str, str]]) -> None:
    first = next((item["url"] for item in images if item["role"] == "first"), "")
    last = next((item["url"] for item in images if item["role"] == "last"), "")
    styles = [item["url"] for item in images if item["role"] == "style"]
    references = [item["url"] for item in images if item["role"] not in {"first", "last", "style"}]
    if first:
        body["start_image_url"] = first
    if last:
        body["end_image_url"] = last
    if references:
        body["image_urls"] = references[:9]
    if styles:
        body["style_image_urls"] = styles[:9]
    ordinary_videos = [item["url"] for item in videos if item["role"] != "camera_motion"]
    if ordinary_videos:
        body["video_reference"] = ordinary_videos[0] if len(ordinary_videos) == 1 else ordinary_videos[:3]


def _rolldek_kling_assets(body: dict[str, Any], images: list[dict[str, str]], videos: list[dict[str, str]]) -> None:
    if len(images) > 2 or len(videos) > 2:
        raise _validation("rolldek_kling_reference_limit", "Kling最多支持两张参考图和两种参考视频。")
    if images:
        values = [item["url"] for item in images]
        body["image_url" if len(values) == 1 else "image_urls"] = values[0] if len(values) == 1 else values
    action = next((item["url"] for item in videos if item["role"] != "camera_motion"), "")
    camera = next((item["url"] for item in videos if item["role"] == "camera_motion"), "")
    if action:
        body["reference_video"] = action
    if camera:
        body["camera_motion_reference_video"] = camera


def _rolldek_veo_assets(
    body: dict[str, Any],
    upstream_model: str,
    images: list[dict[str, str]],
    videos: list[dict[str, str]],
) -> None:
    if upstream_model == "veo-3.1-generate-preview-ref":
        if not 1 <= len(images) <= 3 or videos:
            raise _validation("rolldek_veo_reference_invalid", "Veo多参考模型需要1到3张参考图且不接受参考视频。")
        body["image_urls"] = [item["url"] for item in images]
        return
    if upstream_model == "gemini-omni-flash":
        if len(videos) > 1:
            raise _validation("rolldek_omni_video_limit", "Omni模型最多支持一个参考视频。")
        if images:
            body["image_urls"] = [item["url"] for item in images[:3]]
        if videos:
            body["video_url"] = videos[0]["url"]
        return
    if len(images) > 2 or videos:
        raise _validation("rolldek_veo_reference_invalid", "该Veo模型最多支持两张首尾帧且不接受参考视频。")
    if len(images) == 1:
        body["image_url"] = images[0]["url"]
    elif len(images) == 2:
        body["image_urls"] = [item["url"] for item in images]


def _toonflow_observation(raw: dict[str, Any], fallback_task_id: str) -> Observation:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    task_id = str(data.get("id") or fallback_task_id).strip()
    upstream_status = str(data.get("status") or raw.get("status") or "running").strip()
    normalized = upstream_status.lower().replace(" ", "_")
    result_url = ""
    value = data.get("data")
    if isinstance(value, str):
        parsed = urllib.parse.urlsplit(value.strip())
        if parsed.scheme == "https" and parsed.netloc:
            result_url = value.strip()
    if not result_url:
        result_url = _result_url(raw)
    if normalized in SUCCESS_STATUSES:
        if not result_url:
            raise AdapterError(
                "toonflow_video_result_missing",
                "Toonflow报告成功但没有返回安全的视频地址。",
                phase="deliver",
                retryable=True,
                uncertain=True,
            )
        status = "succeeded"
    elif normalized in FAILURE_STATUSES:
        status = "failed"
    else:
        status = "running"
    return Observation(
        status=status,
        upstream_task_id=task_id,
        result_url=result_url,
        upstream_status=upstream_status[:80],
        error_code="toonflow_video_failed" if status == "failed" else "",
        error_message=_safe_error(raw, "", "Toonflow视频任务失败。") if status == "failed" else "",
    )


def _observation(raw: dict[str, Any], fallback_task_id: str = "") -> Observation:
    upstream_task_id = _first_text(raw, ("task_id", "id", "request_id")) or fallback_task_id
    upstream_status = _first_text(raw, ("status", "state"))
    result_url = _result_url(raw)
    normalized = upstream_status.lower().replace(" ", "_")
    if normalized in SUCCESS_STATUSES or (result_url and normalized not in FAILURE_STATUSES):
        status = "succeeded"
    elif normalized in FAILURE_STATUSES:
        status = "failed"
    elif normalized in QUEUED_STATUSES:
        status = "queued"
    elif normalized in RUNNING_STATUSES or upstream_task_id:
        status = "running"
    else:
        status = "running"
    error_message = _safe_error(raw, "", "上游视频任务失败。") if status == "failed" else ""
    error_code = _first_text(raw, ("error_code", "code")) if status == "failed" else ""
    return Observation(
        status=status,
        upstream_task_id=upstream_task_id,
        result_url=result_url,
        upstream_status=upstream_status[:80],
        error_code=(error_code or "upstream_video_failed")[:80] if status == "failed" else "",
        error_message=error_message[:500],
        retryable=False,
        requires_auth=False,
    )


def _result_url(raw: dict[str, Any]) -> str:
    candidates: list[Any] = [raw.get("video_url"), raw.get("url"), raw.get("output_url")]
    for key in ("result", "data", "output"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            candidates.extend([nested.get("video_url"), nested.get("url"), nested.get("output_url")])
        elif isinstance(nested, list):
            for item in nested[:5]:
                if isinstance(item, dict):
                    candidates.extend([item.get("video_url"), item.get("url"), item.get("output_url")])
                else:
                    candidates.append(item)
    for value in candidates:
        text = str(value or "").strip()
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme == "https" and parsed.netloc:
            return text
    return ""


def _first_text(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    for source in (raw, raw.get("data"), raw.get("result")):
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def _safe_error(raw: dict[str, Any], text: str, fallback: str) -> str:
    values: list[Any] = [raw.get("message"), raw.get("error_message"), raw.get("fail_reason")]
    error = raw.get("error")
    if isinstance(error, dict):
        values.extend([error.get("message"), error.get("detail"), error.get("code")])
    elif error:
        values.append(error)
    for value in values:
        candidate = " ".join(str(value or "").replace("<", " ").replace(">", " ").split())
        if candidate:
            return candidate[:500]
    if text and not text.lstrip().startswith("<"):
        return " ".join(text.split())[:500]
    return fallback


def _api_root(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    return value[:-3] if value.endswith("/v1") else value


def _validation(code: str, message: str) -> AdapterError:
    return AdapterError(code, message, phase="validate", http_status=400, category="validation")
