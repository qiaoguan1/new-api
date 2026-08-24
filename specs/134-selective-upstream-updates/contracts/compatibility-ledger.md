# Compatibility Ledger Contract

`upstream-compatibility.md` is the authoritative audit artifact.

Each table row MUST contain:

1. A contiguous ordinal from `001` through `097`.
2. A unique immutable upstream commit prefix resolving within the frozen range.
3. The upstream subject or a faithful short summary.
4. One valid disposition: `adopt`, `manual-port`, `already-equivalent`, `defer`, or
   `reject-with-conflict`.
5. A primary subsystem and a concrete rationale.

Validation MUST fail when a range commit is missing, duplicated, outside the range, or assigned an
unknown disposition. The ledger is decision evidence only; it MUST NOT contain credentials,
authenticated payloads, private provider data, or copied production state.
