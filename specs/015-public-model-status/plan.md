# Implementation Plan

1. Confirm the current authenticated route, sidebar visibility, model performance
   API authorization, and internal monitor boundary.
2. Add source-level privacy tests before changing the page.
3. Replace the integrated channel monitor view with a model-only status view
   backed exclusively by the sanitized performance summary endpoint.
4. Make the route and sidebar entry available to all signed-in users while
   retaining the standalone internal monitor for administrators.
5. Update the tracked cron example and production root crontab to hourly monitor
   materialization without changing daily collection/audit/pricing jobs.
6. Run typecheck, build, Go/Python tests, privacy review, deploy, and verify with
   both administrator and normal test accounts.
