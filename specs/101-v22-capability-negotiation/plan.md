# Plan

1. Add HTTP contract tests for authenticated capability discovery with v2.1,
   v2.2, no header, and an unsupported header.
2. Make the capability snapshot accept an explicitly negotiated supported
   billing contract while retaining v2.1 as the no-header default.
3. Validate the request header at the capability endpoint and reuse the existing
   structured validation error.
4. Run the focused and full gateway test suites, then complete code and security
   review.
5. Build a reproducible image, deploy to staging, verify without paid requests,
   then deploy production with preserved SQLite state and rollback artifacts.
6. Run ten production no-charge checks and confirm downstream-visible fields.
