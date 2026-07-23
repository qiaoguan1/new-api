# Verification

## Pre-deployment

- 56 Python monitor tests pass; Python compilation and `git diff --check` pass.
- Prettier, ESLint, TypeScript, and the production frontend build pass.
- The current production inventory contains exactly 35 expected channels and
  reports policy state `pending` with no drift.
- The complete migration executed against production PostgreSQL inside a forced
  rollback transaction; syntax, locks, name guards, and non-name fingerprints
  passed, with zero persistent channel changes.
- The standalone guide patch upgrades the current production HTML, passes
  `--check`, and a second application leaves its SHA-256 unchanged.

## Production

Pending backup, deployment, live transaction, monitor regeneration, and final
verification.
