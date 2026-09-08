# Verification — issue 154

- Database transaction completed with preconditions and a postcondition rejecting any core model with more than two enabled channels.
- Database backup: `/opt/ai-api-stack/backups/issue154-two-channel-20260908-143945`.
- Core text routes: Haina and Hanhe.
- GPT-Image-2 routes: Haina and Maolao.
- Banana route: Paisio.
- Code Plan text/image, Paisio GPT-Image-2, and Hanhe image are disabled but retained.
- Haina GPT-6 direct canary: 3/3 HTTP 200.
- Relay text canaries: GPT-5.5, Sol, Terra, Luna, and GPT-6 all HTTP 200.
- GPT-Image-2 relay canary: HTTP 200 in 26 seconds through channel 37.
- Daily audit: 7 enabled, 7 OK, 0 failed.
- Patrol: 19 healthy, 0 warning, 0 failed.
- Automatic-pricing dry run preserves protected text/image prices while Haina actual-cost evidence is incomplete.
