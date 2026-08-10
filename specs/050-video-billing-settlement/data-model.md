# Data Model: Complete Video Billing Settlement

## Gateway Video Job Billing

- `billing_contract_version`
- `billing_status`: reserved, settlement_pending, settled, refunded, pending_review
- `reserved_cny_exact`, `charged_cny_exact`, `refund_cny_exact`, `supplement_cny_exact`
- `official_pricing_revision`, `official_cost_cny_exact`, `markup_exact=1.5`
- private: provider actual cost, evidence source/fingerprint, observed time, settlement revision

The public snapshot omits every private field and holds the result while billing is not terminal.

## Gateway Settlement Revision

- `settlement_id` unique and deterministic
- `job_id`, `revision`, `evidence_fingerprint`
- exact provider task identity and private evidence metadata
- previous/final user charge and delta
- created timestamp

The transaction inserts the revision and updates the job billing snapshot atomically.

## NewAPI Task Billing Context

- frozen reservation quota and final charged quota
- billing status and settlement revision/fingerprint
- refunded and supplemented quota
- public currency/amount derivation
- private provider evidence metadata only inside `TaskPrivateData`

Wallet debt is derived from `users.quota <= 0`; no separate mutable debt boolean is needed.
