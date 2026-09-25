# Catalog repair acceptance

2026-09-25. The public model square was still reading native /api/pricing, which did not contain the seven gateway video models. This was separate from the enabled /v1/models API catalog.

Only the existing public frontdoor was updated. Main NewAPI, price tables, upstream routing and balances were not changed. Nginx adds exact /api/pricing routing and points the existing public API endpoints to xtai-public-video-catalog176. Prior xtai-public-video is stopped and retained for rollback. Image digest: sha256:9fdede898159fd4389bf42e55c5d321bb82688b3a32f79b68d2b0fbb25b36134.

Server and independent local-machine checks of https://api.aixingtuyun.com/api/pricing both returned: ready,7 added,12 video-group models,54 total models. All47 previous rows were semantically identical. Each added record's normalized model_price×video group ratio exactly matches the reserved CNY amount. Records include tested resolution/duration and the real JSON POST /v1/videos endpoint, not chat/completions. Empty/broken video sources preserve the native catalog and disclose degradation.

The separately user-approved legacy credential migration changed only that one token's group SAN→auto. Its key and all other token fields were verified unchanged. Same-key /v1/models now returns200 with all seven videos. No paid generation or monetary mutation was performed. Other legacy tokens were not changed.

Backup: /opt/ai-api-stack/backups/lian123-catalog-20260925 (private; no full key in token snapshot). First immediate post-reload probe reached an old nginx worker and triggered safe config restoration. A bounded convergence check verified the final rollout. Legacy service-key /v1/capabilities remains200. Full public-video package tests passed.
