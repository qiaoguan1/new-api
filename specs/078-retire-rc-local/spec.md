# Feature Specification: Retire Obsolete Disk Expansion Boot Job

**Issue**: https://github.com/qiaoguan1/new-api/issues/78
**Branch**: `codex/issue-78-retire-rc-local`
**Status**: In Progress

## Requirements

- Verify the root ext4 filesystem already occupies the available system partition.
- Verify `/dev/sdb` does not exist before changing the boot script.
- Preserve the exact prior `/etc/rc.local` with mode and SHA-256 evidence.
- Remove only the executable bit so systemd no longer generates/runs rc-local at
  the next boot; do not delete the script or edit disk partitions.
- Clear the historical failed state and verify core services remain healthy.
- Document the one-command rollback (`chmod 755 /etc/rc.local`).
