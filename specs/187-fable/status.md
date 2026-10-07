# 2026-10-07 — compatibility verified; production enablement needs proxy authorization

claude-fable-5-1 is **not enabled on production**. The original issue183 immutable
runtime is still running. No production Fable channel, metadata, price option,
customer key or wallet was changed. The attempted binary rollout cancelled
after its 60-second drain gate. Original compose and nginx bytes were verified
restored, the task-owned DRAIN was removed, and no database rollback occurred.

## Completed

User selected A for exact-model compatibility and two-provider deployment.
The scoped fix covers adaptive thinking, native output_config retention, Chat
reasoning_effort mapping, bounded max tokens, pre-billing HTTP400 errors and
separate aggregate/5m/1h cache creation. Only Chat and Messages are supported.

The candidate was built from the exact production baseline: eleven changed/new
files and 2042 unchanged files checked byte-identical. Immutable candidate:
sha256:7d231e67f87f98ace75943882f3b8128dadfd7963ff579b31d03a6999c30d2f2.

The isolated canary passed twelve valid free full-path cases, five invalid
pre-submit HTTP400 cases and six bounded real retained-provider calls, all with
exact wallet/log charges. Real cache-hit/TTL probes were not added; these
dimensions have captured tariff evidence and synthetic full-path tests.

Source/security/deployment review, relevant Go suites, exact-base build tests,
94 operations tests and diff checks passed. Final canary proof promoted only the
private compatibility flag. Its True-plan SHA:
c3568f7825ccb30ebd3c594b96d31b9171f6dd94580256bab6703abc4a2b0bcf.

## Retained providers and proposed prices

Rolldek ccmax primary (priority10), Maolao group_4 backup (priority8). The six
calls returned the exact upstream model label; this is not independent proof
of model identity or long-run reliability. Rolldek was approximately2.5–3s and
Maolao3.7–7.2s in this canary. Production keys have not been promoted.

Unit: CNY per million tokens. Retail = highest retained same-request cost×1.5.
These prices are **not yet production prices**.

| Dimension | Rolldek cost | Maolao standard cost | Standard retail | Max retail |
|---|---:|---:|---:|---:|
| Input | 12 | 13.39 | 20.085 | 60.255 |
| Output | 60 | 66.95 | 100.425 | 301.275 |
| Cache read | 0.3 | 0.33475 | 0.502125 | 1.506375 |
| Cache creation 5m | 15 | 16.7375 | 25.10625 | 75.31875 |
| Cache creation 1h | 24 | 26.78 | 40.17 | 120.51 |

## Verified blocker

Nginx binds the individual host default.conf file read-only. The deployment
helper used os.replace on that host path; Docker's single-file bind retained
its original inode, so the container never received temporary maintenance and
status configuration. Host/container inodes were557500/540224 and both restored
files117114bytes. Nginx checks and reloads succeeded against the old file.
The drain guard timed out and **never attempted a binary swap**.

This is a deployment-helper compatibility defect, not an upstream model,
balance or customer-authentication failure. The helper now rejects this mount
before any write, checks actual container configuration on reload, and records
bounded sanitized drain diagnostics.

Repairing the shared proxy mount requires separately authorized recreation or
maintenance. It is not silently added to the model-only A scope. No live proxy
remount or replay of the cancelled rollout has been performed.

## Remaining

- Obtain brief proxy maintenance authorization, choose a low-traffic window,
  or defer. Preserve read-only mount protection.
- Promote/re-attest only the two dedicated source keys, stage disabled routes,
  then activate after verified runtime pricing synchronization.
- Verify ordinary access, all eligible lian123 keys and public pricing. The
  current active-key inventory had auto/text keys without model allowlists;
  no customer key restriction or balance was changed.
- Preserve video, other prices and existing provider operation boundaries.

Private credentials, canary evidence, initial DB backup and cancelled-attempt
records remain under /opt/ai-api-stack/backups/fable187-20261007.
The task-only local SSH transport helper was removed. Deletion of generated
Python bytecode caches was rejected by execution policy; those caches are
retained uncommitted rather than bypassing that restriction.
Issue187 remains In Progress, not complete:
https://github.com/qiaoguan1/new-api/issues/187.
