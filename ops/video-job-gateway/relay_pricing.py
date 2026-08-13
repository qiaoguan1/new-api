"""Provider-neutral video pricing owned by the XingTu relay."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Iterable


PRICE_CONTRACT_VERSION = "xtai-video-pricing-v1"
MONEY_LIMIT = Decimal("100000")
MONEY_QUANTUM = Decimal("0.000001")
APPROVED_REVISION = "official-fallback-2026-08-09.1"
APPROVED_OFFICIAL_RATES = {
    ("seedance-2.0", "480p"): Decimal("0.441945"),
    ("seedance-2.0", "720p"): Decimal("0.9936"),
    ("seedance-2.0", "1080p"): Decimal("2.4786"),
    ("seedance-2.0-fast", "480p"): Decimal("0.3554775"),
    ("seedance-2.0-fast", "720p"): Decimal("0.7992"),
    ("seedance-2.0-mini", "480p"): Decimal("0.2209725"),
    ("seedance-2.0-mini", "720p"): Decimal("0.4968"),
}
ARK_VIDEO_INPUT_RATE_RATIOS = {
    "seedance-2.0": (Decimal("28"), Decimal("46")),
    "seedance-2.0-fast": (Decimal("22"), Decimal("37")),
    "seedance-2.0-mini": (Decimal("14"), Decimal("23")),
}


class RelayPricingError(ValueError):
    pass


def _money_exact(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


class RelayPricing:
    def __init__(
        self,
        fallback_file: Path,
        *,
        pricing_url: str = "",
        group_name: str = "视频",
        timeout_seconds: int = 5,
        cache_seconds: int = 30,
    ) -> None:
        self.fallback_file = Path(fallback_file).resolve()
        self.pricing_url = self._validated_url(pricing_url)
        self.group_name = str(group_name or "视频").strip()[:40] or "视频"
        self.timeout_seconds = max(1, min(int(timeout_seconds), 20))
        self.cache_seconds = max(5, min(int(cache_seconds), 300))
        self.fallback = self._load_fallback()
        self.lock = threading.Lock()
        self.dynamic_expires_at = 0.0
        self.dynamic_revision = ""
        self.dynamic_rates: dict[str, Decimal] = {}

    @staticmethod
    def _validated_url(value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            return ""
        parsed = urllib.parse.urlsplit(normalized)
        hostname = (parsed.hostname or "").lower()
        internal_http = parsed.scheme == "http" and hostname in {"new-api", "localhost", "127.0.0.1"}
        if (
            (parsed.scheme != "https" and not internal_http)
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise RelayPricingError("relay pricing URL must be HTTPS or the private new-api service")
        return normalized

    def _load_fallback(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.fallback_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RelayPricingError("relay fallback pricing file is unavailable") from error
        if not isinstance(raw, dict) or raw.get("contract_version") != PRICE_CONTRACT_VERSION:
            raise RelayPricingError("relay fallback pricing contract is invalid")
        if (
            str(raw.get("currency") or "").upper() != "CNY"
            or str(raw.get("revision") or "").strip() != APPROVED_REVISION
        ):
            raise RelayPricingError("relay fallback pricing metadata is invalid")
        try:
            multiplier = Decimal(str(raw.get("fallback_multiplier") or ""))
        except InvalidOperation as error:
            raise RelayPricingError("relay fallback pricing multiplier is invalid") from error
        if multiplier != Decimal("1.5"):
            raise RelayPricingError("relay fallback pricing multiplier must be 1.5")
        models = raw.get("models")
        if not isinstance(models, dict) or not models:
            raise RelayPricingError("relay fallback pricing models are missing")
        loaded_rates: dict[tuple[str, str], Decimal] = {}
        for model, resolutions in models.items():
            if not str(model or "").strip() or not isinstance(resolutions, dict) or not resolutions:
                raise RelayPricingError("relay fallback pricing model is invalid")
            for resolution, row in resolutions.items():
                if not str(resolution or "").strip() or not isinstance(row, dict):
                    raise RelayPricingError("relay fallback pricing resolution is invalid")
                official = self._positive_decimal(row.get("official_cost_cny_per_second"), "official fallback rate")
                loaded_rates[(str(model), str(resolution))] = official
                aliases = row.get("relay_price_aliases")
                if not isinstance(aliases, list) or not any(str(item or "").strip() for item in aliases):
                    raise RelayPricingError("relay price aliases are missing")
                row["_official"] = official
        if loaded_rates != APPROVED_OFFICIAL_RATES:
            raise RelayPricingError("relay fallback pricing table is not the approved Ark revision")
        return raw

    @staticmethod
    def _positive_decimal(value: Any, label: str) -> Decimal:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as error:
            raise RelayPricingError(f"{label} is invalid") from error
        if not number.is_finite() or number <= 0 or number > MONEY_LIMIT:
            raise RelayPricingError(f"{label} is invalid")
        return number

    def _fetch_dynamic(self) -> tuple[str, dict[str, Decimal]]:
        if not self.pricing_url:
            return "", {}
        request = urllib.request.Request(
            self.pricing_url,
            headers={"Accept": "application/json", "User-Agent": "XingTuVideoJobGateway/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read() or b"{}")
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return "", {}
        if not isinstance(payload, dict) or payload.get("success") is False:
            return "", {}
        group_ratios = payload.get("group_ratio") if isinstance(payload.get("group_ratio"), dict) else {}
        try:
            group_ratio = self._positive_decimal(group_ratios.get(self.group_name), "relay group ratio")
        except RelayPricingError:
            return "", {}
        rates: dict[str, Decimal] = {}
        for raw in payload.get("data") or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("model_name") or raw.get("model") or "").strip()
            try:
                quota_type = int(raw.get("quota_type"))
            except (TypeError, ValueError):
                continue
            if not name or quota_type != 1:
                continue
            try:
                model_price = self._positive_decimal(raw.get("model_price"), "relay model price")
            except RelayPricingError:
                continue
            rates[name] = model_price * group_ratio
        return str(payload.get("pricing_version") or "").strip()[:160], rates

    def _dynamic_snapshot(self) -> tuple[str, dict[str, Decimal]]:
        current = time.monotonic()
        with self.lock:
            if self.dynamic_expires_at > current:
                return self.dynamic_revision, dict(self.dynamic_rates)
            revision, rates = self._fetch_dynamic()
            self.dynamic_revision = revision
            self.dynamic_rates = dict(rates)
            self.dynamic_expires_at = current + self.cache_seconds
            return revision, dict(rates)

    def rows(self, pairs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        """Legacy v1 customer pricing; retained unchanged for existing clients."""
        dynamic_revision, dynamic_rates = self._dynamic_snapshot()
        result: list[dict[str, Any]] = []
        for model, resolution in sorted(set(pairs)):
            row = ((self.fallback.get("models") or {}).get(model) or {}).get(resolution)
            if not isinstance(row, dict):
                continue
            official = Decimal(str(row["_official"]))
            alias_rates = {
                dynamic_rates[str(alias).strip()]
                for alias in row.get("relay_price_aliases") or []
                if str(alias or "").strip() in dynamic_rates
            }
            use_dynamic = len(alias_rates) == 1
            rate = next(iter(alias_rates)) if use_dynamic else official * Decimal("1.5")
            result.append({
                "model": model,
                "resolution": resolution,
                "currency": "CNY",
                "billing_unit": "output_second",
                "cny_per_second_exact": format(rate, "f"),
                "official_cost_cny_per_second_exact": _money_exact(official),
                "fallback_multiplier_exact": "1.5",
                "pricing_revision": dynamic_revision if use_dynamic and dynamic_revision else str(self.fallback["revision"]),
                "price_source": "xingtu_relay_price" if use_dynamic else "official_1_5_fallback",
                "fallback": not use_dynamic,
            })
        return result

    def official_rows(self, pairs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        """Billing-v2 reservation pricing; never consumes marketplace pricing."""
        result: list[dict[str, Any]] = []
        for model, resolution in sorted(set(pairs)):
            row = ((self.fallback.get("models") or {}).get(model) or {}).get(resolution)
            if not isinstance(row, dict):
                continue
            official = Decimal(str(row["_official"]))
            result.append({
                "model": model,
                "resolution": resolution,
                "currency": "CNY",
                "billing_unit": "output_second",
                "cny_per_second_exact": _money_exact(official * Decimal("1.5")),
                "official_cost_cny_per_second_exact": _money_exact(official),
                "fallback_multiplier_exact": "1.5",
                "pricing_revision": str(self.fallback["revision"]),
                "price_source": "ark_official_1_5",
                "fallback": False,
            })
        return result

    def snapshot(self, pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
        rows = self.rows(pairs)
        revisions = sorted({str(row.get("pricing_revision") or "") for row in rows})
        return {
            "contract_version": PRICE_CONTRACT_VERSION,
            "currency": "CNY",
            "revision": "+".join(revisions),
            "models": rows,
        }

    def official_snapshot(self, pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
        rows = self.official_rows(pairs)
        revisions = sorted({str(row.get("pricing_revision") or "") for row in rows})
        return {
            "contract_version": PRICE_CONTRACT_VERSION,
            "currency": "CNY",
            "revision": "+".join(revisions),
            "models": rows,
        }

    def quote(self, model: str, resolution: str, duration: int) -> dict[str, Any]:
        rows = self.rows([(str(model or ""), str(resolution or "").lower())])
        if len(rows) != 1:
            raise RelayPricingError("relay price is unavailable for this model and resolution")
        row = rows[0]
        seconds = max(1, min(int(duration), 3600))
        amount = Decimal(str(row["cny_per_second_exact"])) * Decimal(seconds)
        if not amount.is_finite() or amount <= 0 or amount > MONEY_LIMIT:
            raise RelayPricingError("relay quote amount is invalid")
        return {
            **row,
            "contract_version": PRICE_CONTRACT_VERSION,
            "output_seconds": seconds,
            "amount_cny_exact": format(amount, "f"),
        }

    def official_quote(
        self,
        model: str,
        resolution: str,
        duration: int,
        *,
        input_rate_class: str = "without_video_input",
    ) -> dict[str, Any]:
        rows = self.official_rows([(str(model or ""), str(resolution or "").lower())])
        if len(rows) != 1:
            raise RelayPricingError("relay official price is unavailable for this model and resolution")
        row = dict(rows[0])
        if input_rate_class == "with_video_input":
            ratio = ARK_VIDEO_INPUT_RATE_RATIOS.get(str(model or ""))
            if ratio is None:
                raise RelayPricingError("relay official video-input price is unavailable")
            official_rate = Decimal(str(row["official_cost_cny_per_second_exact"])) * ratio[0] / ratio[1]
            row["official_cost_cny_per_second_exact"] = _money_exact(official_rate)
            row["cny_per_second_exact"] = _money_exact(official_rate * Decimal("1.5"))
        elif input_rate_class != "without_video_input":
            raise RelayPricingError("relay official input rate class is invalid")
        seconds = max(1, min(int(duration), 3600))
        official_total = Decimal(str(row["official_cost_cny_per_second_exact"])) * Decimal(seconds)
        amount = official_total * Decimal("1.5")
        if not amount.is_finite() or amount <= 0 or amount > MONEY_LIMIT:
            raise RelayPricingError("relay official quote amount is invalid")
        return {
            **row,
            "contract_version": PRICE_CONTRACT_VERSION,
            "output_seconds": seconds,
            "official_cost_cny_exact": _money_exact(official_total),
            "amount_cny_exact": _money_exact(amount),
            "input_rate_class": input_rate_class,
        }
