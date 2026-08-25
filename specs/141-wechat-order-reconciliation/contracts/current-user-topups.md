# Contract: Current-user top-up reconciliation

`GET /api/user/topup/self` keeps its existing response shape and authentication.

Before returning the page:

- select in response order only rows matching current user + pending + wxpay + wechatpay whose QR TTL has elapsed and whose trade number is locally valid;
- query at most five under one five-second deadline;
- SUCCESS must pass existing full transaction validation and idempotent credit;
- CLOSED/REVOKED/PAYERROR may atomically change pending to failed;
- NOTPAY/USERPAYING/unknown and any error preserve local state;
- refresh the in-memory response row only after a successful database transition;
- never fail the list because reconciliation is unavailable.
- clamp internal budgets to the same five-order/five-second production maxima and log only aggregate failure counts.

No new fields, endpoints, secrets, database columns or client-provided identity are introduced.
