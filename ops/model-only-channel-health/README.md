# Model-only channel health page

Issue #32 removes private business and upstream details from the deployed
legacy `/channel-health` page. The page requests only the existing model
performance summary and displays model name, request count, success rate,
latency, and output speed. Its copy matches the hourly monitor cadence.

`patch_channel_health.py` is fail-closed, idempotent, and writes atomically.
Back up the production source and current image metadata before applying it.
