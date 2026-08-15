# Plan

1. Characterize the authenticated `/api/pricing` metadata already stored in
   the private upstream ledger.
2. Add failing tests for catalog-only text/image pricing, actual-over-catalog
   priority, multi-source maxima, invalid metadata, and video protection.
3. Implement strict catalog normalization and source-level maximum evidence
   selection.
4. Extend private run summaries with catalog evidence fields and skip reasons.
5. Run focused and full channel-monitor tests, then comprehensive and security
   review.
6. Produce a production dry run before any atomic price write; deploy only with
   rollback backup and verify the next daily email digest.
