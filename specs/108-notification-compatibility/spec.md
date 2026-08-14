# Specification: notification event compatibility

## Goal

Preserve every notification event already accepted by production while keeping
the input limits and validation added by #107.

## Requirements

1. Balance, patrol, and credential lifecycle event kinds remain accepted.
2. Patrol events require a bounded code and one of `info`, `warning`, or
   `critical` severity values.
3. Request bodies, names, numbers, and HTML output remain bounded and escaped.
4. The server-side recipient and RootAuth route remain unchanged.
5. Production NewAPI is not replaced until compatibility tests pass.

## Acceptance

- Existing patrol and credential fixtures pass.
- Invalid patrol fields and oversized requests fail closed.
- Controller/router suites pass and production endpoint tests remain healthy.
