# 2026-10-12 verification results

The outcome is partial. No production route or sale price was changed. No
recharge, original uncertain task replay or customer/key mutation occurred.

| Exact model | Provider/path | New evidence | Decision |
|---|---|---|---|
| gpt-image-2.5 | Code Plan Chat | Complete1254x1254 PNG; requested1024x1024;229.041s | Size mismatch; do not switch |
| gpt-image-2.5-flare | Code Plan Chat | Complete1254x1254 PNG;82.786s | Size mismatch; do not switch |
| gpt-image-2.5-sunburst | Code Plan Chat | Complete1254x1254 PNG;21.898s | Size mismatch; do not switch |
| gpt-image-2.5-flare | Existing Nody production image adapter | Complete1024x1024 PNG;23.948s | One new adapter/supplier output passed; ordinary native chain not newly tested |
| gpt-image-2.5-sunburst | Existing Nody production image adapter | Complete1024x1024 PNG;19.572s | Same limited proof |
| claude-fable-5-1 | Direct Nody Chat candidate | Exact returned model; nonempty text;15/4tokens;6.443s | Feasible backup candidate, not enabled automatically |

## Money and route constraints

- New user limit remains CNY10 total. CNY6.90 earlier exposure is retained,
  plus Code Plan candidates0.40 and Nody allocations0.30/0.30/2.10, reaching
  conservative exposure10.00. Actual supplier invoice attribution is not
  established from returned IDs; no additional paid job may be created.
- Nody two actual CNY recharge orders and matching arrival logs prove
  1display credit=1.5CNY. Its two image SKUs each quote0.30CNY per1K request;
  exact Fable base input/output quote36/180CNY per million tokens.
- Native completed CNY WeChat orders and current Price1 establish one native
  wallet pricing unit=1CNY. Fable standard actual revenue is20.085/100.425CNY
  per million tokens, below Nody36/180. A higher Max tariff does not cure
  standard-route loss. Latest user wants Nody as a candidate backup and cost
  comparison; no permission to silently change this sale price or activate
  a loss-making fallback is inferred.
- Rolldek exact Fable group quotes13/65 original credits per million tokens,
  but no cash recharge conversion is available (voucher arrival only). Its
  confirmed negative account balance needs funds before a true test.
- Maolao Fable channel72 has now been uniquely bound to its corresponding
  authenticated account/key group, with current balance0.046186 display
  credits. Standard original-unit input/output costs13/65 per million tokens;
  Max effort multiplies by3. An arrival log says100 credits/payment103 but
  does not state currency, so1.03CNY/credit remains supported inference rather
  than a currency-confirmed receipt. Normal use requires added funds.
- Initial recharge preference is Maolao based on prior usage and the reference
  lower tariff, not a proven cheapest-CNY ranking. Rolldek's confirmed negative
  balance also requires funds; both remain cost candidates despite low balance.

## Remaining work

The generic2.5 serving metadata already declares `/v1/images/generations` only.
Chat400 is a pre-submit endpoint rejection, not absent registration. Correct
callers must use the declared image endpoint. Unsupported editing, references,
sizes and quality cannot be relabeled verified.

The request-ID source repair has27 passing adapter mock tests and independent
review, but is not serving. Existing native retry safety promotion remains a
separate release requiring fresh maintenance/dependency evidence; this task
does not blanket-restart the old non-graceful native service. A few canaries
do not justify a permanent all-users stable label.
