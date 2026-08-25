# Quickstart: Verify WeChat history reconciliation

```powershell
go test ./controller -run 'Test(Reconcile|GetUserTopUps)' -count=1
go test ./model -run 'Test.*TopUp' -count=1
go test ./controller ./model -count=1
go test ./... -count=1
```

Expected: current-user filtering, max-five budget, shared cancellation, SUCCESS validation/credit, terminal update, fail-open behavior and existing payment tests all pass. Production verification is read-only: fetch current-user list, query one pending order, fetch again, and record only state counts.
