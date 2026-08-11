# 星途AI视频接口 v2.1 下游反馈与交付清单

**合同版本**：`xtai-video-billing-v2.1`

## 一、给下游的正式回复

星途中转站已按双方确认方向升级视频接口。新任务统一使用合同版本
`xtai-video-billing-v2.1`，继续支持带声视频，执行统一模型名、官方价1.5倍预扣、
可信上游实际扣费1.5倍结算、多退少补、硬额度、幂等提交、查询和Webhook回调。

其中音轨规则以本次最终确认为准：

- `generate_audio=true`：生成带声视频，中转站必须全链路保持为 `true`，只路由到支持音轨的上游；
- `generate_audio=false`：生成无声视频；
- 该字段必须明确传布尔值，不能省略、传 `null` 或字符串；
- 中转站不得拒绝带声请求，也不得把带声请求静默降级成无声。

## 二、下游必须提供给中转站的资料

上线前请下游提供以下资料：

1. 生产环境 HTTPS Webhook 地址。当前登记地址为：
   `https://ds.aixingtuyun.com/auth/api/webhooks/video`，请书面确认继续使用。
2. 独立的测试环境 HTTPS Webhook 地址，不能与生产地址共用。
3. 接收测试、生产 API Token 和 Webhook Secret 的安全交付渠道。密钥不得放在聊天、普通邮件、
   工单正文或代码仓库中。
4. 如下游设置IP白名单，请提供白名单要求；中转站随后反馈正式回调出口IP或网段。
5. 下游技术联系人、电话和故障通知方式。
6. 下游长期保存视频所用的私有对象存储方案，以及下载完成、文件校验和失败重试规则。

## 三、下游请求时必须发送什么

创建任务：

```http
POST /v1/videos
Authorization: Bearer <XINGTU_VIDEO_API_TOKEN>
X-XingTu-Contract-Version: xtai-video-billing-v2.1
Idempotency-Key: <稳定且唯一的request_id>
Content-Type: application/json
```

最小请求字段：

```json
{
  "provider_id": "video-aixingtu-api",
  "request_id": "req_xxx",
  "model": "seedance-2.0",
  "resolution": "720p",
  "duration": 4,
  "aspect_ratio": "16:9",
  "generate_audio": true,
  "prompt": "视频提示词"
}
```

下游必须遵守：

- 请求头 `Idempotency-Key` 与 JSON `request_id` 完全一致；
- 网络超时、5xx或进程重启后，使用原请求内容和原 `request_id` 重试，不创建新ID；
- 只发送统一模型名，不发送渠道名、上游模型名、上游任务ID或价格；
- 解压后的 JSON 请求体不超过256 KiB；图片、音频、视频素材使用可鉴权HTTPS URL；
- `generate_audio`明确传 `true` 或 `false`；
- 查询任务和下载代理结果时继续携带相同合同版本头与认证Token。

## 四、中转站会返回给下游什么

中转站返回三类相互独立的状态：

- `status`：生成状态，如 `queued`、`running`、`succeeded`、`failed`；
- `billing.status`：计费状态，如 `reserved`、`settlement_pending`、`settled`、
  `payment_required`、`refunded`、`pending_review`；
- `result_delivery`：交付状态，如 `pending_settlement`、`ready`、`unavailable`。

计费字段包括：

- `billing.reserved_amount`：方舟官方基准价乘1.5后的预扣金额；
- `billing.charged_amount`：可信上游实际扣费乘1.5后的最终用户收费；
- `billing.refund_amount`：多退金额；
- `billing.supplement_amount`：少补金额；
- `billing.currency`：固定 `CNY`；
- `billing.markup`：固定字符串 `1.5`；
- `usage`：上游存在可信Token数据时返回；按次、按秒且无Token时可以为空，计费仍以金额字段为准。

所有公开金额均为六位小数字符串。中转站不会向下游返回上游渠道名称、上游任务ID、
真实成本、利润或内部路由顺序。

只有同时满足以下条件，下游才能交付视频：

```text
status == succeeded
result_delivery == ready
billing.status == settled
result.url 非空
```

## 五、Webhook与查询怎么配合

Webhook用于及时通知，查询接口是最终权威状态。成功任务通常先收到
`video.task.succeeded`，真实扣费核对完成后再收到 `video.billing.settled`。

下游必须：

1. 使用原始请求体按 `timestamp + "." + raw_body` 验证HMAC-SHA256签名；
2. 校验时间戳窗口不超过5分钟；
3. 对 `event_id`建立唯一索引，重复回调只返回2xx，不重复记账；
4. 先持久化事件再快速返回2xx，不用3xx跳转；
5. 收到回调后调用查询接口核对权威状态；
6. 即使回调暂时未到，也每1至2分钟低频查询一次，不要高频秒级轮询。

## 六、余额不足和充值后的处理

- 所有用户钱包、订阅和有限额度Token均不允许透支。
- 预扣不足时任务不会创建，也不会调用上游。
- 预扣不足返回 `insufficient_user_quota`；充值后可用原请求内容和原 `request_id`重试。
- 成功后需要补扣但余额不足时，返回 `billing.status=payment_required`，结果不可交付。
- 用户充值后，下游继续查询原 `task_id`，并按原 `request_id`继续该任务；不得新建任务。
- 旧记录中的 `settled_with_debt`也按 `payment_required`处理，不直接交付。

## 七、中转站仍需安全交付给下游的资料

以下值需要在联调前通过安全渠道单独交付，不能写入本文档：

1. 测试和生产 API Base URL；
2. 两套互相独立的 API Token；
3. 两套互相独立的 Webhook Secret；
4. 回调出口IP或网段；
5. 限流额度与 `Retry-After`规则；
6. Token和Webhook Secret轮换、吊销流程；
7. 测试用模型、金额和失败场景清单；
8. 价格版本、正式生效时间和后续价格变更通知机制。

## 八、当前待下游确认项

- [ ] 确认生产Webhook地址继续使用登记地址；
- [ ] 提供独立测试Webhook地址；
- [ ] 确认密钥安全交付渠道；
- [ ] 确认是否需要回调IP白名单；
- [ ] 完成原始请求体验签、事件幂等和1至2分钟兜底查询；
- [ ] 完成私有对象存储转存与文件校验；
- [ ] 按 `generate_audio=true`完成带声任务联调；
- [ ] 按 `payment_required`完成充值后原任务继续结算联调。

完整字段、错误码、Webhook消息体与验收步骤，以
[`xingtu-video-api-v2.1.md`](./xingtu-video-api-v2.1.md) 为准。
