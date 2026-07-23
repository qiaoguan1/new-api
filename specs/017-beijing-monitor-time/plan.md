# Implementation Plan

1. Add a small, dependency-free `monitor_time.py` module that owns the fixed
   `Asia/Shanghai` zone, previous-complete-day resolution, epoch-to-business-day
   conversion, and offset-bearing ISO timestamps.
2. Add boundary-first unit tests and source policy tests before modifying the
   collectors, pricing worker, production-only patch targets, cron contract,
   or model-status page.
3. Update the tracked fetch and pricing workers to use the common module and
   remove UTC day partitioning.
4. Add an idempotent, anchor-validated production patcher for the audit worker,
   monitor generator, and protected monitor UI, whose canonical full sources
   currently live only in the external production stack.
5. Format the customer model-page refresh time explicitly with
   `timeZone: 'Asia/Shanghai'` and a Beijing-time label.
6. Update operational documentation with the explicit `CRON_TZ` contract.
7. Run full Python, frontend, Go/build, comprehensive, security, and production
   dry-run verification.
8. Back up scripts, data, crontab, source files, compose configuration, and the
   running image; deploy, rerun the previous complete Beijing day, and compare
   fetch/audit/monitor dates before any pricing apply.
9. Complete ten final production verification rounds, commit, push, create a
   stacked PR on Issue #15's branch, and move Issue #17 to In Review.
