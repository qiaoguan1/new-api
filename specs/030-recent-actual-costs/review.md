# Review

## Findings

No release-blocking findings remain.

The first isolated fallback design could allow an older expensive source to
override another source's target-day sample. Production was not changed at
that point. The final selector gives global precedence to any valid target-day
sample and has a regression test for that case.

The first final validator also revealed that historical ledger inventory could
resurrect retired models. The live transaction had not introduced a wrong
price, but the worker was tightened so only current enabled-channel
configuration defines inventory. A partial-deployment regression test covers
the staged patch path used on production.

## Residual limits

The worker intentionally leaves a model unchanged when there is no complete,
positive, model-level actual deduction within seven Beijing business days. It
does not infer prices from catalogs, balance changes, or synthetic paid calls.
This is a safety property, not an unresolved write failure.
