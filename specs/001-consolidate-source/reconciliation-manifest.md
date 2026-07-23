# Reconciliation Manifest

Canonical source: `/root/new-api-build/new-api`
Comparison source: `/root/new-api-build/wechatpay-final`
Backup: `/root/maintenance-backups/20260723-142032/new-api-working-tree.tar.gz`

## Integrate from final: new functional paths

| Path | Classification | Disposition |
|---|---|---|
| `controller/topup_wechat.go` | backend implementation | integrate |
| `controller/topup_wechat_test.go` | backend test | integrate test-first |
| `model/topup_wechat_test.go` | model test | integrate test-first |
| `setting/payment_wechat.go` | configuration implementation | integrate |
| `setting/payment_wechat_test.go` | configuration test | integrate test-first; repair fixture only |
| `web/default/src/features/wallet/components/dialogs/wechat-pay-dialog.tsx` | frontend implementation | integrate |
| `web/default/src/features/wallet/hooks/use-wechat-payment.ts` | frontend implementation | integrate |

## Integrate from final: reviewed differing paths

| Path | Classification | Disposition |
|---|---|---|
| `.dockerignore` | build hygiene | integrate reviewed node-module rules |
| `controller/topup.go` | payment integration | integrate |
| `go.mod` | dependency | integrate WeChat SDK delta |
| `go.sum` | dependency checksums | integrate WeChat SDK delta |
| `model/topup.go` | payment persistence | integrate |
| `router/api-router.go` | routing | merge payment routes and preserve canonical custom routes |
| `setting/operation_setting/payment_setting_old.go` | legacy compatibility | integrate reviewed payment-method delta |
| `setting/ratio_setting/model_ratio.go` | pricing compatibility | integrate reviewed completion-ratio override |
| `web/default/src/features/channels/api.ts` | route compatibility | integrate |
| `web/default/src/features/wallet/api.ts` | frontend payment API | integrate |
| `web/default/src/features/wallet/components/recharge-form-card.tsx` | wallet UI | integrate |
| `web/default/src/features/wallet/hooks/index.ts` | frontend exports | integrate |
| `web/default/src/features/wallet/hooks/use-payment.ts` | payment orchestration | integrate |
| `web/default/src/features/wallet/index.tsx` | wallet integration | integrate |
| `web/default/src/features/wallet/lib/billing.ts` | billing UI logic | integrate |
| `web/default/src/features/wallet/lib/payment.ts` | payment utility | integrate |
| `web/default/src/features/wallet/types.ts` | frontend types | integrate |
| `web/default/src/routeTree.gen.ts` | generated route compatibility | integrate and validate via frontend build |

## Preserve from canonical

- Every canonical path not listed above.
- Existing custom channel monitor, RunningHub, OpenAI compatibility, quota, performance, and sidebar changes.
- Canonical-only migration utilities under `bin/`.

`router/api-router.go` is an overlapping customization and must be merged semantically rather than replaced blindly.

## Exclude from final

| Path | Reason |
|---|---|
| `.dockerignore.bak` | backup artifact |
| `controller/topup_wechat.go.bak-debug` | debug backup artifact |
| `setting/ratio_setting/model_ratio.go.backup-20260722-184746` | timestamped backup artifact |
| `web/default/dist/favicon.ico` | generated build output |
| `web/default/dist/logo.png` | generated build output |
| `web/default/dist/pay-apple.png` | generated build output |
| `web/default/dist/pay-card.png` | generated build output |
| `web/default/dist/pay-google.png` | generated build output |

## Remove from canonical working tree after backup verification

These untracked files are redundant editor/workflow backups already preserved in the verified working-tree archive:

- `Dockerfile.bak`
- `model/perf_metric.go.bak-`
- `relay/channel/openai/adaptor.go.bak-workflow-1782221348`
- `relay/channel/openai/runninghub.go.bak-workflow-1782221236`
- `relay/channel/openai/runninghub.go.bak-workflow-1782221348`
- `router/api-router.go.bak-`
- `web/default/src/hooks/use-sidebar-data.ts.bak-`

## Safety boundary

No private key, certificate, `.env`, database file, container volume, production image rebuild, or service restart is part of this reconciliation.
