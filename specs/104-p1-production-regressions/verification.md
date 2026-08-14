# Verification record

## Production recovery performed

- Backed up channel rows, upstream monitor configuration, and pricing options.
- Restored Toonflow collection with an unchanged secret value and owner-only
  `0600` permissions. A privileged and service-user read check succeeded.
- Isolated JojoCode text/image routing after the upstream returned
  `AUTH_SESSION_LIMIT`; recovery is tracked in #106 and requires the upstream
  account owner to revoke sessions or rotate the password.
- Excluded disabled 0809 and APIKeyFun sources from the active monitor scope.
  The final active balance run reported 11/11 complete and 0 unknown.
- Recomputed the daily audit and applied exactly five eligible text/image
  prices from trusted actual-cost evidence at 1.5 markup. The 1,174 remaining
  models stayed fail-closed; official video pricing remained unchanged.
- Isolated the legacy NewAPI Rolldek video channel. Gateway billing approval
  remains false because production has no task-level Rolldek settlement
  evidence. The authorized canary is tracked in #105.
- Re-ran official video pricing successfully after channel isolation; all nine
  decisions remained on the approved official-price path.

## Exact price spot checks

- `gpt-5.5`: input 1.545 -> 2.3175 CNY/M; output 9.27 -> 13.905.
- `gpt-5.6-luna`: input 0.8 -> 1.2; output 6.4 -> 9.6.
- `gpt-5.6-sol`: input 1.545 -> 2.3175; output 9.27 -> 13.905.
- `gpt-5.6-terra`: input 0.2 -> 0.3; output 1.2 -> 1.8.
- `gpt-image-2`: 0.0618 per call -> 0.0927 CNY per call.

## Automated validation before review

- Video gateway focused tests: 27 passed.
- Video gateway full suite: 142 passed.
- Channel monitor suite: 116 passed.
- Go controller/router suites: passed.

No paid video task was created and no credential value was copied into the
repository, logs, Issue, or verification record.
