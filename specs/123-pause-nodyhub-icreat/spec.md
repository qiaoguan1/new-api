# Pause NodyHub and iCreat production channels

## Objective

Pause all NewAPI production routing channels owned by NodyHub and iCreat while
retaining channel definitions, credentials, billing history, and monitoring
history.

## Acceptance criteria

- Channels 27, 28, 40, and 41 are preserved and have status 2.
- Every ability belonging to those channels has `enabled=false`.
- No unrelated channel row or ability is changed.
- A private rollback backup is created before the update.
- No in-flight task is interrupted.
- NewAPI is healthy after its in-memory channel cache is refreshed.
- The post-change upstream summary distinguishes routing state from monitoring
  collection state.

## Non-goals

- Do not delete upstream credentials, channels, logs, or ledger data.
- Do not change the standalone video gateway provider set.
- Do not modify pricing, CLR, models, priorities, or weights.

