# Issue 191: image route and submission safety

## Verified boundaries

- `gpt-image-2.5-flare` / `gpt-image-2.5-sunburst` via the Nody adapter are
  independently verified only for JSON `POST /v1/images/generations`,
  `1024x1024`, `n=1`, default/auto quality and no reference image.
- An image-edit or another resolution rejection is not proof that an image
  model is wholly unavailable. It must not be silently converted to generation,
  resized or substituted with another model.
- Production Nody adapter baseline is byte-identical to Git HEAD:
  7,595 bytes, SHA256
  `6688c4d11499c691d075127e1c7f3c2e832b4365a342cbfe6ded78586b15e272`.
- Banana and legacy Image2.5 adapters were not tracked at the start of repair;
  root authorized capturing their key-free production source and adding the
  modules for auditable fixes. Neither names nor catalog presence proves a valid
  request. Banana baseline is 24,936 bytes / SHA256
  `0d84877745a9388f728ed7bca297f80d57881ecfe9e396e34fb8ce1eadd4687d`;
  Image2.5 baseline is 7,639 bytes / SHA256
  `e99d0a57a17656bcf6ea38581c7fa3709329c1e75fc659be2f44abb0deb77e5c`.
- Native image-route policy baseline is byte-identical to Git HEAD:
  6,584 bytes / SHA256
  `73f4cb93890457d09c6228cd0a3687671a9e66e532a2144b9d97c43994cecfa9`.
- Native `model/pricing.go` is not byte-equivalent to current Git HEAD. Production
  replaces default endpoint inference with valid model endpoint metadata;
  current HEAD merges newer advanced-custom inference. Do not deploy that entire
  unrelated source difference for this repair.
- No paid tests, SSH actions, deployment, balance or tariff writes are performed
  by this worker. The parent alone coordinates the total CNY20 testing budget.

## Defects proven by RED tests

1. Native image fallback rejected all HTTP402 responses even when the provider
   explicitly reported a pre-generation quota failure. Its cooldown recorder
   already recognized HTTP402, making the two policies inconsistent.
2. The Nody adapter returned HTTP400 for transport failure, an unreadable or
   invalid upstream body, no image, malformed image data and failed result
   transfer after the generation POST. A durable gateway could therefore call a
   possibly charged generation a definite failure.
3. Native normalization did not recognize the adapter's legacy unconfirmed
   result codes at HTTP400. Its generic authorization rejection branch could
   even permit fallback when such an error happened to carry HTTP403.
4. Malformed upstream URLs such as `https://[` escaped the response validator
   instead of returning a bounded, non-replayable result error.
5. Banana's fast rejection classifier refused HTTP402/429 explicit quota errors
   yet accepted fast generic HTTP500/502/503 capacity messages as safe failover.
   Speed is not proof that an upstream generation was never created.
6. Banana error-body timeout escaped its typed non-replayable exception path;
   error-response sockets were not closed by that path.

## Scoped changes

- Admit HTTP402 for only the existing explicit quota markers:
  `no available image quota`, `insufficient quota`, `insufficient balance`.
  Generic payment, pending payment and parameter errors do not qualify.
- Preserve no-retry and `violation_fee.*` exclusions, transport/5xx protections,
  model/endpoint cooldown scope, caller authentication and image identity.
- Generic upstream HTTP403 now requires explicit no-task proof before fallback.
  After submission starts, unknown policy/permission403 is non-replayable and
  uncertain. Caller `access_denied` cannot be turned into route fallback by a
  message marker; confirmed violation-fee codes remain intact for settlement.
- Submitted unknown legacy4xx results now use public HTTP502 rather than a
  misleading parameter/permission status. The original typed status, code and
  metadata remain in the inner error chain. Confirmed pre-submit failures,
  caller ACL and violation-charge evidence retain original status.
- Explicit task/job identifiers in native error metadata (root, data or task)
  override quota/no-task text. Generic IDs, UUIDs and request-correlation IDs are
  deliberately not inferred as task identifiers.
