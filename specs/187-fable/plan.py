"""Derive a blocked-by-default single-model plan from actual tariff evidence."""

import argparse
import base64
from decimal import Decimal, ROUND_HALF_UP
import json
import os
from pathlib import Path
import re


MODEL = "claude-fable-5-1"
SOURCE_EXPRESSIONS = {
    "rolldek-ccmax": 'tier("base", p * 10 + c * 50 + cr * 0.25 + cc * 12.5 + cc1h * 20)',
    "maolao": '(tier("标准", p * 10 + c * 50 + cr * 0.25 + cc * 12.5 + cc1h * 20)) * (param("output_config.effort") == "max" ? 3 : 1)',
}
COEFFICIENTS = {"input": Decimal("10"), "output": Decimal("50"),
                "cache_read": Decimal("0.25"), "cache_write_5m": Decimal("12.5"),
                "cache_write_1h": Decimal("20")}
GROUPS = {"rolldek-ccmax": Decimal("1.2"), "maolao": Decimal("1.3")}


def positive_decimal(value: object) -> Decimal:
    """Reject missing, non-finite and non-positive tariff inputs."""
    try:
        result = Decimal(str(value))
    except Exception as error:
        raise ValueError("invalid tariff number") from error
    if not result.is_finite() or result <= 0:
        raise ValueError("nonpositive or nonfinite tariff")
    return result


def numeric_text(value: Decimal) -> str:
    """Render an exact coefficient without a floating-point round trip."""
    return format(value.normalize(), "f")


def build_plan(evidence: list[dict]) -> dict:
    """Require exact successful probes, matching charges and known expressions."""
    if len(evidence) != 2 or {r["source"] for r in evidence} != set(SOURCE_EXPRESSIONS):
        raise ValueError("expected two distinct verified retained candidates")
    costs: dict[str, dict[str, dict[str, Decimal]]] = {}
    for item in evidence:
        source = item["source"]
        group = positive_decimal(item["group_ratio"])
        rate = positive_decimal(item["cny_per_credit"])
        probe, bill = item["probe"], item["bill"]
        if group != GROUPS[source] or not probe.get("success") or probe.get("response_model") != MODEL:
            raise ValueError("probe identity or group mismatch")
        if bill.get("model_name") != MODEL or bill.get("type") != 2:
            raise ValueError("missing exact-model actual consume bill")
        expected_name = item.get("expected_token_name")
        if (not isinstance(expected_name, str) or not expected_name.startswith("xtai-fable187-probe-" + source + "-") or
                bill.get("token_name") != expected_name or not bill.get("request_id")):
            raise ValueError("bill is not linked to the dedicated probe token and request")
        other = bill.get("other") or {}
        if isinstance(other, str):
            other = json.loads(other)
        if other.get("billing_mode") != "tiered_expr" or positive_decimal(other.get("group_ratio")) != group:
            raise ValueError("actual billing contract or group mismatch")
        try:
            expression = base64.b64decode(other["expr_b64"], validate=True).decode()
        except Exception as error:
            raise ValueError("missing actual billing expression") from error
        if re.sub(r"\s+", "", expression) != re.sub(r"\s+", "", SOURCE_EXPRESSIONS[source]):
            raise ValueError("unreviewed upstream billing expression")
        usage = probe["usage"]
        prompt, completion = bill["prompt_tokens"], bill["completion_tokens"]
        if prompt != usage["prompt_tokens"] or completion != usage["completion_tokens"]:
            raise ValueError("probe usage does not match bill")
        if other.get("request_multiplier", 1) != 1:
            raise ValueError("base probe unexpectedly used a multiplier")
        expected = ((Decimal(prompt) * 10 + Decimal(completion) * 50) * group / 2).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP)
        if Decimal(bill["quota"]) != expected:
            raise ValueError("actual upstream charge does not match tariff")
        base = {name: coefficient * group * rate for name, coefficient in COEFFICIENTS.items()}
        multiplier = Decimal(3) if source == "maolao" else Decimal(1)
        costs[source] = {"standard": base, "max": {k: v * multiplier for k, v in base.items()}}
    retail = {
        tier: {dimension: max(c[tier][dimension] for c in costs.values()) * Decimal("1.5")
               for dimension in COEFFICIENTS}
        for tier in ("standard", "max")
    }
    group_ratio = Decimal("0.15")
    variables = {"input": "p", "output": "c", "cache_read": "cr",
                 "cache_write_5m": "cc", "cache_write_1h": "cc1h"}
    terms = {
        tier: " + ".join(variable + " * " + numeric_text(retail[tier][dimension] / group_ratio)
                         for dimension, variable in variables.items())
        for tier in retail
    }
    expression = ('(param("output_config.effort") == "max" || param("reasoning_effort") == "max") ? tier("max", ' + terms["max"] +
                  ') : tier("standard", ' + terms["standard"] + ')')
    standard = retail["standard"]
    return {
        "model": MODEL, "group": "文", "group_ratio": "0.15",
        "compatibility_verified": False,
        "deployment_blocker": "Original-request effort and final upstream request must be reconciled before activation.",
        "evidence": [{"source": item["source"], "token_name": item["expected_token_name"],
                      "bill_request_id": item["bill"]["request_id"],
                      "response_request_id": item["probe"].get("request_id")} for item in evidence],
        "billing_mode": "tiered_expr", "billing_expr": expression,
        "options": {"ModelRatio": float(standard["input"] / Decimal("0.3")),
                    "CompletionRatio": float(standard["output"] / standard["input"]),
                    "CacheRatio": float(standard["cache_read"] / standard["input"]),
                    "CreateCacheRatio": float(standard["cache_write_5m"] / standard["input"])},
        "providers": [{"source": "rolldek-ccmax", "priority": 10}, {"source": "maolao", "priority": 8}],
        "cost_cny_per_m_per_source": {
            source: {tier: {k: numeric_text(v) for k, v in values.items()} for tier, values in tiers.items()}
            for source, tiers in costs.items()
        },
        "retail_cny_per_m": {tier: {k: numeric_text(v) for k, v in values.items()} for tier, values in retail.items()},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    candidate = build_plan(json.loads(args.evidence.read_text()))
    with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as handle:
        json.dump(candidate, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"model": MODEL, "compatibility_verified": False,
                      "retail_cny_per_m": candidate["retail_cny_per_m"]}, ensure_ascii=False))
