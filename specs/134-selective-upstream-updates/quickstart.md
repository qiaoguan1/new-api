# Quickstart: Validate the Selective Upstream Update

## Prerequisites

- Git has `origin/main` and the fetched `upstream/main` reference.
- Bun 1.3 or later is available for frontend checks.
- No production credentials or production connection is required.

## Verify the frozen range

```powershell
$base = git merge-base origin/main upstream/main
$head = git rev-parse upstream/main
$count = git rev-list --count "$base..$head"
@{ base = $base; head = $head; count = $count }
```

Expected: the base and head match `spec.md`, and `count` is `97`.

## Verify ledger coverage

```powershell
$range = @(git rev-list --reverse `
  1721144221ec5c94dd87891a7ae1bee228e7bb63..2d8e50bf36e94200b809dfb39e73624ec48b1e23)
$rows = Select-String -LiteralPath `
  'specs/134-selective-upstream-updates/upstream-compatibility.md' `
  -Pattern '^\| [0-9]{3} \| ([0-9a-f]{12}) \|' | ForEach-Object { $_.Matches[0].Groups[1].Value }
if ($rows.Count -ne 97 -or ($rows | Sort-Object -Unique).Count -ne 97) { throw 'invalid ledger coverage' }
foreach ($sha in $rows) {
  if (-not ($range | Where-Object { $_.StartsWith($sha) })) { throw "outside range: $sha" }
}
```

## Run the focused OAuth regression

```powershell
Set-Location web
bun test src/features/auth/lib/__tests__/oauth-callback-mode.test.ts
```

## Run frontend verification

```powershell
Set-Location web
bun test
bun run typecheck
bun run lint
bun run build
```

## Run affected repository regression suites

```powershell
Set-Location ..
$env:GOTMPDIR = 'C:\Users\Administrator\AppData\Local\Temp'
go test -count=1 ./controller ./middleware ./model ./relay/...
go test -count=1 ./service -run '^TestObserveChannelAffinityUsageCacheByRelayFormat_MixedMode$'
go test -count=1 ./service -run '^TestObserveChannelAffinityUsageCacheByRelayFormat_UnsupportedModeKeepsEmpty$'
python -m unittest discover -s ops/channel-monitor/tests -p 'test_*.py'
python -m unittest discover -s ops/video-job-gateway/tests -p 'test_*.py'
```

The two service tests pass independently but currently share counters when run together; that
pre-existing isolation defect is tracked in [Issue #135](https://github.com/qiaoguan1/new-api/issues/135).

No command in this guide deploys, submits a paid request, reads credentials, or writes production.
