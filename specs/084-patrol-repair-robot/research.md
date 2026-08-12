# Research: Daily Patrol and Safe Self-Healing Robot

## Existing capabilities to reuse

- The upstream balance monitor already validates an internal-only NewAPI URL, reads a token from a file and sends structured events through a RootAuth endpoint. The robot will reuse this transport rather than storing SMTP secrets.
- Daily upstream collection, audit, official video pricing, generic pricing, balance monitoring and backup already run independently. Patrol evaluates freshness and outcomes; it does not merge their responsibilities.
- Video gateway state is durable SQLite. Patrol uses bounded read-only aggregate queries and never writes settlement evidence.

## Decision

A deterministic policy engine is safer and more reliable than an external AI agent for production repair. Unknown faults are summarized with evidence and escalated to the administrator. Adding a future diagnostic model may improve summaries, but it must never authorize repair actions.

