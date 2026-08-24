# Upstream Compatibility Ledger

**Fork baseline**: `f0fb7d0aa3da56f757fe23b4a2e461403fcf198a`
**Merge base**: `1721144221ec5c94dd87891a7ae1bee228e7bb63`
**Upstream head**: `2d8e50bf36e94200b809dfb39e73624ec48b1e23`
**Range size**: 97 commits

| No. | Commit | Disposition | Area | Decision |
|---:|:---|:---|:---|:---|
| 001 | cb96ab0208bc | reject-with-conflict | GitHub | Issue-form governance is fork-owned and unrelated to runtime. |
| 002 | cbd9b30aa487 | reject-with-conflict | GitHub | Follow-up to rejected upstream issue-form migration. |
| 003 | 84a79b6807ac | defer | Logging | Upstream response-body logging may expose provider data; requires redaction review. |
| 004 | 27235a277a0f | defer | Model pricing UI | Clean patch, but global pricing-map behavior lacks upstream regression coverage. |
| 005 | bf8cfcc51267 | defer | Model pricing UI | Drawer reset fix overlaps the deferred pricing component change. |
| 006 | a0d0e5049e2d | defer | Channel UI | Useful priority-edit stability change needs fork UI regression coverage. |
| 007 | 18b0b7631a99 | defer | Channel UI | Rename/refactor depends on the deferred priority-edit series. |
| 008 | eb4a1bd19332 | defer | JSON editor UI | Large admin editor rewrite is not required by the first batch. |
| 009 | 257223be2675 | reject-with-conflict | Branding | README badge change conflicts with protected project metadata policy. |
| 010 | 5ede832d80d8 | reject-with-conflict | Branding | Translated README badge change conflicts with protected metadata policy. |
| 011 | ae17f2749d16 | defer | JSON editor UI | Cosmetic editor-layer fix needs a reproducible fork UI case. |
| 012 | 8b41defbe0d9 | defer | Model catalog | New GA aliases overlap reviewed stable model mapping policy. |
| 013 | 08f88d25e588 | defer | Provider | New Tencent provider requires business, credential, and protocol validation. |
| 014 | ab65d2582feb | defer | Wallet UI | Top-up input behavior is outside this bounded safety batch. |
| 015 | 3e1e72827988 | defer | Users UI | Search debounce is low priority and lacks measurable fork impact. |
| 016 | 2d23cdf29154 | reject-with-conflict | Billing/providers | Sixty-five-file pricing/provider feature conflicts with custom billing policy. |
| 017 | bc14c18f6024 | reject-with-conflict | Refunds | Refund rewrite overlaps audited custom settlement and refund invariants. |
| 018 | 398cdafecf29 | defer | Provider | New API channel type requires separate protocol and billing acceptance. |
| 019 | f51dd4d808d1 | reject-with-conflict | Advanced routes | Compact route fix depends on upstream channel architecture absent in the fork. |
| 020 | 86ac0f7745cc | reject-with-conflict | RelayKit | 446-file RelayKit extraction is incompatible with the fork architecture. |
| 021 | 60a1acb703a6 | reject-with-conflict | RelayKit | Import update depends on the rejected RelayKit extraction. |
| 022 | b8bb3f40ac9d | reject-with-conflict | RelayKit | Type-package refactor depends on the rejected RelayKit extraction. |
| 023 | 8aa5e754a86b | defer | Middleware | Package rename has no required behavior and broad conflict potential. |
| 024 | 8a7a49072ab0 | reject-with-conflict | Release CI | GitCode publishing is not part of the fork release process. |
| 025 | 2ec6171faa74 | reject-with-conflict | Release CI | Follow-up script fix belongs to the rejected GitCode workflow. |
| 026 | 6d57d250f88e | reject-with-conflict | Release CI | Asset-matrix workflow is not used by the fork. |
| 027 | f3ab2cff36b3 | reject-with-conflict | Release CI | Follow-up to the rejected GitCode release workflow. |
| 028 | a043eef559a9 | reject-with-conflict | RelayKit/Gemini | Stream converter depends on RelayKit and conflicts structurally. |
| 029 | b27b2b1d6f72 | defer | Login UI | iPad session detection needs device-specific reproduction and regression tests. |
| 030 | e99a9bd86fb2 | defer | HTTP transport | Per-channel transport controls touch routing and require separate security review. |
| 031 | 2cf3c8d71e92 | reject-with-conflict | Governance | Upstream AGENTS rules must not replace fork-specific conventions. |
| 032 | f01c13b0863f | defer | CI | Build/test CI is useful but must be reconciled with fork PR governance. |
| 033 | c3db41407dd1 | defer | Database logging | SQL log parameterization requires three-database regression review. |
| 034 | 8e2bfe278b86 | defer | Networking | CustomEvent mutex refactor lacks a fork incident or requirement. |
| 035 | 1db6ae19576d | defer | CI | Go vet expansion depends on reconciling the upstream CI workflow. |
| 036 | afe16c64cd73 | reject-with-conflict | RelayKit docs | Documents a runtime module absent from the fork. |
| 037 | c27d1ef651c6 | reject-with-conflict | Git attributes | Repository attribute policy is fork-owned and unrelated to runtime fixes. |
| 038 | cb4c8c02f81d | defer | OIDC | Custom display-name feature needs auth/UI acceptance and migration review. |
| 039 | 66ee6b8f9889 | reject-with-conflict | RelayKit/Qwen | Thinking passthrough patch depends on absent RelayKit structure. |
| 040 | 0f9f668c6076 | defer | Middleware | zstd request decompression expands attack surface and needs limit tests. |
| 041 | 84834eee859f | defer | Usage logs | Stream-status visibility changes a privacy-facing log contract. |
| 042 | 8461e5339d48 | defer | Provider | Multipart image fix depends on the deferred New API channel type. |
| 043 | e78e1db1e4ed | manual-port | OAuth UI | Selected: positive popup proof prevents foreign opener login hangs; upstream tests ported first. |
| 044 | aa7d0d39a4a7 | defer | Public UI | Navigation font-size style change has no operational value. |
| 045 | 9724ef1b248a | reject-with-conflict | RelayKit/DeepSeek | Responses support depends on absent RelayKit architecture. |
| 046 | df43f801536b | reject-with-conflict | Billing | Tiered retry settlement overlaps custom pricing and group accounting. |
| 047 | cfaba1dd6754 | reject-with-conflict | Billing | Follow-up tiered group-switch accounting overlaps custom billing. |
| 048 | bd585d78efd4 | defer | AWS relay | Cancellation propagation is useful but needs provider-specific integration tests. |
| 049 | 0ab02020603d | already-equivalent | Auto groups | Fork already implements ordered auto groups, default selection, and cross-group retry. |
| 050 | d6b5ce99de49 | reject-with-conflict | Relay | Replay metadata patch targets upstream relay structure not present in fork. |
| 051 | ea4f021012cd | reject-with-conflict | Relay | Refactor depends on the preceding incompatible replay implementation. |
| 052 | 0cd9dc85e334 | reject-with-conflict | Upstream merge | Opaque upstream fork merge is not a reviewable atomic change. |
| 053 | c9bc038649d1 | defer | Channel UI | Model categorization can affect custom stable model catalogs. |
| 054 | b941253aea6b | defer | Channel tests | Native Claude/Gemini test requests require provider-safe test review. |
| 055 | 1da23d6b3342 | defer | Rate limiting | Security-sensitive rate limits need a separate threat and compatibility review. |
| 056 | e926e5cacee2 | defer | Voucher UI | Precision display fix must be checked against fork quota units. |
| 057 | 5c3abffe8572 | reject-with-conflict | Release CI | Extends the unused GitCode synchronization workflow. |
| 058 | 2399de97daf6 | defer | Ali relay | Useful optional `top_p` fix conflicts with fork relay and needs manual porting. |
| 059 | 823e26304a39 | defer | Model catalog | Qwen TTS categorization needs stable-model policy review. |
| 060 | 5d3423bec13f | defer | Channel testing | Auto-disable mode overlaps custom channel-monitor health policy. |
| 061 | 7dd1000a190d | defer | Web performance | Search debounce is noncritical and spans ten UI files. |
| 062 | eab18a835791 | defer | Usage logs | Reasoning-effort logging changes a privacy and accounting surface. |
| 063 | 85feb7a345d2 | defer | Relay policy | User/group parameter overrides expand routing policy and need authorization review. |
| 064 | 8ad159a3bbc2 | reject-with-conflict | RelayKit/Ollama | Reasoning/tool context fix depends on absent RelayKit structure. |
| 065 | d49160f0e543 | defer | Validation | Backend length validation change lacks focused upstream regression tests. |
| 066 | 4cf9107f0437 | defer | Billing logs | Conditional multiplier display overlaps customized price evidence. |
| 067 | 9c97e78aced5 | defer | Token security UI | Confirmation is useful but conflicts in the customized settings UI. |
| 068 | 253a74dd1b47 | reject-with-conflict | RelayKit/Responses | Penalty preservation depends on absent RelayKit architecture. |
| 069 | bb234ff41861 | reject-with-conflict | Responses | Compact suffix refactor spans custom routing and pricing paths. |
| 070 | 4eaeefbdf5b9 | defer | Mobile UI | Sidebar fix is unrelated to relay correctness and lacks a fork reproduction. |
| 071 | ffeb1b24ef85 | defer | Turnstile UI | Clean useful patch, but no low-cost user-behavior regression harness exists yet. |
| 072 | 3d5dc36f1d85 | defer | Gemini routing | Useful model-list fix conflicts with fork middleware/router; manual issue required. |
| 073 | d7992672a606 | defer | OAuth backend | Binding state change is security-sensitive and spans database/auth behavior. |
| 074 | 50e5377ea5fe | reject-with-conflict | Top-up | Atomic recharge rewrite overlaps fork transaction and quota safety work. |
| 075 | ccd535ef8e50 | reject-with-conflict | Quota | Concurrency rewrite overlaps audited custom quota and status helpers. |
| 076 | 58d4e9bd3bb0 | reject-with-conflict | Refunds | Async refund accounting overlaps custom task settlement and refunds. |
| 077 | 15cfdeddef46 | defer | Model UI | Form synchronization conflicts and can affect custom model mappings. |
| 078 | 93d2df85f824 | defer | Ali image relay | Useful mapped-model protocol fix conflicts with fork relay; manual issue required. |
| 079 | 626058075524 | defer | Electron dependencies | Clean builder dependency bump is outside the web-only first batch. |
| 080 | f250f3b589c8 | adopt | Web dependency | Selected package declaration update to DOMPurify 3.4.13; Bun regenerated the matching lock. |
| 081 | 53a8739eedbf | defer | Electron dependencies | Fast-URI lockfile update conflicts with current dependency graph. |
| 082 | e5efc73cdb49 | defer | Electron dependencies | Clean tar update requires an Electron packaging validation batch. |
| 083 | 2a0ce3475c2d | reject-with-conflict | Top-up | Payment eligibility rewrite overlaps custom recharge safety. |
| 084 | cf38105a9946 | defer | Electron dependencies | js-yaml update conflicts with current Electron lockfile. |
| 085 | bbf67df0499c | defer | Electron dependencies | Electron runtime bump conflicts and requires platform packaging tests. |
| 086 | 47ba9d2c63d6 | reject-with-conflict | Top-up | Wallet quota guard overlaps custom transaction and quota rules. |
| 087 | 7d09c6954ef3 | reject-with-conflict | RelayKit/Responses | Prompt-cache conversion fix depends on absent RelayKit architecture. |
| 088 | e90a7c48e5e4 | defer | Gateway UI | Field passthrough controls expand routing policy and need security review. |
| 089 | 4442bb302898 | reject-with-conflict | RelayKit/Claude | Empty-tools fix conflicts because the fork lacks RelayKit. |
| 090 | 116255f076a3 | defer | OAuth UI | Binding response alignment depends on broader upstream auth UI state. |
| 091 | e2c7aa7b102c | defer | Frontend tests | Large Vitest migration is unnecessary for the two selected changes. |
| 092 | 3dda1d50c6d4 | reject-with-conflict | RelayKit/Claude | Parameterless-tool fix depends on absent RelayKit. |
| 093 | 2b0efd8484cc | reject-with-conflict | Advanced routes | Twenty-one-file route editor refactor conflicts with fork routing UI. |
| 094 | 4add708ebe3b | defer | Channel testing | Expanded channel tests overlap custom monitor and provider safety controls. |
| 095 | 137d1171f2b4 | defer | Playground UI | Animation/editor hardening is noncritical and spans thirteen files. |
| 096 | f11641428416 | reject-with-conflict | Responses billing | Cached-token settlement fix conflicts with fork service and custom pricing. |
| 097 | 2d8e50bf36e9 | defer | Usage log security UI | Clean useful autofill fix needs a user-visible regression harness. |

## First Batch

- `manual-port`: `e78e1db1e4ed` with upstream regression tests and explicit call-site integration.
- `adopt`: `f250f3b589c8` for the exact package declaration change plus the required Bun-generated lockfile.
- All remaining entries leave current behavior unchanged in Issue #134.
