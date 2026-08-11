# 星途AI软件—星途中转站视频API对接与部署协议 v2

**合同版本**：`xtai-video-billing-v2`  
**时区**：北京时间 `Asia/Shanghai`（UTC+8）  
**金额**：人民币 CNY，跨系统一律使用六位小数字符串  
**接口模式**：提交异步任务后轮询；当前不使用回调/Webhook

本文档是星途AI软件（下游）唯一需要实现的视频对接合同。下游不接触 Paisio、Toonflow
或其他上游，不选择渠道，不读取上游模型广场，也不接收上游真实成本。

## 1. 计费规则

1. 提交时按版本化方舟官方请求成本乘 `1.5` 预扣。
2. 中转站在能力等价的上游候选中内部选路；上游名称和价格单位不进入公共协议。
3. 任务成功后，中转站按上游任务ID取得可信的单任务真实净扣费证据。
4. 最终用户收费等于上游真实净扣费乘 `1.5`。
5. 最终收费低于预扣时退差额；高于预扣时补扣差额。
6. 暂时没有可信证据时保持预扣并返回 `settlement_pending`，不得猜价或按0元结算。
7. 结果地址只在最终结算后交付。

公式：

```text
reserved_amount = 方舟官方请求成本 × 1.5
charged_amount = 上游可信单任务真实净扣费 × 1.5
refund_amount = max(reserved_amount - charged_amount, 0)
supplement_amount = max(charged_amount - reserved_amount, 0)
```

当前4秒预扣示例：

| 下游模型 | 分辨率 | 预扣金额 |
|---|---:|---:|
| `seedance-2.0` | 480p | `2.651670` |
| `seedance-2.0` | 720p | `5.961600` |
| `seedance-2.0` | 1080p | `14.871600` |
| `seedance-2.0-fast` | 480p | `2.132865` |
| `seedance-2.0-fast` | 720p | `4.795200` |
| `seedance-2.0-mini` | 480p | `1.325835` |
| `seedance-2.0-mini` | 720p | `2.980800` |

预扣表有版本号；下游不得把示例金额写成自己的计费公式，应以接口返回的
`billing.reserved_amount`为准。

## 2. 下游部署前需要准备

中转站管理员提供：

- API基础地址，例如 `https://api.example.com`；
- 一个独立的下游API令牌；
- 可用模型和分辨率清单；
- 测试环境和生产环境各自的令牌，二者不得混用。

下游必须配置：

```text
XINGTU_VIDEO_BASE_URL=https://api.example.com
XINGTU_VIDEO_API_TOKEN=sk-...
XINGTU_VIDEO_CONTRACT_VERSION=xtai-video-billing-v2
XINGTU_VIDEO_PROVIDER_ID=video-aixingtu-api
```

API令牌只能保存在下游服务端密钥配置中，不能放进浏览器、客户端安装包、日志、截图或
源码仓库。

## 3. 下游必须生成和保存的数据

每次用户点击生成时，下游先生成一个稳定请求ID，例如 UUID 或：

```text
req_20260811_000001
```

格式要求：8至128个小写ASCII字符，只允许小写字母、数字、点、下划线、冒号和短横线，
第一个字符必须是小写字母或数字。

下游数据库至少保存：

| 字段 | 用途 |
|---|---|
| `request_id` | 幂等恢复；一次用户生成从开始到结束永远不变 |
| `task_id` | 中转站返回的公开任务ID |
| 原始请求JSON | 判断恢复时是否仍为同一个请求 |
| `status` | 视频生成状态 |
| `billing_status` | 计费状态 |
| `reserved_amount` | 预扣金额 |
| `charged_amount` | 最终用户收费 |
| `refund_amount` | 退款金额 |
| `supplement_amount` | 补扣金额 |
| `result_delivery` | 是否允许读取结果 |
| `result_url` | 最终短期视频地址 |
| `updated_at` | 下游最近一次查询时间 |

HTTP超时、断网、5xx或下游进程重启后，必须继续使用原 `request_id` 和原请求内容。禁止
为了“重试”生成新ID，否则可能产生第二个付费视频任务。

## 4. 创建视频任务

```http
POST /v1/videos
Authorization: Bearer <XINGTU_VIDEO_API_TOKEN>
X-XingTu-Contract-Version: xtai-video-billing-v2
Idempotency-Key: req_20260811_000001
Content-Type: application/json
```

