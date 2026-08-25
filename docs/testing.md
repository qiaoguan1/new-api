# Testing from a clean worktree

The Go root package embeds both frontend distributions. Generate them before
running the complete Go suite; the generated `dist/` directories remain ignored
and must not be committed.

```bash
cd web/classic
bun install --frozen-lockfile
bun run build

cd ../default
bun install --frozen-lockfile
bun run build

cd ../..
go test ./... -count=1
```

For backend-only changes, run impacted packages first. The complete prepared
suite remains the merge gate.
