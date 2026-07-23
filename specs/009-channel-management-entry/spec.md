# Feature Specification: Channel Management Entry Points

**Issue**: [#9](https://github.com/qiaoguan1/new-api/issues/9)
**Status**: In Progress

## User story

As the site administrator, I can start adding a NewAPI channel, manage existing
channels, and configure upstream billing collection directly from Channel Monitor,
so a later channel addition does not depend on remembering hidden URLs.

## Acceptance criteria

1. Integrated and standalone Channel Monitor surfaces show visible add/manage links.
2. `/channels?action=create` opens the existing create-channel dialog for an authenticated admin.
3. The upstream collection link uses `/channel-monitor/upstreams-admin.html` and retains HTTP Basic Auth.
4. The UI explains that NewAPI channel setup and upstream billing setup are both required for reconciliation and automatic pricing.
5. All links are relative and contain no credential material.
6. Frontend typecheck/build, patch tests, production backup, deployment, and browser-level verification pass.

## Out of scope

- Creating a real upstream channel or storing a real key during this change.
- Removing either the NewAPI admin role check or Channel Monitor Basic Auth.
- Changing pricing formulas or running paid model probes.