```json
{
  "provider_id": "video-aixingtu-api",
  "request_id": "req_20260811_000001",
  "model": "seedance-2.0",
  "resolution": "720p",
  "duration": 4,
  "aspect_ratio": "16:9",
  "generate_audio": true,
  "prompt": "一只小狗在草地上奔跑，电影镜头"
}
```

字段规则：

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `provider_id` | 是 | string | 固定为 `video-aixingtu-api`，不是上游渠道名 |
| `request_id` | 是 | string | 必须与请求头 `Idempotency-Key` 完全相同 |
| `model` | 是 | string | 统一模型：`seedance-2.0`、`seedance-2.0-fast`或`seedance-2.0-mini` |
| `resolution` | 是 | string | 模型支持的 `480p`、`720p`或`1080p` |
| `duration` | 是 | integer | 视频秒数，必须大于0 |
| `aspect_ratio` | 是 | string | 例如 `16:9`、`9:16`、`1:1` |
| `generate_audio` | 是 | boolean | 必须明确传 `true` 或 `false`，不能省略或传 `null` |
| `prompt` | 条件必填 | string | 文生视频必填；图生视频也建议填写 |
| `image` | 否 | string | 单张参考图URL或适配器支持的图片值 |
| `images` | 否 | array | 多张参考图 |

下游不能提交：渠道ID、Paisio/Toonflow名称、上游原始模型名、上游任务ID、上游账号或价格。

### 创建成功

```json
{
  "id": "task_public_xxx",
  "request_id": "req_20260811_000001",
  "object": "video",
  "model": "seedance-2.0",
  "status": "queued",
  "progress": 0,
  "created_at": 1786400000,
  "result": null,
  "result_delivery": "unavailable",
  "billing": {
    "contract_version": "xtai-video-billing-v2",
    "status": "reserved",
    "currency": "CNY",
    "reserve_basis": "ark_official_1_5",
    "reserved_amount": "5.961600",
    "charged_amount": null,
    "refund_amount": null,
    "supplement_amount": null,
    "markup": "1.5",
    "pricing_revision": "official-fallback-2026-08-09.1"
  }
}
```

下游收到响应后立即保存 `id` 为 `task_id`。金额字段按字符串保存，不要先转成 JavaScript
浮点数再写回数据库。

## 5. 查询任务

```http
GET /v1/videos/task_public_xxx
Authorization: Bearer <XINGTU_VIDEO_API_TOKEN>
X-XingTu-Contract-Version: xtai-video-billing-v2
```

建议正常生成阶段每2至5秒查询一次。下游页面关闭不代表任务取消；重新打开后根据数据库中
的 `task_id` 继续查询。

### 正在生成

```json
{
  "id": "task_public_xxx",
  "request_id": "req_20260811_000001",
  "object": "video",
  "model": "seedance-2.0",
  "status": "running",
  "progress": 60,
  "created_at": 1786400000,
  "result": null,
  "result_delivery": "unavailable",
  "billing": {
    "contract_version": "xtai-video-billing-v2",
    "status": "reserved",
    "currency": "CNY",
    "reserve_basis": "ark_official_1_5",
    "reserved_amount": "5.961600",
    "charged_amount": null,
    "refund_amount": null,
    "supplement_amount": null,
    "markup": "1.5"
  }
}
```

### 视频成功、账单证据未到

```json
{
  "id": "task_public_xxx",
  "request_id": "req_20260811_000001",
  "object": "video",
  "model": "seedance-2.0",
  "status": "succeeded",
  "progress": 100,
  "created_at": 1786400000,
  "completed_at": 1786400100,
  "result": null,
  "result_delivery": "pending_settlement",
  "billing": {
    "contract_version": "xtai-video-billing-v2",
    "status": "settlement_pending",
    "currency": "CNY",
    "reserve_basis": "ark_official_1_5",
    "reserved_amount": "5.961600",
    "charged_amount": null,
    "refund_amount": null,
    "supplement_amount": null,
    "markup": "1.5"
  }
}
```

此时下游显示“视频已生成，正在核对最终费用”，继续查询；不得把预扣显示为最终实际消费，
也不得自行请求第二个视频。

### 最终结算并可交付

