#!/usr/bin/env python3
"""Validated official Seedance pricing and deterministic downstream quotes."""

from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Iterable
from urllib.parse import urlsplit


EXPECTED_GROUP_RATIO = Decimal("0.15")
MILLION = Decimal("1000000")
MONEY_QUANTUM = Decimal("0.000001")
EXPECTED_MODELS = {
    "seedance-2.0": {"480p", "720p", "1080p"},
    "seedance-2.0-fast": {"480p", "720p"},
    "seedance-2.0-mini": {"480p", "720p"},
}
EXPECTED_FORMULA = {
    "frame_rate": 24,
    "divisor": 1024,
    "min_output_seconds": 4,
    "max_output_seconds": 15,
    "dimensions": {
        "480p": {"16:9": [854, 480], "9:16": [480, 854]},
        "720p": {"16:9": [1280, 720], "9:16": [720, 1280]},
        "1080p": {"16:9": [1920, 1080], "9:16": [1080, 1920]},
    },
}


class OfficialVideoPricingError(RuntimeError):
    """Raised when an official video quote cannot be proven from the catalog."""


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise OfficialVideoPricingError(f"{label} must be a positive number")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise OfficialVideoPricingError(f"{label} must be a positive number") from exc
    if not number.is_finite() or number <= 0:
        raise OfficialVideoPricingError(f"{label} must be a positive number")
    return number


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _money(value: Decimal) -> float:
    return float(value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))


