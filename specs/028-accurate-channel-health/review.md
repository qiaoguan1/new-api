# Review

## Scope reviewed

- Active-only aggregation and health classification
- Deterministic generator and internal UI patchers
- Existing host-hardening verification around Basic Auth
- Production rollback and access boundaries

## Findings resolved

1. The first deployment exposed that one enabled priority-zero channel was
   silently excluded from both upstream and unmatched totals. Global totals now
   derive from all enabled channels, while an unmatched enabled channel produces
   an explicit configuration warning.
2. Stale channel response times could be averaged with a fresh test and produce
   a false slow alert. Only response times from tests no older than two hours are
   now used.
3. The first enabled channel with an error was selected rather than the newest
   error. Selection now uses `last_error_at`.
4. Production Basic Auth returned HTTP 500 because the root-owned credential
   file was mode 0600 and unreadable by the Nginx worker. It is now root-owned,
   group Nginx, mode 0640, and the host-hardening verifier checks both metadata
   and worker readability.
5. Patchers originally rewrote files in place. They now write a same-directory
   temporary file, preserve mode, and atomically replace the target.

## Residual risks

- Topaz is included in global enabled-channel totals but is not yet mapped to a
  credential-backed upstream definition. It is intentionally visible as one
  configuration warning instead of being added to the pricing upstream list
  without a trustworthy billing collector.
- Automatic all-channel tests remain disabled because the current NewAPI
  routine can create billable media requests and auto-disable channels. Health
  for active traffic is current; idle channels require an operator test until a
  zero-cost provider-specific probe exists.

No unresolved correctness or security finding blocks this delivery.
