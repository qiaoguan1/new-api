# Production Verification

## Release identity

- Source commit: `065ae51c45c9b45769ba891ce457d0d1f1efd046`
- Image: `xtai/video-job-gateway:routing-p0-065ae51c`
- Catalog revision: `2026-08-14.2`
- Catalog SHA-256: `3813c8c21879739ed5ecaa8e85ae5bfe53166448865762ae3318d17699ed8b07`
- Runtime source SHA-256: `4f192dbcf1921a25a861c1d01a1ba342af3a53640b18b615511f8dc18234cbae`

The image labels match all three values and the Docker build verified the
catalog and complete runtime source before installing packages.

## Deployment

- Staging and production were backed up before migration.
- The previous containers remain stopped under issue-specific rollback names.
- Production preserved 58 historical tasks.
- SQLite `pragma integrity_check` returned `ok` after deployment.
- The required Docker network alias `video-job-gateway-v2-production` was
  restored and `nginx -t` passed before reload.

## Ten no-charge rounds

Every round verified:

- public `/health`, `/ready`, `/v1/capabilities`, and `/v1/video-prices`: HTTP 200;
- no `sd2` in the published capability or price documents;
- standard 720p route: Paisio `sd3-720p`, then Toonflow `Seedance 2.0`;
- evidence-backed recovery loop and durable attempt ledger present;
- 58 jobs, zero active jobs, zero pending settlements, zero pending Webhooks;
- all three incident jobs preserved as failed and refunded;
- no paid generation request submitted.

Result: **10/10 PASS**.
