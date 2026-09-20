# Production verification — 2026-09-20

- Backup: `/opt/ai-api-stack/backups/issue167-20260920-132207`.
- Deployed only `auto-apply-pricing.py`, `daily-ops-digest.py`, `fetch-upstream-recharges.py`; all originals matched SHA-256 preconditions. Same cron locks acquired.
- Tests: 187 local monitor tests, 69 focused tests using current production dependencies, and independent review passed.
- Recharge collector completed: 3 complete / 4 unavailable. Unlike the previous Toonflow exception, successful provider summaries were persisted with a current timestamp.
- Live pricing rerun: 44 decisions, zero writes, business_status=blocked. The process was not aborted. Current shared-model cost gaps remain: Haina for GPT5.6, Hanhe for GPT5.5, Toonflow for Image2. Generic video SKUs remain protected. No claim of successful repricing is made.
- Daily report for Beijing business day 2026-09-19: `delivered`, seven channels. Immediate rerun returned `already_delivered`; no duplicate email. Server acknowledgement confirms accepted delivery, not inbox visibility.
- Public `/api/status` HTTP200; no failed systemd units.
- Generation routes, API credentials, balances and video prices unchanged.
- PR #168 has no reported CI checks; not automatically merged. Issue #167 retains cost-source recovery, historical unresolved job and pre-existing stale live-balance display as tracked work.

Remaining policy decision: ignoring a missing cost source for a shared model while that source remains routable changes the prior highest-retained-source pricing rule and can underprice that source. This patch does not silently make that change.
