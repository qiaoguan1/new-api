<!-- REVIEW:START -->
## Code Review Complete

| Property | Value |
|----------|-------|
| Worker | `model_callability_fix_191` |
| Issue | #191; parent #190 |
| Scope | MAJOR |
| Security-Sensitive | YES |
| Reviewed | 2026-10-10, Asia/Shanghai |
| Review type | Worker self-review; independent release review remains required |

### Criteria Results

| # | Criterion | Status | Findings |
|---|-----------|--------|----------|
| 1 | Blindspots | FIXED | Legacy status and returned-task boundaries covered |
| 2 | Clarity | PASS | Explicit submitted/not-submitted/uncertain distinctions |
| 3 | Maintainability | PASS | Original production adapter baselines captured; scoped patches |
| 4 | Security | FIXED | No transport/5xx generation replay; no raw provider message exposure |
| 5 | Performance | FIXED | Error response sockets closed; existing concurrency/deadlines retained |
| 6 | Documentation | FIXED | Configured/verified/intent-only image capabilities distinguished |
| 7 | Style | PASS | Existing adapter style retained; new safety functions documented |

### Findings Fixed in This PR

| # | Severity | Finding | Resolution |
|---|----------|---------|------------|
| 1 | Major | Explicit HTTP402 quota failure cannot use a safe image backup | Only the three existing quota markers qualify; unrelated statuses/messages do not |
| 2 | Major | Nody and legacy Image2.5 post-submission failure masquerades as HTTP400 | HTTP502/uncertain for timeout, unknown HTTP response, no result or transfer failure |
| 3 | Major | Native legacy adapter error code can be treated as a definite rejection | Explicit result-error code classification precedes generic HTTP rejection logic |
| 4 | Minor | Malformed upstream image URL escapes adapter failure handling | Bounded `invalid_result` HTTP502/uncertain |
| 5 | Major | Banana guesses fast generic 5xx capacity failure is safe to replay | Only exact fast 4xx admission rejections qualify; returned task identity blocks replay |
| 6 | Major | Banana error-body timeout escapes typed uncertainty and leaks response handle | Typed non-replayable failure and unconditional HTTP error handle close |
| 7 | Minor | Generic discovery obscures exact route and prompt-only dimensions | Per-provider configured image whitelist and honest Banana canvas intent metadata |
| 8 | Major | Adapter-local busy before any POST is represented as unknown provider failure | Local HTTP429/no-task evidence; safe backup only before any provider call |
| 9 | Major | Generic image403 can repeat an admitted policy failure or bypass an ACL-coded refusal | Unknown post-submit403 uncertain; explicit no-task only; caller ACL and known fee codes protected |
| 10 | Major | Native uncertainty code retained misleading legacy4xx HTTP status | Submitted unknown4xx returns502; original error retained as inner evidence; ACL/fee/pre-submit failure unchanged |
| 11 | Major | Quota/no-task text could override explicit task ID in native error metadata | Typed task/job metadata root/data/task checked first; ordinary request ID/UUID never guessed |

### Security review

- Bearer checks and secret-file protections are unchanged. No production key,
  API token, raw error detail or user prompt appears in the new source/tests/docs.
- Unsupported editing, sizes, quality, model names and image counts remain
  rejected; no model substitution or forced resize was introduced.
- Safe fallback excludes unknown/transport/5xx outcomes, skip-retry and violation
  fees; a returned upstream task identifier always wins over a quota-like marker.
- Successful result failures remain potentially chargeable/uncertain instead of
  silently becoming free failed tasks. No historical task is replayed or mutated.
- All new tests mock upstream submission. This worker created no paid task,
  balance write, tariff change, user/key permission change or deployment.
- Production native marketplace source differs from current Git HEAD and must
  not be blanket-overwritten. Byte-equivalent native image-route policy and Nody
  source are appropriate scoped backport targets only after fresh identities.

### Verification

- Nody image adapter: 11 tests pass.
- Banana chat-image adapter: 9 tests pass.
- Legacy Image2.5 adapter: 6 tests pass.
- Existing image-job gateway: 9 tests pass, unmodified.
- Full `go test ./service -count=1` and `go vet ./service` pass.
- Controller selected image/retry command compiles, with no matching tests;
  this is not presented as controller behavior coverage.
- RED-before-GREEN records are summarized in `image-callability.md`.
- `git diff --check` passes for changed code.

### Findings Deferred (With Tracking Issues)

None from this self-review. Production dependency hashes, ordinary-user discovery,
bounded actual generation tests, source backport and deployment verification are
existing #190/#191 release acceptance gates, not claimed completed by this worker.

### Summary

| Category | Count |
|----------|-------|
| Fixed in PR | 11 |
| Deferred (with tracking) | 0 |
| Unaddressed | 0 |

**Review Status:** COMPLETE — worker self-review only; not production acceptance.
<!-- REVIEW:END -->
