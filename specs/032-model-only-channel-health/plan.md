# Implementation Plan

1. Add a fail-closed production-source patcher and safe page template.
2. Test replacement, idempotency, unknown-source refusal, and privacy markers.
3. Back up and patch the authoritative production customization tree.
4. Build a candidate image and verify the browser page with an admin session.
5. Run ten rounds, review, merge, and close the issue.
