# Implementation Plan

1. Add a strictly validated, versioned official CNY video price catalog and a
   deterministic quote engine for the supported request contract.
2. Add a dedicated official video option plan that writes per-second base
   `ModelPrice` values and make the generic upstream-cost worker skip video.
3. Correct classic upstream log aggregation to preserve per-second/per-call
   units, refunds, success counts, and authenticated catalog price fields.
4. Replace actual-cost publication gates with official-price gates. Keep exact
   raw-model upstream evidence only for internal profit comparison.
5. Extend manifests and reports with privacy-safe downstream rate metadata and
   private cost/profit evidence.
6. Test fail-closed inputs, the Paisio refund case, Rolldek catalog evidence,
   route isolation, pricing arithmetic, and public privacy.
7. Review, merge, back up production, deploy without exposing credentials, run
   dry-run and live option verification, then repeat ten deterministic checks.

