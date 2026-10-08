"""Exact operator-evidenced Nody media prices, without tariff extrapolation."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import re
from typing import cast

from catalog import Catalog
from nody_media_wire import build_media_body
from nodyhub import NODY_SOURCE, UUID, money


_EXACT_FIELDS = ("model", "resolution", "duration", "mode", "image_count", "video_count", "audio_count", "generate_audio", "aspect_ratio")
_SIX_DECIMALS = re.compile(r"(?:0|[1-9][0-9]{0,2})\.[0-9]{6}")


def _media_profile_identity(row: dict[str, object]) -> tuple[object, ...]:
    """Include measured input duration in the exact accepted billing tuple."""
    return tuple(row.get(field) for field in (*_EXACT_FIELDS, "input_video_seconds_exact", "input_audio_seconds_exact"))


def _validated_media_profile(row: object) -> dict[str, object]:
    """Validate authentic successful billing metadata and its candidate wire spec."""
    if not isinstance(row, dict):
        raise ValueError("Nody media profile must be an object")
    row = {**row, "aspect_ratio": row.get("aspect_ratio", "16:9")}
    counts = (row.get("image_count"), row.get("video_count"), row.get("audio_count"))
    if any(type(count) is not int or count < 0 or count > limit for count, limit in zip(counts, (10, 5, 5))):
        raise ValueError("Nody media profile counts are invalid")
    if (row.get("status") != "succeeded" or row.get("cost_status") != "actual"
            or row.get("evidence_source") != "nodyhub_authenticated_video_task"
            or not isinstance(row.get("evidence_task_id"), str) or not UUID.fullmatch(row["evidence_task_id"])):
        raise ValueError("Nody media profile needs a successful authenticated actual bill")
    value = row.get("actual_cost_cny_exact")
    if not isinstance(value, str) or not _SIX_DECIMALS.fullmatch(value) or not 0 < Decimal(value) <= 100:
        raise ValueError("Nody actual cost must be a positive six-decimal string not exceeding CNY100")
    if counts[1] and "input_video_seconds_exact" not in row:
        raise ValueError("Nody video profiles require exact measured input seconds")
    if counts[2] and "input_audio_seconds_exact" not in row:
        raise ValueError("Nody audio profiles require exact measured input seconds")
    for field, count in (("input_video_seconds_exact", counts[1]), ("input_audio_seconds_exact", counts[2])):
        if field not in row:
            continue
        value = row[field]
        if (not count or not isinstance(value, str) or not _SIX_DECIMALS.fullmatch(value)
                or not count <= Decimal(value) <= count * 15):
            raise ValueError("Nody input media seconds must be an exact positive bounded string")
    images = []
    mode = row.get("mode")
    if not isinstance(mode, str):
        raise ValueError("Nody media profile mode must be a canonical string")
    for index in range(counts[0]):
        role = "reference"
        if mode in {"first_frame", "last_frame"}:
            role = str(mode)
        elif mode == "first_last_frame":
            role = "first_frame" if index == 0 else "last_frame"
        images.append({"url": f"https://media.example/image{index}.png", "role": role})
    build_media_body(row.get("model"), {
        **{field: row.get(field) for field in _EXACT_FIELDS}, "prompt": "evidence validation",
        "images": images,
        "videos": [{"url": f"https://media.example/video{index}.mp4", "role": "reference"} for index in range(counts[1])],
        "audios": [{"url": f"https://media.example/audio{index}.mp3", "role": "reference"} for index in range(counts[2])],
    })
    result = {field: row[field] for field in (*_EXACT_FIELDS, "actual_cost_cny_exact", "status", "cost_status", "evidence_source", "evidence_task_id")}
    for field in ("input_video_seconds_exact", "input_audio_seconds_exact"):
        if field in row:
            result[field] = row[field]
    return result


class NodyMediaContracts:
    """An empty evidence file keeps all additional media specs unavailable."""

    def __init__(self, revision: str, profiles: tuple[dict[str, object], ...]) -> None:
        self.revision = revision
        self.profiles = profiles

    @classmethod
    def load(cls, path: Path | None) -> NodyMediaContracts:
        """Read at most 80 successful exact profiles; never reuse an evidence task."""
        if path is None:
            return cls("", ())
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Nody media evidence configuration cannot be read") from error
        if not isinstance(raw, dict) or raw.get("schema_version") != "xtai-nody-media-input-v1":
            raise ValueError("Nody media evidence schema is invalid")
        revision, rows = raw.get("revision"), raw.get("profiles")
        if (not isinstance(revision, str) or not revision.strip() or len(revision) > 120
                or not isinstance(rows, list) or len(rows) > 80):
            raise ValueError("Nody media evidence revision/profiles are invalid")
        profiles = []
        seen_tuples: set[tuple[object, ...]] = set()
        seen_tasks: set[str] = set()
        for row in rows:
            profile = _validated_media_profile(row)
            identity = _media_profile_identity(profile)
            task_id = cast(str, profile["evidence_task_id"])
            if identity in seen_tuples or task_id in seen_tasks:
                raise ValueError("duplicate Nody media evidence tuple/task")
            seen_tuples.add(identity)
            seen_tasks.add(task_id)
            profiles.append(profile)
        return cls(revision, tuple(profiles))

    def quote(self, model: str, resolution: str, duration: int, mode: str, image_count: int,
              video_count: int, audio_count: int, generate_audio: bool,
              input_video_seconds_exact: str | None = None, *, aspect_ratio: str = "16:9",
              input_audio_seconds_exact: str | None = None) -> dict[str, object]:
        """Quote only the evidenced exact spec and measured input-video duration."""
        if (any(type(value) is not int for value in (duration, image_count, video_count, audio_count))
                or type(generate_audio) is not bool
                or any(not isinstance(value, str) for value in (model, resolution, mode, aspect_ratio))):
            raise ValueError("invalid Nody media quote tuple types")
        if video_count and input_video_seconds_exact is None:
            raise ValueError("Nody media quote requires measured video seconds")
        if audio_count and input_audio_seconds_exact is None:
            raise ValueError("Nody media quote requires measured audio seconds")
        for seconds, count in ((input_video_seconds_exact, video_count), (input_audio_seconds_exact, audio_count)):
            if seconds is not None and (not count or not isinstance(seconds, str) or not _SIX_DECIMALS.fullmatch(seconds)
                                        or not count <= Decimal(seconds) <= count * 15):
                raise ValueError("invalid Nody media quote input duration")
        query = {
            "model": model, "resolution": resolution, "duration": duration, "mode": mode,
            "image_count": image_count, "video_count": video_count, "audio_count": audio_count,
            "generate_audio": generate_audio, "aspect_ratio": aspect_ratio,
            "input_video_seconds_exact": input_video_seconds_exact, "input_audio_seconds_exact": input_audio_seconds_exact,
        }
        identity = _media_profile_identity(query)
        for profile in self.profiles:
            if _media_profile_identity(profile) != identity:
                continue
            cost = Decimal(cast(str, profile["actual_cost_cny_exact"]))
            amount = cost * Decimal("1.5")
            result: dict[str, object] = {
                "model": model, "resolution": resolution, "operation_mode": mode,
                "image_count": image_count, "video_count": video_count, "audio_count": audio_count,
                "generate_audio": generate_audio, "aspect_ratio": aspect_ratio,
                "duration": duration, "currency": "CNY", "billing_unit": "output_second",
                "cny_per_second_exact": money(amount / duration), "amount_cny_exact": money(amount), "output_seconds": duration,
                "reference_cost_cny_exact": money(cost), "fallback_multiplier_exact": "1.5", "pricing_revision": self.revision,
                "media_contract_evidence": {"task_id": profile["evidence_task_id"], "source": profile["evidence_source"]},
                "price_source": NODY_SOURCE, "contract_version": "xtai-video-pricing-v1",
                "input_rate_class": "with_video_input" if video_count else "without_video_input", "fallback": False,
            }
            if input_video_seconds_exact is not None:
                result["input_video_seconds_exact"] = input_video_seconds_exact
            if input_audio_seconds_exact is not None:
                result["input_audio_seconds_exact"] = input_audio_seconds_exact
            return result
        raise ValueError("Nody media mode/count/spec/input duration has no verified price")

    def public_rows(self) -> list[dict[str, object]]:
        """Return exact retail tuples, without provider cost or private bill identity."""
        result = []
        for profile in self.profiles:
            row = self.quote(**{field: profile[field] for field in _EXACT_FIELDS},
                             input_video_seconds_exact=profile.get("input_video_seconds_exact"),
                             input_audio_seconds_exact=profile.get("input_audio_seconds_exact"))
            row.pop("reference_cost_cny_exact")
            row.pop("media_contract_evidence")
            result.append(row)
        return result

    def catalog(self, base: Catalog) -> Catalog:
        """Union only evidenced specs and resolution-local Nody media route flags."""
        models = []
        for item in base.models:
            profiles = [profile for profile in self.profiles if profile["model"] == item.id]
            if not profiles:
                models.append(item)
                continue
            original = next((route for route in item.routes if route.provider == "nodyhub"), None)
            if original is None:
                raise ValueError("Nody media evidence has no matching Nody catalog route")
            resolutions = tuple(dict.fromkeys((*item.resolutions, *(cast(str, profile["resolution"]) for profile in profiles))))
            routes = list(item.routes)
            for resolution in dict.fromkeys(cast(str, profile["resolution"]) for profile in profiles):
                matching = [profile for profile in profiles if profile["resolution"] == resolution]
                if not any(route.provider == "nodyhub" and route.resolution == resolution for route in routes):
                    routes.append(replace(original, resolution=resolution, max_total_assets=0,
                                          supports_reference_video=False, supports_reference_audio=False, max_reference_audios=0))
                max_videos = max(cast(int, profile["video_count"]) for profile in matching)
                max_audios = max(cast(int, profile["audio_count"]) for profile in matching)
                max_assets = max(sum(cast(int, profile[field]) for field in ("image_count", "video_count", "audio_count")) for profile in matching)
                routes = [replace(route, max_total_assets=max(route.max_total_assets, max_assets),
                                  supports_reference_video=route.supports_reference_video or bool(max_videos),
                                  supports_reference_audio=route.supports_reference_audio or bool(max_audios),
                                  max_reference_audios=max(route.max_reference_audios, max_audios))
                          if route.provider == "nodyhub" and route.resolution == resolution else route for route in routes]
            modes = tuple(dict.fromkeys((*item.operation_modes, *(cast(str, profile["mode"]) for profile in profiles))))
            aspect_ratios = tuple(dict.fromkeys((*item.aspect_ratios, *(cast(str, profile["aspect_ratio"]) for profile in profiles))))
            durations = tuple(sorted(set((*item.durations, *(cast(int, profile["duration"]) for profile in profiles)))))
            models.append(replace(item, operation_modes=modes, resolutions=resolutions, durations=durations, aspect_ratios=aspect_ratios,
                                  duration_min=min(durations), duration_max=max(durations),
                                  max_images=max(item.max_images, *(cast(int, profile["image_count"]) for profile in profiles)),
                                  max_videos=max(item.max_videos, *(cast(int, profile["video_count"]) for profile in profiles)), routes=tuple(routes)))
        if any(profile["model"] not in {item.id for item in base.models} for profile in self.profiles):
            raise ValueError("Nody media evidence model is absent from the catalog")
        revision = base.revision + ("+" + self.revision if self.profiles else "")
        return Catalog(base.protocol_version, revision, tuple(models))
