# Channel Monitor health policy

Issue #28 separates actionable incidents from stale or retired observations.

- `monitor_health_policy.py` is copied next to the production
  `generate-monitor-data.py` worker.
- `patch_monitor_generator.py` makes a fail-closed, idempotent source patch.
- `patch_monitor_frontend.py` labels inactive and stale states and shows
  monitoring coverage/configuration warnings in the internal operator UI.
- The policy never mutates channel state, pricing, credentials, or user quota.
- NewAPI `CHANNEL_TEST_FREQUENCY` remains unset: its all-channel routine can
  execute billable media requests and automatically disable channels.

Production deployment must first create a root-only rollback directory with
the original generator, generated data, and hashes. Run the patcher once,
compile both Python files, regenerate data, and independently validate the
active-only totals before keeping the change.

## Upstream video catalog normalization

Issue #34 governs mutable upstream Seedance 2.0 names before they reach the
fixed `xtai-relay-v1` catalog. It does not change the desktop application.

Runtime files:

- `config/video-model-policy.json`: versioned stable catalog, reviewed aliases,
  and the explicit publish allowlist. Operators can update this file without a
  NewAPI rebuild, but must increment `revision` and keep a review reason.
- `video_catalog_policy.py`: validates rules, performs fail-closed matching,
  and calculates the strict publish intersection.
- `upstream_video_catalog.py`: loads enabled NewAPI routes in memory, fetches
  `/v1/models` over HTTPS without redirects, sanitizes failures, and preserves
  the last complete per-channel snapshot.
- `scripts/sync-upstream-video-catalog.py`: dry-run/apply entry point.

Validate a rule change without reading the database or contacting upstreams:

```bash
python3 channel-monitor/scripts/sync-upstream-video-catalog.py --validate-only
```

Review current upstream names without writing runtime data:

```bash
python3 channel-monitor/scripts/sync-upstream-video-catalog.py --print-report
```

Atomically refresh the catalog snapshot, mapping report, and candidate
manifests (this still does not edit channels or prices):

```bash
flock -n /run/lock/upstream-video-catalog.lock \
  python3 channel-monitor/scripts/sync-upstream-video-catalog.py --apply-snapshot
```

The fixed catalog is `seedance-2.0`, `seedance-2.0-fast`, and
`seedance-2.0-mini`; resolution is stored separately. A new upstream name is
handled as follows:

1. Exact reviewed source rule.
2. Exact reviewed global rule.
3. Reviewed bounded regex rule.
4. Conservative unique family/variant/resolution parser.
5. Otherwise it remains in `video-model-mapping-report.json` for review.

AI-produced suggestions must be added with `review_state: pending`. They cannot
publish until an operator verifies the upstream meaning, changes the state to
`approved`, records a reason, increments the rule version/policy revision, and
runs validation. `packapi` and `unity2` are intentionally excluded.

Candidate output is gated by approved mapping, current enabled model config,
daily health audit, complete positive actual-cost evidence, and the publish
allowlist. The public candidate file contains only stable model IDs,
resolutions, availability, and protocol/catalog revisions. Upstream names,
channel IDs, costs, credentials, and review notes remain internal.
