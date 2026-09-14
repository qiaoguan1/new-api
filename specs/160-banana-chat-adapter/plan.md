# Implementation Plan

1. Define deterministic request validation and upstream response parsing.
2. Implement a minimal standard-library HTTP service with file-backed secrets.
3. Add explicit-safe fallback for Flash and a single Rolldek route for Pro.
4. Build and dark-run the container with dedicated test secrets.
5. Back up production, add an isolated Compose service and Nginx location, then create one NewAPI
   channel for `banana-flash` and `banana-pro`.
6. Verify catalog visibility and run one bounded end-to-end request per model.
7. Complete review, record evidence, and retain exact rollback assets.
