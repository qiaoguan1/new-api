# Implementation Plan

1. Trace the audit alert schema and reproduce the blocking behavior in a test.
2. Separate recognized underpricing signals from non-recoverable critical
   model alerts in the audit policy.
3. Allow recovery candidates to use only the existing complete, healthy,
   enabled source intersection and existing price-change limits.
4. Replace catalog-price critical alerts with comparisons against complete
   model-level actual billing evidence and add structured recovery evidence.
5. Run focused and full monitor tests plus a comprehensive review.
6. Back up production, deploy the worker and audit policy, perform a dated dry run, execute once,
   and verify database values, ledger evidence, monitoring output, and ten
   read-only validation rounds.

## Safety Strategy

The change does not relax credential, collection-completeness, channel-health,
billing-kind, markup, or maximum-change checks. It only reclassifies explicitly
recognized price-below-cost alerts as a reason to calculate a corrective price.
Any other critical model alert wins and keeps the model blocked.