```json
{
  "id": "task_public_xxx",
  "request_id": "req_20260811_000001",
  "object": "video",
  "model": "seedance-2.0",
  "status": "succeeded",
  "progress": 100,
  "created_at": 1786400000,
  "completed_at": 1786400102,
  "result": {
    "type": "url",
    "url": "https://api.example.com/v1/videos/task_public_xxx/content"
  },
  "result_delivery": "ready",
  "billing": {
    "contract_version": "xtai-video-billing-v2",
    "status": "settled",
    "currency": "CNY",
    "reserve_basis": "ark_official_1_5",
    "reserved_amount": "5.961600",
    "charged_amount": "2.175000",
    "refund_amount": "3.786600",
    "supplement_amount": "0.000000",
    "markup": "1.5",
    "pricing_revision": "official-fallback-2026-08-09.1",
    "settled_at": "2026-08-11T12:30:02+08:00"
  }
}
```

只有同时满足以下条件才停止轮询并交付：

```text
status == succeeded
result_delivery == ready
billing.status in [settled, settled_with_debt]
result.url 非空
```

`result.url`始终是星途中转站代理地址，不会暴露上游域名。下游读取该地址时仍须携带：

```http
Authorization: Bearer <XINGTU_VIDEO_API_TOKEN>
X-XingTu-Contract-Version: xtai-video-billing-v2
```

下游需要长期保存时，应在授权范围内及时下载到自己的对象存储。

## 6. 状态表

任务状态：

| 状态 | 下游处理 |
|---|---|
| `queued` | 已受理，继续查询 |
| `submitting` | 正在提交上游，继续查询 |
| `running` | 正在生成，显示进度并继续查询 |
| `succeeded` | 视频生成成功；仍需检查计费和交付状态 |
| `failed` | 生成失败，停止生成轮询，继续确认退款状态 |
| `uncertain` | 不确定是否提交成功，禁止换请求ID重提，联系管理员 |
| `pending_review` | 人工复核，禁止重提 |

计费状态：

| 状态 | 下游处理 |
|---|---|
| `reserved` | 已预扣，非最终费用 |
| `settlement_pending` | 等待可信真实扣费证据，继续查询 |
| `settled` | 最终收费完成 |
| `settled_with_debt` | 最终收费完成，但普通钱包余额已为负；结果仍可交付，禁止新任务 |
| `refund_pending` | 已决定退款，等待账户入账 |
| `refunded` | 已全额退款 |
| `payment_required` | 硬限额令牌或订阅额度无法补扣，按错误提示处理 |
| `pending_review` | 账单证据冲突或异常，等待人工处理 |

结果交付状态：

| 状态 | 下游处理 |
|---|---|
| `pending_settlement` | 结果已存在但暂不交付，继续查询 |
| `ready` | 可以读取 `result.url` |
| `unavailable` | 尚无结果或任务失败 |

## 7. Token字段

如果上游提供可信Token使用量，响应可以包含：

```json
{
  "usage": {
    "output_tokens": 86400,
    "total_tokens": 86400
  }
}
```

按次或按秒上游可能不返回 `usage`。下游最终扣费只能使用 `billing.charged_amount`，不能用
Token自行反算费用。

## 8. 幂等、超时和重试

- 同一 `request_id`、相同请求内容：返回原任务，不重复生成、不重复扣费。
- 同一 `request_id`、不同请求内容：HTTP 409 `idempotency_conflict`。
- HTTP 409 `request_in_progress`：等待 `Retry-After` 秒数，使用同一请求ID重试或稍后查询。
- HTTP 409 `request_uncertain`：禁止生成新请求ID，保留任务ID并联系管理员。
- 429和可重试5xx：指数退避，但仍使用同一请求ID和同一请求内容。
- 请求超时不代表提交失败；不能立即改走另一个渠道。

建议退避：2秒、4秒、8秒、15秒、30秒，之后每30秒一次。下游自己的业务等待超时只停止
前台等待，不取消服务端任务；后续仍用原任务ID恢复。

## 9. 错误格式

```json
{
  "error": {
    "code": "idempotency_conflict",
    "message": "request_id was already used with a different payload",
    "request_id": "req_20260811_000001",
    "task_id": "task_public_xxx",
    "retryable": false
  }
}
```

下游只根据 HTTP状态码、`error.code`和`retryable`处理，不解析英文 `message`。

常用错误：

