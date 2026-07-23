# Implementation Plan

1. Inventory the complete production channel table without keys.
2. Define and test an explicit canonical mapping.
3. Build a guarded, idempotent, name-only transaction with backup and non-name
   fingerprint verification.
4. Document `上游名 · 用途` in both Channel Monitor entry variants.
5. Review, deploy, apply once, regenerate monitor data, and re-run all health
   checks.
