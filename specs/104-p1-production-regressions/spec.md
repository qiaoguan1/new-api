# Specification: P1 production regression recovery

## Goal

Restore trustworthy daily pricing, video-provider eligibility, and upstream
balance monitoring without weakening fail-closed billing controls or creating
paid verification traffic.

## Requirements

1. Generic text and image pricing is evaluated per channel and per model. A
   failed or incomplete source blocks only models whose trusted evidence depends
   on that source; unrelated trusted models remain eligible for atomic updates.
2. Official video pricing remains isolated from generic upstream-cost pricing.
   Unknown or heuristic video aliases remain fail-closed.
3. Rolldek can enter the production video route only after a unique terminal
   task-level net-cost collector, reconciliation evidence, and regression tests
   satisfy the same billing approval gate used by other providers.
4. Toonflow balance and billing collection reads its credential only from the
   controlled secret path. The credential must remain owner-restricted with mode
   `0600`, must not be logged, and a permission regression must be detected.
5. Active upstreams are monitored independently from disabled upstreams.
   Collection failures remain `unknown`, never zero; low balance and collection
   failure events use deduplicated administrator notifications.
6. Paisio scheduler-capacity failures trigger bounded provider isolation. A
   request without an upstream task can use the existing definite-failure
   fallback; a request with an upstream task can advance only after the
   authoritative failed-task collector records its exact net cost. Historical
   task polling and settlement remain available during isolation.
7. Production changes are backed up and reversible. Verification must not
   modify CLR, restore TelecomJS/PackAPI/Unity2, or create a paid video task.

## Non-goals

- Inventing actual cost from catalog, reservation, or balance-delta values.
- Approving a provider without task-level settlement evidence.
- Automatically bypassing CAPTCHA or rotating third-party credentials.
- Changing downstream pricing, public channel names, or historical billing.

## Acceptance

- Regression tests fail before each code change and pass afterward.
- Daily pricing produces a non-zero eligible plan when trusted evidence exists,
  while preserving every blocked model and all official video prices.
- Toonflow collection returns complete with the secret still mode `0600`.
- Every active configured balance target is either complete or has a precise,
  notified failure; disabled targets do not degrade active-health totals.
- Rolldek remains excluded unless its full billing approval criteria pass.
- Paisio capacity errors are isolated without duplicate task creation.
- Staging and production pass ten no-charge verification rounds.
