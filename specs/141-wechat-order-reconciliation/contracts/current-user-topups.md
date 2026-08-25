# Contract: Current-user top-up reconciliation

`GET /api/user/topup/self` keeps its existing response shape and authentication.

Before returning the page:

- select in response order only rows matching current user + pending + wxpay + wechatpay;
- query at most five under one five-second deadline;
- SUCCESS must pass existing full transaction validation and idempotent credit;
- CLOSED/REVOKED/PAYERROR may atomically change pending to failed;
- NOTPAY/USERPAYING/unknown and any error preserve local state;
- refresh the in-memory response row only after a successful database transition;
- never fail the list because reconciliation is unavailable.

No new fields, endpoints, secrets, database columns or client-provided identity are introduced.
