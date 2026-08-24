# Verification: Selective Upstream Updates

## RED Evidence

- Command: `bun test src/features/auth/lib/__tests__/oauth-callback-mode.test.ts`
- Date: 2026-08-24
- Result: expected failure, exit code 1.
- Evidence: Bun could not resolve `../oauth-callback-mode` because implementation had not been added.
- Upstream provenance: `e78e1db1e4ed7d65e37c2527826f290c0c63b041`.

## GREEN Evidence

- Focused command: `bun test src/features/auth/lib/__tests__/oauth-callback-mode.test.ts`
- Result: 10 passed, 0 failed.
- Covered: valid bind proof, foreign opener, mismatched provider/state, closed opener, missing or
  blocked session storage.

## Regression Suites

- `bun install --frozen-lockfile`: passed after Bun regenerated the DOMPurify 3.4.13 lock.
- `bun test`: 90 passed, 0 failed.
- `bun run typecheck`: passed.
- Changed-file Oxlint: passed.
- Targeted Oxfmt: passed.
- `node scripts/add-copyright.mjs --check`: passed.
- `bun run build`: passed with Rsbuild 2.1.6.
- Repository-wide format check still reports three pre-existing script files outside this change;
  none was modified.

## Backend and Custom Operations

- `go test -count=1 ./controller ./middleware ./model ./relay/...`: passed after directing Go's
  temporary linker output to the C drive; the default E-drive TEMP had less than 1 GB free.
- `python -m unittest discover -s ops/channel-monitor/tests -p 'test_*.py'`: 176 passed.
- `python -m unittest discover -s ops/video-job-gateway/tests -p 'test_*.py'`: 142 passed.
- `git diff --exit-code origin/main -- '*.go'`: passed; this batch changes no Go file.
- The full `./service` package has a pre-existing test-isolation failure: two channel-affinity usage
  cache tests pass separately but fail together because counters are shared. Tracked independently
  in [Issue #135](https://github.com/qiaoguan1/new-api/issues/135); no production Go logic was changed.

## Safety

- Production deployments: 0.
- Paid upstream requests: 0.
- Credential reads: 0.
- Production database writes: 0.
- Protected routing, pricing, monitoring, video, and model-mapping code changes: 0.

## Review Fixes

- Added documentation to the exported OAuth callback-mode contracts and functions.
- Removed `.specify/feature.json` from version control and ignored the mutable local feature pointer.
- Corrected quickstart Go commands to use passing relevant packages and isolated service tests; the
  pre-existing combined-test failure remains tracked in Issue #135.
- Comprehensive review artifact: [Issue #134 comment](https://github.com/qiaoguan1/new-api/issues/134#issuecomment-5397181930).
