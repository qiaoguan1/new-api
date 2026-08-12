# Implementation Plan: Daily Patrol and Safe Self-Healing Robot

**Branch**: `codex/issue-85-patrol-repair-bot` | **Date**: 2026-08-13 | **Spec**: [spec.md](./spec.md)

## Summary

Add a standard-library Python patrol engine with declarative checks and repair policies, a persistent incident state machine and a NewAPI notification adapter. Deploy it behind a separate unprivileged loopback API and a root systemd oneshot/timer. The root process has no listener and accepts only a fixed trigger file.

## Technical Context

**Language/Version**: Python 3.11+  
**Dependencies**: Python standard library, systemd, Docker CLI, existing NewAPI internal alert endpoint  
**Storage**: root-owned JSON policy; private atomic report/state JSON; journal logs  
**Testing**: `unittest`, fake command runner/filesystem/clock/HTTP, systemd static checks, bounded production verification  
**Target Platform**: Ubuntu production host, Docker Compose and systemd  
**Constraints**: no external AI dependency, no paid request, no unsafe database/price/credential repair, no public listener

## Architecture

```text
systemd timer / manual / trigger.path
                 |
                 v
       root patrol-repair oneshot
       | checks | policy | state |
       | safe action + post-check|
                 |
        private report + event
                 |
                 v
      NewAPI internal notify endpoint -> configured administrator email

127.0.0.1 control API (unprivileged)
       | GET status / POST trigger
       v
0600 trigger directory -> systemd path unit
```

## Delivery Phases

1. Implement typed domain model, command abstraction, evidence parsing, redaction and policy validation.
2. Implement checks and bounded repair coordinator with persistent incident lifecycle.
3. Reuse the existing notification transport and add a constrained operations-alert event type.
4. Add loopback API, token validation, systemd units and deployment documentation.
5. Back up production configuration, deploy dark, run dry/live patrol, exercise a disposable failure and complete ten read-only rounds.

## Safety Decisions

- No generative AI is in the repair decision path; deterministic policy is auditable and available during upstream API outages.
- Default automatic repair is limited to restarting approved stateless services and regenerating derived monitor data. Data stores, pricing writes, billing evidence, credentials and cleanup remain alert-only.
- A single run executes at most the configured action budget and each action has a persistent cooldown.
- API authentication is defense in depth; loopback binding and an unprivileged service are mandatory independently of the bearer token.

