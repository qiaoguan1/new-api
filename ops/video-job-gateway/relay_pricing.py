"""Provider-neutral video pricing owned by the XingTu relay."""

from __future__ import annotations

import json
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


class RelayPricingError(ValueError):
    pass


def _money_exact(value: Decimal) -> str:
    return format(value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING), "f")


class RelayPricing:
    def __init__(
        self,
        fallback_file: Path,
    ) -> None:
        self.fallback_file = Path(fallback_file).resolve()
        self.fallback = self._load_fallback()

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

    def rows(self, pairs: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for model, resolution in sorted(set(pairs)):
            row = ((self.fallback.get("models") or {}).get(model) or {}).get(resolution)
            if not isinstance(row, dict):
                continue
            official = Decimal(str(row["_official"]))
            rate = official * Decimal("1.5")
            result.append({
                "model": model,
                "resolution": resolution,
                "currency": "CNY",
                "billing_unit": "output_second",
                "cny_per_second_exact": _money_exact(rate),
                "official_cost_cny_per_second_exact": _money_exact(official),
                "fallback_multiplier_exact": "1.5",
                "pricing_revision": str(self.fallback["revision"]),
                "price_source": "ark_official_1_5",
                "fallback": False,
            })
        return result

    def snapshot(self, pairs: Iterable[tuple[str, str]]) -> dict[str, Any]:
        rows = []
        for private_row in self.rows(pairs):
            row = dict(private_row)
            row.pop("official_cost_cny_per_second_exact", None)
            row.pop("fallback_multiplier_exact", None)
            rows.append(row)
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
        source = ((self.fallback.get("models") or {}).get(str(model or "")) or {}).get(
            str(resolution or "").lower()
        )
        if not isinstance(source, dict):
            raise RelayPricingError("relay official source price is unavailable")
        official_total = Decimal(str(source["_official"])) * Decimal(seconds)
        amount = official_total * Decimal("1.5")
        if not amount.is_finite() or amount <= 0 or amount > MONEY_LIMIT:
            raise RelayPricingError("relay quote amount is invalid")
        return {
            **row,
            "contract_version": PRICE_CONTRACT_VERSION,
            "output_seconds": seconds,
            "official_cost_cny_exact": _money_exact(official_total),
            "amount_cny_exact": _money_exact(amount),
        }
