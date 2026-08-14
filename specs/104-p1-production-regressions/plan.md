# Plan

1. Capture current production configuration, scheduler outputs, collector
   status, route eligibility, and rollback metadata without exposing secrets.
2. Trace the existing pricing, credential validation, balance aggregation,
   billing approval, and provider-isolation code paths.
3. Add focused regression tests for the five P1 symptoms before implementation.
4. Implement the smallest deterministic changes that preserve fail-closed
   pricing and task-level billing evidence.
5. Run focused and full relevant test suites, then perform comprehensive and
   security review with zero unaddressed findings.
6. Build and deploy to staging, validate with read-only endpoints and stored
   fixtures, then deploy production with backups and rollback pointers.
7. Run ten no-charge production verification rounds and reconcile scheduled
   job output, monitoring state, route eligibility, and notification health.
