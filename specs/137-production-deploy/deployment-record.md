# Issue 137 Production Deployment Record

## Release identity

| Item | Verified value |
| --- | --- |
| Reviewed main merge | `4336a72bd159d3f34cf37d5edb99be3fd0a385e1` |
| Production source baseline | `4cca558c70734dc5d81df5cab0949d4da857f37c` |
| Selective backport commit | `eb65d7032` |
| Release source SHA-256 | `43dcff0ff66d11aa88a480ffa059e4747ed678dd374aa7f348bd2be3d637d3f5` |
| Previous image | `new-api-fixed:patrol-repair-4cca558c` |
| Previous image ID | `sha256:7b1075dfbba08375e34002696c7b404bd56f141bca9a66c9188ef650333e3652` |
| Deployed image | `new-api-fixed:issue137-eb65d7032` |
| Deployed image ID | `sha256:63a028e3d11e974169db3c6b34e1e09d6209ac3b2e54628e9e04ac50aaa0c11d` |

The backport contains only the OAuth foreign-opener callback proof and the DOMPurify 3.4.13
override required by Issue 137. It does not attempt to deploy current `main` over the divergent
production source tree.

## Verification before activation

- OAuth regression test observed RED before the helper was ported.
- OAuth and channel tests: 11/11 passed after the port.
- Frozen Bun install, TypeScript typecheck, changed-file ESLint/Prettier, and production frontend
  build passed.
- Full repository lint/format still reports unrelated baseline defects; no changed file failed.
- Security review reported no actionable findings. The deferred single-use OAuth state hardening is
  tracked by Issue #138.
- Candidate image dark-run used isolated data/log directories and loopback port `127.0.0.1:13001`.
  Candidate `/api/status` passed and the temporary container, port, and environment file were removed.

## Backup and rollback

- Database recovery bundle:
  `/opt/ai-api-stack/backups/issue137-recovery/newapi-20260825-003426`
- Deployment rollback bundle:
  `/opt/ai-api-stack/backups/issue137-production-deploy-20260825-003507`
- The old image was exported into the rollback bundle and retained locally under its original tag.
- Database dump, configuration archive, rollback image archive, and bundle manifests passed SHA-256
  verification; the image archive also passed gzip integrity verification.

## Activation result

The compose image value was changed to the issue-specific tag and only `new-api` was force-recreated.
All non-`new-api` container IDs matched the pre-deployment snapshot.

- Container state: healthy.
- Compose validation: passed.
- Five activation rounds: `/api/status` 200, unauthenticated `/v1/models` 401 as expected, video
  readiness 200, `accepting=true`, `draining=false`, circuit closed.
- Three independent post-deployment rounds passed the same invariants.
- The new frontend asset `index.b420fa47ee.js` was served.
- The first independent status request immediately after restart took about 6.2 seconds; subsequent
  checks returned to about 160 ms.
- Release pointer: `/opt/ai-api-stack/releases/new-api-current` resolves to
  `/opt/ai-api-stack/releases/issue137-prod-eb65d7032`.

No database, key, route, price, provider allowlist, mounted data, or non-`new-api` service was changed.
