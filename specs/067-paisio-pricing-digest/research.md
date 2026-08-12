# Research: Paisio Settlement, Isolated Pricing, and Daily Operations Digest

## Production findings

- Paisio generation authentication and the account billing session are healthy.
- `/api/task/self` returns `quota=1` for successful video tasks and `quota=0` for failed tasks. This field is a task count, not monetary quota.
- Paisio writes exact billing entries to `/api/log/self`. Every inspected video's billing rows use `request_id` equal to the task API's `task_id`.
- Successful per-second samples have a positive pre-charge row and a zero `completed` row; failed samples have a matching negative `generation_failed_refund` row.
- `/api/log/self?request_id=<exact task id>` returns only the matching rows and reports the filtered total, enabling bounded completeness checks.
- The current gateway and daily collector divide task `quota` by 500,000. This creates false near-zero task costs and the observed `cost_mismatch`.
- The automatic-pricing planner already has per-model expected-source guards, but `main()` aborts before planning if any credentialed upstream is incomplete. Removing that outer gate exposes the intended isolation behavior.
- The existing balance-alert email endpoint uses a fixed server-side recipient and configured SMTP. A separate structured digest endpoint can reuse the same secure delivery path without copying SMTP credentials into Python.

## Decisions

- Paisio exact cost is the net sum of safe type-2 billing rows for the exact request ID, after a unique terminal successful task lookup.
- Task `quota` is retained only as non-monetary metadata and never enters settlement arithmetic.
- Static approval remains an independent deployment gate; a collector becoming configured does not automatically enable paid traffic.
- Digest payloads are structured and bounded. The Go server renders and escapes HTML; Python never submits arbitrary HTML.
- Delivery state is written only after a successful response, so failed attempts retry without duplicate-success records.

