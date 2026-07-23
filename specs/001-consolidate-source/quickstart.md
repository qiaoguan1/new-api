# Quickstart: Validate Consolidated Source

Run from `/root/new-api-build/new-api` on the source server. These commands do not deploy or restart production.

## 1. Confirm branch and scope

```bash
git branch --show-current
git status --short
git diff --check
```

Expected branch: `codex/issue-1-consolidate-server-source`.

## 2. Restore dependencies

```bash
go mod download
cd web/default
bun install --frozen-lockfile
cd ../..
```

## 3. Run focused backend tests

```bash
go test ./setting ./model ./controller
```

## 4. Run full backend tests

```bash
go test ./...
```

## 5. Validate the frontend

```bash
cd web/default
bun run typecheck
bun run build
cd ../..
```

Use the repository's equivalent script if a named command is not present in `package.json`, and record that substitution.

## 6. Check repository hygiene

```bash
git diff --check
git status --short
find . -type f \( -name '*.bak' -o -name '*.backup-*' \) -print
```

No newly imported backup artifact or credential material is allowed.

## 7. Confirm production is unchanged

```bash
docker ps --format '{{.Names}}\t{{.Status}}'
```

All previously running containers should remain up; this workflow must not rebuild or restart them.
