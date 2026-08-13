"""Resolve one historical Paisio billing identity without direct SQL.

The command is dry-run by default.  Applying a repair requires an exact gateway
job ID plus a matching confirmation phrase, re-runs all evidence checks, writes
the immutable provider-task binding through Store, and settles through the same
idempotent settlement gate used by the background collector.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import Config
from billing_collectors import BillingCollectionError, NewAPITaskBillingCollector
from store import Store, StoreConflict, build_settlement_evidence


JOB_ID_PATTERN = re.compile(r"vjob_[0-9a-f]{32}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Repair one Paisio execution/billing task binding")
    parser.add_argument("--job-id", required=True, help="exact vjob_* gateway identity")
    parser.add_argument(
        "--historical",
        action="store_true",
        help="allow the bounded created_at fallback for a task created before submit-window columns",
    )
    parser.add_argument("--apply", action="store_true", help="persist binding and settlement")
    parser.add_argument(
        "--confirm",
        default="",
        help="required with --apply: BIND <exact-job-id>",
    )
    return parser


def _safe_summary(
    *,
    job_id: str,
    action: str,
    record: Any,
    binding_reused: bool | None = None,
    settlement_reused: bool | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "ok": True,
        "action": action,
        "job_id": job_id,
        "billing_task_id_sha256": hashlib.sha256(
            str(record.provider_task_id).encode("utf-8")
        ).hexdigest(),
        "actual_cost_cny_exact": record.actual_cost_cny_exact,
        "actual_cost_status": record.actual_cost_status,
        "evidence_source": record.evidence_source,
        "observed_at": record.observed_at,
    }
    if record.resolver_version:
        summary.update(
            {
                "resolver_version": record.resolver_version,
                "provider_submit_time": record.provider_submit_time,
                "provider_finish_time": record.provider_finish_time,
                "media_size_bytes": record.media_size_bytes,
                "media_sha256": record.media_sha256,
            }
        )
    if binding_reused is not None:
        summary["binding_reused"] = binding_reused
    if settlement_reused is not None:
        summary["settlement_reused"] = settlement_reused
    return summary


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    job_id = str(args.job_id or "").strip()
    if not JOB_ID_PATTERN.fullmatch(job_id):
        print(json.dumps({"ok": False, "error": "job_id_invalid"}), file=sys.stderr)
        return 2
    if args.apply and args.confirm != f"BIND {job_id}":
        print(json.dumps({"ok": False, "error": "confirmation_mismatch"}), file=sys.stderr)
        return 2

    try:
        config = Config.from_env()
        if "paisio" not in config.newapi_billing_enabled_providers:
            raise BillingCollectionError(
                "provider_billing_collector_not_configured", retry_after_seconds=3600
            )
        store = Store(config.data_dir, max_active_jobs=config.max_active_jobs)
        job = store.get(job_id=job_id, internal=True)
        if not job:
            raise StoreConflict("video repair job does not exist")
        if str(job.get("provider_id") or "").strip().lower() != "paisio":
            raise StoreConflict("video repair job is not a Paisio task")
        if str(job.get("status") or "") != "succeeded":
            raise StoreConflict("video repair job is not succeeded")
        if str(job.get("billing_status") or "") != "settlement_pending":
            raise StoreConflict("video repair job is not awaiting settlement")

        provider = config.providers["paisio"]
        collector = NewAPITaskBillingCollector(
            "paisio",
            f"{provider.base_url}/api/task/self",
            credential_file=config.newapi_billing_credential_files["paisio"],
            rate_cny_per_usd=config.newapi_billing_rates_cny_per_usd.get("paisio", "1"),
            timeout_seconds=provider.poll_timeout_seconds,
            result_hosts=provider.result_hosts,
            max_media_bytes=config.max_stream_bytes,
            identity_resolution_enabled=True,
        )
        binding = store.get_provider_task_binding(job_id)
        if binding:
            record = collector.collect(str(binding.get("billing_task_id") or ""))
        else:
            record = collector.resolve_and_collect(job, allow_historical=bool(args.historical))
        if not args.apply:
            print(
                json.dumps(
                    _safe_summary(job_id=job_id, action="dry_run", record=record),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            return 0

        binding_reused = True
        if not binding and record.execution_task_id != record.provider_task_id:
            binding, binding_reused = store.bind_provider_task(
                job_id=job_id,
                provider_id="paisio",
                execution_task_id=record.execution_task_id,
                billing_task_id=record.provider_task_id,
                resolver_version=record.resolver_version,
                provider_record_id=record.provider_record_id,
                provider_submit_time=record.provider_submit_time,
                provider_finish_time=record.provider_finish_time,
                media_size_bytes=record.media_size_bytes,
                media_sha256=record.media_sha256,
            )
        current = store.get(job_id=job_id, internal=True)
        if not current or str(current.get("billing_status") or "") != "settlement_pending":
            raise StoreConflict("video repair job changed before settlement")
        evidence = build_settlement_evidence(
            job_id=job_id,
            revision=int(current.get("settlement_revision") or 0) + 1,
            provider_task_id=record.provider_task_id,
            actual_cost_status=record.actual_cost_status,
            actual_cost_cny_exact=record.actual_cost_cny_exact,
            evidence_source=record.evidence_source,
            evidence_id=record.evidence_id,
            observed_at=record.observed_at,
            contract_version=str(current.get("billing_contract_version") or ""),
        )
        _snapshot, settlement_reused = store.apply_settlement(evidence)
        print(
            json.dumps(
                _safe_summary(
                    job_id=job_id,
                    action="applied",
                    record=record,
                    binding_reused=binding_reused,
                    settlement_reused=settlement_reused,
                ),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        return 0
    except BillingCollectionError as error:
        print(json.dumps({"ok": False, "error": error.code}), file=sys.stderr)
        return 3
    except StoreConflict as error:
        print(
            json.dumps({"ok": False, "error": "repair_conflict", "message": str(error)[:200]}),
            file=sys.stderr,
        )
        return 4
    except Exception as error:  # pragma: no cover - final secret-safe guardrail
        print(
            json.dumps({"ok": False, "error": "repair_internal_error", "type": type(error).__name__}),
            file=sys.stderr,
        )
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