def _aware_timestamp(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(_text(value))
    except ValueError as exc:
        raise OfficialVideoPricingError(f"{label} must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OfficialVideoPricingError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_official_video_pricing(
    value: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """Validate and return an isolated official-price catalog."""
    if not isinstance(value, dict):
        raise OfficialVideoPricingError("official video pricing must be a JSON object")
    catalog = copy.deepcopy(value)
    if catalog.get("schema_version") != 1:
        raise OfficialVideoPricingError("unsupported official video pricing schema")
    if not _text(catalog.get("revision")):
        raise OfficialVideoPricingError("official video pricing revision is required")
    if catalog.get("currency") != "CNY":
        raise OfficialVideoPricingError("official video pricing currency must be CNY")
    if _decimal(catalog.get("markup"), "markup") != Decimal("1.5"):
        raise OfficialVideoPricingError("official video markup must be exactly 1.5")
    source_url = _text(catalog.get("source_url"))
    parsed = urlsplit(source_url)
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or not (hostname == "volcengine.com" or hostname.endswith(".volcengine.com"))
    ):
        raise OfficialVideoPricingError("official price source must be a public HTTPS URL")
    checked_at = _aware_timestamp(catalog.get("source_checked_at"), "source_checked_at")
    valid_until = _aware_timestamp(catalog.get("valid_until"), "valid_until")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise OfficialVideoPricingError("validation time must include a timezone")
    current = current.astimezone(timezone.utc)
    if checked_at > current + timedelta(minutes=5):
        raise OfficialVideoPricingError("official price source_checked_at is in the future")
    if valid_until <= current or valid_until > checked_at + timedelta(days=45):
        raise OfficialVideoPricingError("official price verification is expired or too long")

    formula = catalog.get("token_formula")
    if not isinstance(formula, dict):
        raise OfficialVideoPricingError("token_formula is required")
    if formula != EXPECTED_FORMULA:
        raise OfficialVideoPricingError("token_formula does not match the approved Ark formula")
    for key in ("frame_rate", "divisor", "min_output_seconds", "max_output_seconds"):
        number = _decimal(formula.get(key), f"token_formula.{key}")
        if number != number.to_integral_value():
            raise OfficialVideoPricingError(f"token_formula.{key} must be an integer")
    minimum = int(formula["min_output_seconds"])
    maximum = int(formula["max_output_seconds"])
    if minimum > maximum:
        raise OfficialVideoPricingError("output duration bounds are reversed")
    dimensions = formula.get("dimensions")
    if not isinstance(dimensions, dict) or not dimensions:
        raise OfficialVideoPricingError("token_formula.dimensions is required")
    for resolution, aspects in dimensions.items():
        if not _text(resolution) or not isinstance(aspects, dict) or not aspects:
            raise OfficialVideoPricingError("every resolution needs aspect dimensions")
        for aspect, pair in aspects.items():
            if not _text(aspect) or not isinstance(pair, list) or len(pair) != 2:
                raise OfficialVideoPricingError("each aspect must contain width and height")
            width = _decimal(pair[0], f"dimensions.{resolution}.{aspect}.width")
            height = _decimal(pair[1], f"dimensions.{resolution}.{aspect}.height")
            if width != width.to_integral_value() or height != height.to_integral_value():
                raise OfficialVideoPricingError("video dimensions must be integers")

    models = catalog.get("models")
    if not isinstance(models, dict) or set(models) != set(EXPECTED_MODELS):
        raise OfficialVideoPricingError("official catalog must define exactly three models")
    for model, row in models.items():
        if not _text(model) or not isinstance(row, dict):
            raise OfficialVideoPricingError("official model row is invalid")
        resolutions = row.get("resolutions")
        if (
            not isinstance(resolutions, list)
            or len(resolutions) != len(set(resolutions))
            or set(resolutions) != EXPECTED_MODELS[model]
        ):
            raise OfficialVideoPricingError(f"{model} resolutions do not match Ark")
        if any(resolution not in dimensions for resolution in resolutions):
            raise OfficialVideoPricingError(f"{model} references unknown resolution")
        price_table = row.get("cny_per_m_tokens_by_resolution")
        if not isinstance(price_table, dict) or set(price_table) != set(resolutions):
            raise OfficialVideoPricingError(
                f"{model} needs one official token-rate row per resolution"
            )
        for resolution, rates in price_table.items():
            if not isinstance(rates, dict):
                raise OfficialVideoPricingError(
                    f"{model}@{resolution} token rates are required"
                )
            _decimal(
                rates.get("no_video_input"),
                f"{model}@{resolution}.no_video_input",
            )
            _decimal(
                rates.get("with_video_input"),
                f"{model}@{resolution}.with_video_input",
            )
    return catalog


def _quote_values(
    catalog: dict[str, Any],
    *,
    model: str,
    resolution: str,
    output_seconds: int,
    aspect_ratio: str,
) -> tuple[int, Decimal, Decimal]:
    models = catalog["models"]
    row = models.get(model)
    if not isinstance(row, dict):
        raise OfficialVideoPricingError(f"model has no official price: {model}")
    if resolution not in row["resolutions"]:
        raise OfficialVideoPricingError(f"resolution has no official price: {model}@{resolution}")
    formula = catalog["token_formula"]
    if (
        not isinstance(output_seconds, int)
        or isinstance(output_seconds, bool)
        or output_seconds < int(formula["min_output_seconds"])
        or output_seconds > int(formula["max_output_seconds"])
    ):
        raise OfficialVideoPricingError("output duration is outside the official catalog")
    dimensions = (formula["dimensions"].get(resolution) or {}).get(aspect_ratio)
    if not isinstance(dimensions, list):
        raise OfficialVideoPricingError("aspect ratio has no official token dimensions")
    width, height = (Decimal(str(item)) for item in dimensions)
    tokens = (
        Decimal(output_seconds)
        * width
        * height
        * Decimal(str(formula["frame_rate"]))
        / Decimal(str(formula["divisor"]))
    )
    estimated_tokens = int(tokens.to_integral_value(rounding=ROUND_CEILING))
    official_rate = Decimal(
        str(row["cny_per_m_tokens_by_resolution"][resolution]["no_video_input"])
    )
    official_cost = Decimal(estimated_tokens) * official_rate / MILLION
    sale = official_cost * Decimal(str(catalog["markup"]))
    return estimated_tokens, official_cost, sale


def quote_video_sale(
    catalog: dict[str, Any],
    *,
    model: str,
    resolution: str,
    output_seconds: int,
    aspect_ratio: str,
    input_video_seconds: int | None = None,
) -> dict[str, Any]:
    """Return an official 1.5x quote or fail before downstream quota is frozen."""
    if input_video_seconds is not None:
        raise OfficialVideoPricingError(
            "video-input pricing requires the official minimum-token lookup table"
        )
    estimated_tokens, official_cost, sale = _quote_values(
        catalog,
        model=model,
        resolution=resolution,
        output_seconds=output_seconds,
        aspect_ratio=aspect_ratio,
    )
    return {
        "model": model,
        "resolution": resolution,
        "aspect_ratio": aspect_ratio,
        "output_seconds": output_seconds,
        "estimated_tokens": estimated_tokens,
        "official_cost_cny": _money(official_cost),
        "sale_cny": _money(sale),
        "sale_cny_per_second": _money(sale / Decimal(output_seconds)),
        "markup": float(Decimal(str(catalog["markup"]))),
        "pricing_revision": catalog["revision"],
        "source_url": catalog["source_url"],
    }


def official_cost_per_second(
    catalog: dict[str, Any], model: str, resolution: str, aspect_ratio: str = "16:9"
) -> Decimal:
    """Return the no-video-input official cost for one output second."""
    formula = catalog["token_formula"]
    dimensions = (formula["dimensions"].get(resolution) or {}).get(aspect_ratio)
    row = catalog["models"].get(model)
    if not isinstance(row, dict) or resolution not in row.get("resolutions", []):
        raise OfficialVideoPricingError(
            f"model resolution has no official price: {model}@{resolution}"
        )
    if not isinstance(dimensions, list):
        raise OfficialVideoPricingError("aspect ratio has no official token dimensions")
    width, height = (Decimal(str(item)) for item in dimensions)
    tokens = (
        width
        * height
        * Decimal(str(formula["frame_rate"]))
        / Decimal(str(formula["divisor"]))
    )
    rate = row["cny_per_m_tokens_by_resolution"][resolution]["no_video_input"]
    return tokens * Decimal(str(rate)) / MILLION


def build_official_model_price_plan(
    catalog: dict[str, Any],
    routes: Iterable[dict[str, Any]],
    current_options: dict[str, Any],
) -> dict[str, Any]:
    """Build per-second NewAPI ModelPrice writes from official prices only."""
    for key in ("ModelRatio", "CompletionRatio", "ModelPrice", "GroupRatio"):
        if not isinstance(current_options.get(key), dict):
            raise OfficialVideoPricingError(f"{key} must be a JSON object")
    group_ratios = current_options["GroupRatio"]
    if not group_ratios or any(
        not math.isclose(float(value), float(EXPECTED_GROUP_RATIO), rel_tol=0, abs_tol=1e-12)
        for value in group_ratios.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ) or any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in group_ratios.values()
    ):
        raise OfficialVideoPricingError("every downstream group ratio must be 0.15")

    options = {
        "ModelRatio": dict(current_options["ModelRatio"]),
        "CompletionRatio": dict(current_options["CompletionRatio"]),
        "ModelPrice": dict(current_options["ModelPrice"]),
    }
    decisions = []
    seen: dict[str, tuple[str, str]] = {}
    for route in sorted(
        (row for row in routes if isinstance(row, dict)),
        key=lambda row: str(row.get("raw_model") or ""),
    ):
        raw_model = _text(route.get("raw_model"))
        stable_model = _text(route.get("stable_model"))
        resolution = _text(route.get("resolution"))
        if not raw_model or not stable_model or not resolution:
            raise OfficialVideoPricingError("official video route is incomplete")
        identity = (stable_model, resolution)
        if raw_model in seen and seen[raw_model] != identity:
            raise OfficialVideoPricingError(f"raw model has conflicting official SKUs: {raw_model}")
        if raw_model in seen:
            continue
        seen[raw_model] = identity
        cost_per_second = official_cost_per_second(catalog, stable_model, resolution)
        sale_per_second = cost_per_second * Decimal(str(catalog["markup"]))
        model_price = sale_per_second / EXPECTED_GROUP_RATIO
        options["ModelPrice"][raw_model] = _money(model_price)
        options["ModelRatio"].pop(raw_model, None)
        options["CompletionRatio"].pop(raw_model, None)
        decisions.append(
            {
                "model": raw_model,
                "stable_model": stable_model,
                "resolution": resolution,
                "action": "apply",
                "billing_unit": "output_second",
                "official_cost_cny_per_second": _money(cost_per_second),
                "sale_cny_per_second": _money(sale_per_second),
                "new_model_price": _money(model_price),
                "pricing_revision": catalog["revision"],
            }
        )
    return {
        "pricing_revision": catalog["revision"],
        "markup": float(Decimal(str(catalog["markup"]))),
        "decisions": decisions,
        "options": options,
    }
