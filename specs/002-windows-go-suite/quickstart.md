# Quickstart

```powershell
Set-Location web/classic; bun install --frozen-lockfile; bun run build
Set-Location ../default; bun install --frozen-lockfile; bun run build
Set-Location ../..
go test ./relay/helper ./service -count=1
go test ./... -count=1
```

Repeat the two Go commands three times. Generated `dist/` directories remain ignored.
