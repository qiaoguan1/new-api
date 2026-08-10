# Research: Provider Billing Interfaces

## Toonflow

The official web client uses `https://api.toonflow.net/web` and Bearer authentication. Relevant
read-only endpoints are `/web/operationLog/getOperationLog`, `/web/pointsPreview/getPreviewData`,
`/web/model/getModelData`, and `/web/user/getUserData`. Operation records expose `taskICode`,
`modelName`, `price`, `state`, `creationTime`, `completionTime`, and `errorReason`. State `2` is
completed, `-1` failed, and `1` in progress. Login requires CAPTCHA, so unattended collection uses
a previously authorized web token from a root-readable secret file and fails
closed when it is unavailable. The token is sent only to `api.toonflow.net`.

## Paisio

Paisio exposes authenticated classic NewAPI account, pricing and log APIs. Its logs can contain
per-second reserve/refund/completion rows. Net deductions must be reconstructed from the signed
quota evidence; raw log-row count is not a call count. Provider task identifiers are preserved
when present, while missing identifiers remain aggregate evidence only.

## Decision

Task-level evidence uses provider task identity. Provider list/catalog price is retained only as a
comparison field. No catalog price is promoted to actual cost, and no upstream cost changes Ark
official ×1.5 downstream pricing.
