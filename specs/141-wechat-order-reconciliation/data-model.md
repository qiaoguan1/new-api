# Data Model: 当前用户微信订单状态同步

无数据库迁移。复用 `model.TopUp`：

| Field | Reconciliation role |
|---|---|
| UserId | 必须等于当前 Gin 用户 |
| PaymentMethod | 必须为 `wxpay` |
| PaymentProvider | 必须为 `wechatpay` |
| Status | 仅 `pending` 进入查询 |
| TradeNo | 作为微信商户订单号；不从客户端请求体获取 |
| Money | SUCCESS 时沿用既有分币金额校验 |

状态转换：pending + SUCCESS → 既有幂等入账 → success；pending + CLOSED/REVOKED/PAYERROR → failed；其他结果保持 pending。非 pending 无转换。