- Recognize explicit unconfirmed result codes independently of legacy status;
  after native submission starts, normalize them to `image_submit_uncertain`
  with skip-retry and `X-XingTu-Image-Submission-State: uncertain`.
- Nody preflight endpoint/size/quality/count rejection carries its existing
  exact-safe marker and `not_submitted` state. Unsupported modes remain rejected.
- All Nody post-submission outcome or transfer failures are HTTP502/uncertain;
  no automatic second POST is added. Upstream HTTP error handles are closed.
- The adapter's authenticated model catalog adds exact `image_capabilities`
  metadata. This is internal adapter discovery, not a claim that native/public
  model-marketplace consumers already project those fields.
- Legacy Image2.5 applies the same result-error classification and preserves its
  configured three sizes, while the Rolldek route remains 1K-only. Its metadata
  is `configured`, not a fabricated new-generation success or editing proof.
- Banana retains original model and route bindings and the original fast
  rejection deadline. Only definite 4xx quota/no-channel/model-not-found errors
  qualify; generic 5xx, overload, payment and permission messages never qualify.
  Any returned task identifier prohibits fallback. Exhausted definite routes
  expose sanitized `upstream_rejected_no_task`; HTTP error bodies are closed.
- Adapter-local concurrency rejection happens before any upstream POST. It now
  carries explicit no-task evidence, instead of an ambiguous local HTTP503.
- Banana canvas size is currently appended as prompt intent, not an enforced
  provider pixel parameter. Its discovery metadata explicitly says prompt-only,
  unverified output dimensions, no editing or references.

## Validation checkpoint

- Nody adapter: 14 deterministic tests pass. Real handler tests use a local
  fixture server and mocked upstream only; zero generation charges.
- Existing image-job gateway: 9 tests pass, including timeout/no-replay audits.
- Banana adapter: 9 deterministic tests pass; legacy Image2.5 adapter: 6 pass.
  All upstream calls are mocked and no live key is present.
- Full native service suite `go test ./service -count=1` and `go vet ./service`
  pass. Controller builds via selected image/retry test command, but that filter
  matches no current controller tests and is not claimed as behavior coverage.
- RED evidence: HTTP402 tests failed before the policy correction; Nody new
  handler tests failed with legacy HTTP400/missing capability; explicit legacy
  unconfirmed-code test failed before normalization; malformed URL test failed
  before URL-error handling.

## Remaining release gates

Fresh production identities, independent code/security review, full impacted
suites and free production
verification are owned by the coordinated release. Passing these tests does not
claim that all public models or unsupported Image2.5 modes generated successfully.
Root is still correlating the actual recent Banana upstream HTTP rejection and
bounded live tests. Source-level fixes alone do not prove upstream funded model
availability. Image2.5/Nody runtime `image_io.py` dependencies were independently
verified at 3,333 bytes / SHA256
`7834a058614143542dd979fb57fa83d06676a4ed53beff9449c48f45381196d1`,
byte-identical to tracked Nody helper. The same unchanged helper is now tracked
beside legacy Image2.5, so standalone imports use the current runtime helper.
Production helper remains unmodified.

## Production checkpoint: 2026-10-10

Three adapter replacements and the exact Image2.5 endpoint metadata correction
are live. Current native image-policy changes are built and tested against a copy
of the exact serving source, but are NOT promoted. The serving legacy main has
no graceful shutdown handler, and its native relay surface is wider than the
image/text admission gate. A separately approved complete maintenance plan is
required before replacing that process; the partial adapter release does not
claim the native policy is already effective.

One operator-owned Flare request delivered a validated 1024x1024 PNG. Its bounded
quote is CNY0.30, but the authenticated provider ledger request ID differs from
the returned request header and no exact billing association is present. Actual
attributable cost remains unknown. No second paid POST was made, and the
temporary operator token was closed. Neither time/model similarity nor a balance
delta is used as an exact receipt. This successful response does not prove
Sunburst, image editing, references or other resolutions.
