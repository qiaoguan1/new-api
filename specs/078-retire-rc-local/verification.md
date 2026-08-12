# Verification

Production evidence before remediation:

- Root source: `/dev/sda1`, ext4, 98.3 GiB.
- Root available: 35.9 GiB; usage 59%.
- `/dev/sdb`: absent.
- The script invokes `growpart` and `resize2fs` for both sda1 and nonexistent sdb1.
- The historical service state is failed because `growpart /dev/sda 1` returns
  `NOCHANGE` under `#!/bin/sh -e`.

The production outcome and ten-round checks are recorded on Issue #78 without
including secrets.
