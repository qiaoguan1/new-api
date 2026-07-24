# Production Host Hardening

**Issue**: [#23](https://github.com/qiaoguan1/new-api/issues/23)

## Goal

Remove direct public access to the channel-monitor administration API while
preserving the authenticated Nginx route and existing key-based operations.

## Requirements

1. TCP 8791 listens only on the host-side Docker bridge and is unreachable from
   the public Internet.
2. Nginx on the `app-net` Docker network can still reach the administration
   API and unauthenticated HTTPS requests receive HTTP 401.
3. SSH public-key login is verified before password authentication is disabled.
4. Root password authentication is disabled while root public-key login remains
   available for existing automation.
5. UFW permits public TCP 22, 80, and 443, and permits TCP 8791 only from the
   `app-net` subnet.
6. Fail2ban protects SSH and starts automatically.
7. Every changed host configuration has a mode-0700 rollback directory.
8. NewAPI and all production containers pass post-change checks.

## Non-goals

- Changing pricing or billing behavior.
- Rotating application credentials.
- Removing root key login before dependent automation is inventoried.
