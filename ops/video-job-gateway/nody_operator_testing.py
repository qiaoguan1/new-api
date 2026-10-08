"""Explicit, unverified Nody operator admission and estimated wallet holds.

No quote claims an upstream test passed. Public pricing snapshots inform only
the user-approved estimate formula; actual authenticated receipts remain the
only settlement authority. This module does not fetch prices, media, or tasks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
from pathlib import Path
import re
from typing import cast

from catalog import Catalog
from nody_media_wire import build_candidate_body, candidate_constraints


SCHEMA_VERSION = "xtai-nody-operator-testing-v1"
SOURCE = "nodyhub_operator_testing_estimate"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXACT = re.compile(r"(?:0|[1-9][0-9]{0,2})\.[0-9]{6}")


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001"), rounding=ROUND_CEILING), "f")


def _provenance_digest(value: Mapping[str, object]) -> str:
    encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validated_policy(raw: object) -> dict[str, object]:
    """Validate a protected operator setting, not authenticated task evidence."""
    fields = {"schema_version", "revision", "enabled", "source_snapshot", "currency_factor_exact", "group_factor_exact", "markup_exact", "max_reserve_cny_exact", "models"}
    if not isinstance(raw, dict) or set(raw) - fields or raw.get("schema_version") != SCHEMA_VERSION or type(raw.get("enabled")) is not bool:
        raise ValueError("operator testing policy schema/enabled is invalid")
    revision = raw.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,119}", revision):
        raise ValueError("operator testing policy revision is invalid")
    if not raw["enabled"]:
        return {"schema_version": SCHEMA_VERSION, "revision": revision, "enabled": False, "models": []}
    if raw.get("currency_factor_exact") != "1.500000" or raw.get("markup_exact") != "1.500000" or raw.get("max_reserve_cny_exact") != "150.000000":
        raise ValueError("operator testing currency/markup/hold cap differs from approved policy")
    group = raw.get("group_factor_exact")
    if not isinstance(group, str) or not _EXACT.fullmatch(group) or not 0 < Decimal(group) <= 100:
        raise ValueError("operator testing group factor is invalid")
    snapshot = raw.get("source_snapshot")
    if (not isinstance(snapshot, dict) or set(snapshot) != {"endpoint", "observed_at", "sha256", "source_unit", "billing_unit"}
            or snapshot.get("endpoint") != "https://nodyhub.com/api/pricing" or snapshot.get("source_unit") != "display_credit"
            or snapshot.get("billing_unit") != "per_second" or not isinstance(snapshot.get("sha256"), str) or not _SHA256.fullmatch(snapshot["sha256"])):
        raise ValueError("operator testing pricing snapshot is invalid")
    observed = snapshot.get("observed_at")
    if not isinstance(observed, str) or len(observed) > 64:
        raise ValueError("operator testing pricing observation time is invalid")
    try:
        timestamp = datetime.fromisoformat(observed)
    except ValueError as error:
        raise ValueError("operator testing pricing observation time is invalid") from error
    if timestamp.tzinfo is None:
        raise ValueError("operator testing pricing observation time requires a timezone")
    rows = raw.get("models")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 7:
        raise ValueError("operator testing model price rules are invalid")
    seen = set()
    models = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"model", "max_source_rate_exact", "source_row_sha256"}:
            raise ValueError("operator testing model rate metadata is invalid")
        model = row.get("model")
        candidate_constraints(model)
        if model in seen:
            raise ValueError("duplicate operator testing model")
        rate, digest = row.get("max_source_rate_exact"), row.get("source_row_sha256")
        if (not isinstance(rate, str) or not _EXACT.fullmatch(rate) or not 0 < Decimal(rate) <= 100
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ValueError("operator testing max rate/digest is invalid")
        models.append(dict(row))
        seen.add(model)
    return {**raw, "source_snapshot": dict(snapshot), "models": models}


class NodyOperatorTesting:
    """A disabled optional policy never changes proven text/image admission."""

    def __init__(self, policy: dict[str, object] | None = None) -> None:
        self._policy = _validated_policy(policy) if policy is not None else {"enabled": False, "revision": "", "models": []}
        self.enabled = self._policy["enabled"] is True
        self.revision = cast(str, self._policy["revision"])

    @classmethod
    def load(cls, path: Path | None) -> NodyOperatorTesting:
        """Read protected local policy only; do not fetch or infer current prices."""
        if path is None:
            return cls()
        try:
            text = path.read_text(encoding="utf-8")
            if len(text) > 256 * 1024:
                raise ValueError("operator testing policy file is too large")
            raw = json.loads(text)
        except (OSError, ValueError) as error:
            raise ValueError("operator testing policy cannot be read") from error
        return cls(raw)

    def quote(self, model: str, resolution: str, duration: int, mode: str, image_count: int, video_count: int,
              audio_count: int, generate_audio: bool, input_video_seconds_exact: str | None = None, *,
              aspect_ratio: str = "16:9", input_audio_seconds_exact: str | None = None) -> dict[str, object]:
        """Freeze a complete candidate tuple with a capped, explicitly non-final hold."""
        if not self.enabled:
            raise ValueError("operator testing admission is disabled")
        if (not isinstance(model, str) or any(type(value) is not int for value in (duration, image_count, video_count, audio_count))
                or type(generate_audio) is not bool or not isinstance(mode, str)
                or any(value < 0 or value > limit for value, limit in zip((image_count, video_count, audio_count), (10, 5, 5)))):
            raise ValueError("operator candidate tuple is invalid")
        rows = cast(list[dict[str, object]], self._policy["models"])
        rate_row = next((row for row in rows if row["model"] == model), None)
        if rate_row is None:
            raise ValueError("operator candidate model has no authorized estimate source")
        role = "first_frame" if mode == "first_frame" else "last_frame" if mode == "last_frame" else "reference"
        images = [{"url": f"https://media.example/image{index}.png", "role": "first_frame" if mode == "first_last_frame" and index == 0 else "last_frame" if mode == "first_last_frame" else role} for index in range(image_count)]
        candidate = {"model": model, "mode": mode, "prompt": "operator shape validation", "duration": duration, "resolution": resolution,
                     "aspect_ratio": aspect_ratio, "generate_audio": generate_audio, "images": images,
                     "videos": [{"url": f"https://media.example/video{index}.mp4", "role": "reference"} for index in range(video_count)],
                     "audios": [{"url": f"https://media.example/audio{index}.mp3", "role": "reference"} for index in range(audio_count)]}
        build_candidate_body(model, candidate)
        for count, seconds in ((video_count, input_video_seconds_exact), (audio_count, input_audio_seconds_exact)):
            if count:
                if not isinstance(seconds, str) or not _EXACT.fullmatch(seconds) or not count <= Decimal(seconds) <= count * 15:
                    raise ValueError("operator input seconds must match bounded material counts")
            elif seconds is not None:
                raise ValueError("operator input seconds provided without matching materials")
        evidence: dict[str, object] = {"schema_version": SCHEMA_VERSION, "policy_revision": self.revision,
                                      "source_snapshot": dict(cast(dict[str, object], self._policy["source_snapshot"])),
                                      "currency_factor_exact": self._policy["currency_factor_exact"], "group_factor_exact": self._policy["group_factor_exact"],
                                      "markup_exact": self._policy["markup_exact"], "max_reserve_cny_exact": self._policy["max_reserve_cny_exact"], "model_rate": dict(rate_row)}
        evidence["sha256"] = _provenance_digest(evidence)
        estimate = Decimal(cast(str, rate_row["max_source_rate_exact"])) * Decimal(cast(str, evidence["currency_factor_exact"])) * Decimal(cast(str, evidence["group_factor_exact"])) * duration
        raw_hold = estimate * Decimal("1.5")
        amount = min(raw_hold, Decimal("150"))
        result: dict[str, object] = {"contract_version": "xtai-video-pricing-v1", "model": model, "resolution": resolution, "duration": duration,
                                    "output_seconds": duration, "operation_mode": mode, "image_count": image_count, "video_count": video_count,
                                    "audio_count": audio_count, "generate_audio": generate_audio, "aspect_ratio": aspect_ratio,
                                    "currency": "CNY", "billing_unit": "output_second", "amount_cny_exact": _money(amount), "estimated_cost_cny_exact": _money(estimate),
                                    "cny_per_second_exact": _money(amount / duration), "fallback_multiplier_exact": "1.5", "pricing_revision": self.revision,
                                    "price_source": SOURCE, "pricing_kind": "estimated_reservation", "verification_status": "unverified", "admission_mode": "operator_testing",
                                    "is_upper_bound": False, "reserve_cap_applied": raw_hold > 150, "input_rate_class": "with_video_input" if video_count else "without_video_input",
                                    "fallback": False, "policy_digest": evidence["sha256"], "operator_policy_evidence": evidence}
        for field, value in (("input_video_seconds_exact", input_video_seconds_exact), ("input_audio_seconds_exact", input_audio_seconds_exact)):
            if value is not None:
                result[field] = value
        return result

    def public_rules(self) -> list[dict[str, object]]:
        """Expose candidate ranges and retail estimates, never private source costs."""
        if not self.enabled:
            return []
        result = []
        for row in cast(list[dict[str, object]], self._policy["models"]):
            rule = candidate_constraints(cast(str, row["model"]))
            rate = Decimal(cast(str, row["max_source_rate_exact"])) * Decimal(cast(str, self._policy["currency_factor_exact"])) * Decimal(cast(str, self._policy["group_factor_exact"])) * Decimal("1.5")
            rule.update(currency="CNY", pricing_revision=self.revision, price_source=SOURCE, pricing_kind="estimated_reservation",
                        verification_status="unverified", admission_mode="operator_testing", is_upper_bound=False,
                        estimated_cny_per_output_second_exact=_money(rate), max_reserve_cny_exact="150.000000")
            result.append(rule)
        return result

    def catalog(self, base: Catalog) -> Catalog:
        """Overlay bounded candidate routes only; callers must label them unverified."""
        rules = {rule["model"]: rule for rule in self.public_rules()}
        models = []
        for item in base.models:
            rule = rules.get(item.id)
            if rule is None:
                models.append(item)
                continue
            original = next((route for route in item.routes if route.provider == "nodyhub"), None)
            if original is None:
                raise ValueError("operator candidate has no Nody route")
            resolutions = tuple(dict.fromkeys((*item.resolutions, *cast(list[str], rule["resolutions"]))))
            routes = list(item.routes)
            for resolution in resolutions:
                if not any(route.provider == "nodyhub" and route.resolution == resolution for route in routes):
                    routes.append(replace(original, resolution=resolution))
            routes = [replace(route, aspect_ratios=tuple(dict.fromkeys((*route.aspect_ratios, *cast(list[str], rule["aspect_ratios"])))),
                              max_total_assets=max(route.max_total_assets, cast(int, rule["max_total_assets"])),
                              supports_reference_video=route.supports_reference_video or cast(int, rule["max_videos"]) > 0,
                              supports_reference_audio=route.supports_reference_audio or cast(int, rule["max_audios"]) > 0,
                              max_reference_audios=max(route.max_reference_audios, cast(int, rule["max_audios"]))) if route.provider == "nodyhub" else route for route in routes]
            durations = set(item.durations) | set(cast(list[int], rule["durations"]))
            if item.id == "omni-flash":
                durations.update(range(4, 31))
            models.append(replace(item, operation_modes=tuple(dict.fromkeys((*item.operation_modes, *cast(list[str], rule["operation_modes"])))),
                                  aspect_ratios=tuple(dict.fromkeys((*item.aspect_ratios, *cast(list[str], rule["aspect_ratios"])))),
                                  resolutions=resolutions, durations=tuple(sorted(durations)), duration_min=min(durations), duration_max=max(durations),
                                  max_images=max(item.max_images, cast(int, rule["max_images"])), max_videos=max(item.max_videos, cast(int, rule["max_videos"])), routes=tuple(routes)))
        if set(rules) - {item.id for item in base.models}:
            raise ValueError("operator candidate model is absent from catalog")
        return Catalog(base.protocol_version, base.revision + ("+" + self.revision if rules else ""), tuple(models))


def validate_operator_quote(payload: Mapping[str, object], quote: Mapping[str, object]) -> bool:
    """Validate a frozen estimate without current policy files or fake bill evidence.

    Provenance digests bind trusted settings against accidental tampering; they
    are not signatures and do not claim upstream authentication/acceptance.
    The gateway is responsible for verifying bytes before the initial freeze.
    """
    try:
        if not isinstance(payload, Mapping) or not isinstance(quote, Mapping) or payload.get("_nody_operator_testing") is not True or quote.get("price_source") != SOURCE:
            return False
        evidence = quote.get("operator_policy_evidence")
        if not isinstance(evidence, dict):
            return False
        digest = evidence.get("sha256")
        material = {key: value for key, value in evidence.items() if key != "sha256"}
        if not isinstance(digest, str) or digest != _provenance_digest(material) or quote.get("policy_digest") != digest:
            return False
        raw = {"schema_version": material["schema_version"], "revision": material["policy_revision"], "enabled": True,
               "source_snapshot": material["source_snapshot"], "currency_factor_exact": material["currency_factor_exact"],
               "group_factor_exact": material["group_factor_exact"], "markup_exact": material["markup_exact"],
               "max_reserve_cny_exact": material["max_reserve_cny_exact"], "models": [material["model_rate"]]}
        operator = NodyOperatorTesting(raw)
        model = cast(str, payload.get("model"))
        build_candidate_body(model, payload)
        counts = []
        for field in ("images", "videos", "audios"):
            items = payload.get(field, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("identity"), str) or not _SHA256.fullmatch(item["identity"]) for item in items):
                return False
            counts.append(len(items))
        references = payload.get("reference_input", {})
        if not isinstance(references, dict):
            return False
        for source, target, seconds_field in (("reference_videos", "videos", "input_video_seconds_exact"), ("reference_audios", "audios", "input_audio_seconds_exact")):
            items = cast(list[dict[str, object]], payload.get(target, []))
            metadata = references.get(source, [])
            if not isinstance(metadata, list) or len(metadata) != len(items):
                return False
            if items:
                if any(not isinstance(row, dict) or row.get("sha256") != items[index]["identity"] or not isinstance(row.get("duration_seconds"), str)
                       or not _EXACT.fullmatch(row["duration_seconds"]) or not 1 <= Decimal(row["duration_seconds"]) <= 15 for index, row in enumerate(metadata)):
                    return False
                total = format(sum((Decimal(row["duration_seconds"]) for row in metadata), Decimal(0)), ".6f")
                if payload.get(seconds_field) != total:
                    return False
        expected = operator.quote(model, cast(str, payload.get("resolution")), cast(int, payload.get("duration")), cast(str, payload.get("mode")), *counts,
                                  cast(bool, payload.get("generate_audio")), input_video_seconds_exact=cast(str | None, payload.get("input_video_seconds_exact")),
                                  input_audio_seconds_exact=cast(str | None, payload.get("input_audio_seconds_exact")), aspect_ratio=cast(str, payload.get("aspect_ratio")))
        return dict(quote) == expected
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False
