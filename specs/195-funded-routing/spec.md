# Funded text/image routing

Issue: https://github.com/qiaoguan1/new-api/issues/195

## Direct human decisions

- Prefer Haina and the previously configured providers with usable balances.
- Do not send new requests to confirmed exhausted routes.
- Haina text only is promoted; Haina image remains disabled.
- Hanhe channel 48 may remove the already-disabled `gpt-6` from its runtime
  model list, keeping `gpt-5.5,gpt-5.6-sol`. Its four disabled GPT-6/Astra
  abilities stay unchanged.

## Scope

First tier, priority 100: channel IDs
`6,38,39,48,52,56,57,58,59,60,61,62,63,64,65,66,67,68,69`.
Confirmed depleted: Maolao image channel `23`, which becomes status 2 and has
its abilities disabled. Channel 72 uses a different key; account identity and
current funds are unknown, so channel 23's balance is not applied to it.

Only channel status/priority, enabled-ability priority and depleted-ability
enabled fields may change, plus the explicitly authorized model alignment for
channel 48. Preserve weights, mappings, groups, credentials, prices, user/key
permissions, previously disabled abilities and all unrelated channels.
Unknown balance is not zero. Jojo and Haina image remain paused. Existing tasks
are never replayed; their original credentials and polling remain available.
Video gateways, video-only channels, specifications and prices are unchanged.

## Verification

1. Dated positive-balance evidence plus exact live route inventory.
2. A private before/after manifest and guarded rollback; atomic compare-and-swap
   checks complete channel and ability row sets and immutable configuration.
3. Deterministic planner tests, independent seven-criterion/security review and
   PostgreSQL read-only canonical-hash validation before execution.
4. Read back actual changed rows and preserve unrelated configuration hashes.
5. Account for native cache timing and affinity bypass. No restart, generation,
   recharge, customer-balance changes or native software promotion.
