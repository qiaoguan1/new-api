# Implementation Plan

1. Inspect the running container and immutable image using sanitized outputs.
2. Extract `/app` from the exact image and save the image as an offline archive.
3. Generate a secret-free runtime manifest and SHA-256 inventory.
4. Compare critical files with the old current release and verify the snapshot.
5. Atomically switch the current symlink without restarting production.
6. Run ten health, settlement, Webhook and pointer verification rounds.
