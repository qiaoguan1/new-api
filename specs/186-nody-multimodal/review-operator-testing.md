<!-- REVIEW:START -->
## Code Review Complete — approved operator testing release

| Property | Value |
|----------|-------|
| Worker | /root |
| Issue | #186 / PR188 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-08 |

### Criteria results

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Blindspots | FIXED | SDK-only candidates; complete tuple/metadata/provenance; bounded held amount; no uncertain task replay |
| Clarity | PASS | Separate unverified estimates from accepted profiles and real upstream bills |
| Maintainability | PASS | Optional policy defaults off; original seven text and six Grok image behavior preserved |
| Security | FIXED | Authentication/ACLs unchanged; frozen private quote identity; source/private field filtering; actual bill requirement; input/decoder checks retained |
| Performance | FIXED | Media validation75s request/client budget vs shared60s verifier; ordinary calls10s; bounded release/recovery operations |
| Documentation | FIXED | Explicit estimates, possible supplements, user-run validation and no agent-paid generation |
| Style | PASS | Go common JSON helpers, require/assert, no schema changes, relevant tests/vet/build/diff checks |

### Fixed findings

1. Full activation lacked successful billing profiles: use an explicitly approved, separate operator estimate policy; no fake success status or UUID.
2. Quote provenance could be mislabeled verified or re-priced on restart: bind complete quote/request and store its private envelope; strip before forwarding; preserve frozen replay.
3. New estimate supplements could overdraw wallets: require available user/limited-token funds transactionally for estimated rows only; preserve real receipt/result pending funding. Existing legacy debt contract unchanged.
4. Candidate input errors could quarantine verified routes: partition only explicit operator400/422 validation failures; retain quota/auth/capacity/infrastructure and uncertain-settlement protections.
5. Capability fields still appeared text-only: candidate AV/image flags and limits union with actual profile lists, clearly labeled unverified; exact lists/prices are not fabricated.
6. Omni marketplace lost its video-only4–30s range: explicitly validate/project those two fields without widening text durations.
7. A10s frontdoor budget truncated legitimate60s verification: only media validation/asset submissions use75s budget and a copied client; ordinary calls remain10s.
8. Broader video action bills could remain unmatched: documented frame/reference/remix actions require platform48, known Nody video model and v1/v2 video path, plus exact task identity and authenticated successful bill.
9. Inert release builds could attest mutated copied source: verify reviewed binary, complete gateway and catalog SHA digests before build.
10. Two gateways could use different upstream accounts: freely verify active production key/group and identical key/rate/session bindings before policy preparation.
11. Saved entrypoint could ignore the copied binary: assert actual public entrypoint, preserve old environment/network/data; new operation uses immutable IDs and current-data rollback only.
12. Free canary could duplicate live reconciliation: use schema-only isolated PostgreSQL and empty gateway state, ordinary-role fixture keys; route allowlist has no generation endpoint.

### Verification boundary

Local complete gateway regression, Go public/model suites, vet, Linuxamd64 build
and diff checks pass. Independent code/security and operations reviewers found no
remaining blocking code issue after these fixes. Production promotion has not yet
been performed at this review checkpoint. The isolated free canary must prove
zero quota/task/log changes before promotion; it makes no paid upstream calls.

User A authorized estimated holds followed by authenticated actual cost×1.5
settlement, including refund/supplement. Held amounts are cappedCNY150, not a
guaranteed final-price maximum. No recharge or agent-paid generation is authorized.

**Unaddressed: 0**
**Review Status: COMPLETE**
<!-- REVIEW:END -->
