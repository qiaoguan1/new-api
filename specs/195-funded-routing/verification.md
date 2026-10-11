# Production verification — 2026-10-11

The user-authorized routing configuration is live. This does not deploy the
separate native software candidate from issue 190.

## Exact outcome

- 19 previously enabled text/image channels are priority 100, with their enabled
  abilities also priority 100: Haina text, CodePlan, Hanhe, Paisio and Nody image.
- Maolao image channel 23 is status 2 and its ability is disabled. Existing task
  data and credentials were not deleted or replayed.
- Haina image 37 remains disabled, as explicitly requested.
- Hanhe48 models are `gpt-5.5,gpt-5.6-sol`; its four previously disabled GPT-6
  and Astra abilities retain enabled=false and priority 4.
- Maolao72 uses another key and has no verified current account balance;
  Rolldek, Toonflow, Topaz and Banana Flash funding ownership remain qualified.
  Their unknown balance was not silently converted to zero. They were not
  promoted or newly disabled. Jojo remains paused.

## Verified evidence

Operation: `0d80c2dfbc9b463f9c0a66f70e808cee`.
Private evidence directory:
`/opt/ai-api-stack/backups/issue195-funded-routing-0d80c2dfbc9b463f9c0a66f70e808cee`.
Directory0700 and manifest0600 were read back. Artifacts contain sanitized
configuration projections and hashes, not plaintext API credentials.

- PostgreSQL canonical projection and Python hash matched before execution:
  `9f5ccb277ac9e57edcfb11aceab8dac4ee5da513e0ee739323a420c68e438c46`.
- One journaled atomic transaction completed with verified after hash:
  `669916c5f03e433118ab4b8be31245be2d079bc0c3a94396ee8ced7d462b3fcd`.
- All unrelated channel/ability projections and the complete global options
  hash remained identical. Native instance/image remained unchanged.
- After-commit native cache synchronization was observed, including
  `2026/10/11 23:45:56 channels synced from database`.
- Both applicable affinity rule caches were cleared through the existing
  authenticated interface; each returned deleted=0. No settings were changed.
- Fresh public GET: status success; pricing success; Image2 present; video
  catalog ready with all seven existing new video models.
- Root and independent offline verification: 28 tests PASS. Independent review
  fixed five findings, unaddressed0, posted to issue195 comment6110729851.

No generation, paid test, recharge, customer-balance modification, task replay,
native restart or video configuration change was performed. Actual output for
every model/specification is not claimed from these non-generating checks.

## Recovery boundary

The private recovery file contains the exact minimal rollback SQL. It aborts if
current protected configuration differs from the expected after state; it does
not restore any historical financial database or overwrite newer operator
changes. Unknown outcomes must use read-only inspection, not another apply.
