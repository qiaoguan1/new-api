# Production verification — 2026-09-19 Beijing time

- Server root mode 755, load below 1, disk 27%; API status HTTP200.
- Routing snapshot and rollback: `/opt/ai-api-stack/backups/reliability-20260919-183513`.
- Before: 142 successful Sol logs attempted Jojo→Haina→Hanhe; seven image requests attempted Jojo→Haina→Maolao.
- After: operator smoke logs 66169–66175 attempted exactly one channel each. Dedicated test credentials 231–234 were revoked.
- Minimal text completions: GPT5.5 2.05s, Sol 2.09s, Terra 3.14s, GPT6 alias 1.54s.
- Banana b64_json actual image PASS 19.52s; GPT-Image-2 b64_json PASS 37.94s. No returned image URLs, bodies, passwords or keys are retained in this report.
- Hanhe Luna returned502 twice (~23s), failed attempt quota0; its model ability was isolated.
- User recharged Haina. Direct post-recharge tests: Sol2.00s, Terra1.84s, Luna1.70s, GPT6 Astra2.66s, Image2 42.96s all PASS. Haina text/image restored at lower priority than funded primaries. This confirms service resumption, not the exact recharge amount: account billing login still has session-limit errors.
- Public relay retest of Luna after restoration: HTTP200,2.51s, succeeded through Haina; test token235 revoked.
- Final route policy: GPT5.5 CodePlan; Sol/Terra Hanhe→Haina; Luna Haina; GPT6/Astra CodePlan→Haina; Image2 Maolao→Haina. Jojo text/image stay paused until recharge and verification.
- Patrol missing boot directory fixed with tmpfiles rule. API/path units active, last patrol exit0 and21/21 healthy.
- Adapter image `xtai/banana-chat-adapter:issue163-b64`, healthy. Rollback compose/source `/opt/ai-api-stack/backups/banana-format-20260919-184127`.
-21 adapter tests: format acceptance, conversion, inline image, pinned DNS/TLS, private DNS rejection, redirects/oversize rejection, existing fallback rules.
-6 logout tests: modern SID-scoped logout, success and failure cleanup, recharge cleanup, no logout on failed login, sanitized exception path.
- Logout fix backup: `/opt/ai-api-stack/backups/collector-logout-20260919-163`.
- External unsupported-model requests accompanied disposable-email registration activity. Existing rate limiting returned429; no customer account was deleted or disabled, and unsupported models were not enabled.

## Pending external action
Jojo account login returns409 AUTH_SESSION_LIMIT. The former collector discarded session cookies when its process exited, so no usable Jojo session was found in the service's credential/session directories. Owner must revoke other sessions from an already logged-in device or use account recovery. The new collector finally block prevents new successful classic logins from being left active on normal and error exits; a network failure during logout remains detectable in stderr.
