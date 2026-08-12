# Implementation Plan: Independent Daily Pricing Reliability

1. Capture the production failure and the exact policy/audit/mapping relationship.
2. Add a failing test for a reviewed exact alias absent from the mapping report
   without an `unavailable_models` marker.
3. Make approved exact policy matches authoritative while retaining report
   revision and identity checks for discovered rows.
4. Run targeted and complete channel-monitor suites.
5. Review, back up production files, deploy atomically, and verify dry-runs.
6. Verify the next 08:20/08:30/08:35/08:40 Beijing-time run through logs and
   persisted run artifacts.
