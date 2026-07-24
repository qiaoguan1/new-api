# Recent actual-cost pricing fallback

Issue #30 adds a seven-complete-Beijing-day lookback to the deployed pricing
worker. It keeps the target-day credential completeness gate unchanged and
uses only model-level `per_model_real_cost` records derived from upstream
billing logs.

If at least one healthy enabled source has a target-day model sample, the
selector uses target-day samples only. It consults each source's newest older
sample only when the model has no target-day sample anywhere, preventing stale
high costs from overriding current actual deductions. The existing worker then
takes the highest eligible same-basis cost across sources and retains its 5x
movement cap, billing-kind checks, critical-alert guards, backups, and atomic
option transaction.

The worker's inventory remains the current healthy enabled channel
configuration. A model present only in historical billing is not rediscovered
or written.

`patch_auto_pricing_recent_costs.py` is fail-closed and idempotent. Deployment
must first validate a patched copy in an isolated root, then create a restricted
rollback bundle before patching production. A dry-run and manual per-model
review are mandatory before a live invocation.

`patch_completion_ratio_override.py` applies the corresponding NewAPI runtime
fix to an older production source tree. The repository-native Go change and
tests are authoritative; the patcher exists only to rebuild the currently
deployed customization tree without replacing its Topaz, payment, or media
adaptors.