| HTTP | code | 含义 |
|---:|---|---|
| 400 | `unsupported_contract_version` | 合同版本错误 |
| 400 | `missing_idempotency_key` | 缺少幂等键或请求ID |
| 400 | `idempotency_key_mismatch` | 请求头与JSON请求ID不一致 |
| 400 | `invalid_provider_id` | `provider_id`不是固定值 |
| 400 | `missing_generate_audio` | 没有明确传声音开关 |
| 400 | `unsupported_video_sku` | 模型、分辨率或时长不在方舟官方预扣表内 |
| 401 | 认证类错误 | API令牌无效 |
| 402/403 | 余额或权限错误 | 余额、订阅或令牌硬限额不足 |
| 409 | `idempotency_conflict` | 相同请求ID对应了不同内容 |
| 409 | `request_in_progress` | 原提交正在处理中，可按原ID重试 |
| 409 | `request_uncertain` | 原提交状态不确定，禁止新ID重提 |
| 409 | `task_contract_mismatch` | 用v2合同查询了旧合同任务 |
| 429 | 限流错误 | 按 `Retry-After` 退避 |
| 5xx | 服务错误 | `retryable=true`时使用原ID重试 |

## 10. 普通注册用户余额

- 任务受理时普通钱包余额大于0，可以让当前一个任务预扣或补扣后跨到负数。
- 该任务仍继续生成、结算和交付。
- 余额小于或等于0后禁止创建下一任务。
- 用户充值先冲抵负余额；余额重新大于0后才能创建新任务。
- 订阅额度和带硬上限的API令牌不允许透支。
- `settled_with_debt`已经是最终结算，充值后不得再次补扣同一任务。

## 11. 隐私边界

公共响应严禁出现：

```text
Paisio、Toonflow或其他供应商名称
渠道ID和内部路由顺序
上游原始模型名称
上游任务ID
上游账户和凭据
上游真实成本
利润、毛利和加价内部明细
结算证据原文或指纹
```

`billing.charged_amount`是用户最终收费，不是上游真实成本。

## 12. 下游最小处理伪代码

```text
request_id = load_or_create_stable_request_id(user_action)
response = POST /v1/videos with same request_id in header and body

if response is success:
    persist response.id as task_id
else if error.retryable:
    retry with the exact same request_id and payload
else:
    show stable error code and stop automatic resubmission

loop:
    task = GET /v1/videos/{task_id}
    persist task status and billing fields

    if task.status == failed and task.billing.status == refunded:
        stop
    if task.result_delivery == ready
       and task.billing.status in [settled, settled_with_debt]:
        deliver task.result.url
        stop
    if task.status in [uncertain, pending_review]
       or task.billing.status in [payment_required, pending_review]:
        stop automatic retry and request operator review

    wait 2-5 seconds
```

## 13. 上线验收清单

- [ ] 测试与生产使用不同API令牌。
- [ ] API令牌只在服务端保存。
- [ ] `XINGTU_VIDEO_BASE_URL` 是公网HTTPS地址；成功响应中的 `result.url` 不得是 localhost 或内网地址。
- [ ] 每次用户操作只生成一个稳定 `request_id`。
- [ ] `Idempotency-Key`和JSON `request_id`始终相同。
- [ ] `generate_audio`始终明确传布尔值，带声视频传 `true`。
- [ ] 相同请求重试不会产生第二个 `task_id`。
- [ ] 相同请求ID修改提示词会收到409冲突。
- [ ] 下游分别保存任务状态、计费状态和交付状态。
- [ ] `settlement_pending`不会被当作最终收费。
- [ ] 只有 `result_delivery=ready`才交付视频。
- [ ] 金额以六位小数字符串保存。
- [ ] 按次/按秒任务没有Token时，下游仍能正确显示最终收费。
- [ ] 失败任务能看到退款状态。
- [ ] `settled_with_debt`能交付当前结果，但阻止下一任务。
- [ ] 日志和页面没有展示渠道、上游成本或利润。
- [ ] 超时恢复使用原请求ID和原任务ID，不新建付费任务。

## 14. 兼容说明

旧客户端不发送 `X-XingTu-Contract-Version` 时继续使用原 OpenAI 视频响应格式。星途AI软件
必须始终发送该版本头，才能获得本协议的统一字段、六位金额和幂等保障。新合同只改变视频
公共接口，不改变 CLR 和非视频计费。
