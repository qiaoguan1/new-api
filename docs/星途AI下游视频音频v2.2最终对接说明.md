# 星途AI下游视频、音频 v2.2 最终对接说明

合同版本：`xtai-video-billing-v2.2`  
适用范围：包含参考视频或参考音频的视频生成任务。无参考视频、无参考音频的任务继续使用 v2.1。

这是下游唯一需要执行的最终文档。下游不需要知道或处理任何上游渠道、原始模型、登录凭据、路由选择、失败切换或上游账单查询。

## 一、双方职责

星途中转站负责：

- 接收统一模型与参考素材；
- 校验媒体、选择可用路线并处理安全恢复；
- 按方舟官方对应输入模式价格 × 1.5 计算预冻结金额；
- 任务结束后取得可信的单任务上游实际净扣费，按实际净扣费 × 1.5 结算；
- 返回任务状态、退款/补扣金额和中转站视频地址；
- 发送 HMAC-SHA256 签名 Webhook。

星途AI下游负责：

- 生成并永久保存唯一 `request_id`；
- 将本地素材上传至规定的私有 TOS，计算真实 SHA-256 和媒体元数据；
- 提交、保存中转站 `task_id`、接收回调并兜底查询；
- 根据中转站返回的金额，在下游自己的账户体系中处理冻结、退款或补扣；具体用户扣费规则由下游自行决定。

## 二、固定接口

生产 API 基址：`https://api.aixingtuyun.com`

| 用途 | 方法与路径 |
| --- | --- |
| 能力目录 | `GET /v1/capabilities` |
| 价格目录 | `GET /v1/video-prices` |
| 创建任务 | `POST /v1/videos` |
| 查询任务 | `GET /v1/videos/{task_id}` |
| 下载结果 | `GET /v1/videos/{task_id}/content` |

所有接口携带：

```http
Authorization: Bearer <中转站分配的服务端 Token>
X-XingTu-Contract-Version: xtai-video-billing-v2.2
```

创建任务还必须携带：

```http
Idempotency-Key: <与 JSON request_id 完全相同>
Content-Type: application/json
```

Token、Webhook 密钥只能放在下游服务端，不能放进浏览器、App 或普通日志。

## 三、下游必须先做的能力判断

下游在展示或提交前读取 `/v1/capabilities`，只能提交对应稳定模型、分辨率和参考组合同时满足：

```text
supported == true
available == true
```

稳定模型与分辨率：

| 稳定模型 | 版本 | 分辨率 |
| --- | --- | --- |
| `seedance-2.0` | 标准版 | `480p`、`720p`、`1080p` |
| `seedance-2.0-fast` | Fast版 | `480p`、`720p` |
| `seedance-2.0-mini` | Mini版 | `480p`、`720p` |

下游只提交这些稳定名称。不要提交任何渠道名或上游原始模型名。

## 四、参考素材规则

1. 参考视频：最多 3 段；单段不超过 200 MiB；MP4 容器；H.264 或 H.265/HEVC；单段 2—15 秒；总时长不超过 15 秒。
2. 参考音频：最多 3 段；单段不超过 15 MiB；单段 2—15 秒；总时长不超过 15 秒。
3. 音频格式：
   - MP3：`mime_type=audio/mpeg`，`codec=mp3`
   - WAV：`mime_type=audio/wav`，`codec=wav`
   - AAC：`mime_type=audio/aac`，`codec=aac`
   - M4A：`mime_type=audio/mp4`，`codec=m4a`
4. 图片最多 9 张；图片、参考视频、参考音频合计最多 12 项。
5. 参考音频不能作为唯一输入；有参考音频时，至少同时提供一张图片或一段参考视频。
6. `generate_audio` 控制输出视频是否生成声音；它与输入字段 `reference_audios` 相互独立，必须明确传布尔值。
7. 所有图片、视频、音频 URL 必须来自：`xtai-media-temp-20260722.tos-cn-beijing.volces.com`，使用公网 HTTPS 443 短期签名 URL，不允许重定向。
8. TOS 对象必须返回与声明一致的 `Content-Type`。签名有效期必须覆盖中转站校验和上游读取，建议不少于 2 小时。
9. 下游必须按文件原始字节计算小写 SHA-256。不得使用文件名、URL 或转码前文件的摘要代替。
10. 创建请求 JSON 上限为 256 KiB，不允许内嵌 Base64 媒体。

中转站会在冻结和创建任务前实际下载并校验参考视频、参考音频的长度、SHA-256、容器、编码、时长、宽高、采样率和声道；不一致会直接拒绝且不创建任务。

## 五、创建请求

下面是“图片 + MP3 参考音频，并要求结果带声”的完整示例：

```http
POST /v1/videos HTTP/1.1
Authorization: Bearer <Token>
X-XingTu-Contract-Version: xtai-video-billing-v2.2
Idempotency-Key: req_20260814_000001
Content-Type: application/json
```

```json
{
  "provider_id": "video-aixingtu-api",
  "request_id": "req_20260814_000001",
  "model": "seedance-2.0",
  "resolution": "720p",
  "duration": 8,
  "aspect_ratio": "16:9",
  "generate_audio": true,
  "prompt": "保持人物外观和音乐节奏，生成自然运镜的视频",
  "image": "https://xtai-media-temp-20260722.tos-cn-beijing.volces.com/path/frame.png?<签名>",
  "image_identities": [
    "<图片原始字节的64位小写SHA-256>"
  ],
  "reference_audios": [
    {
      "role": "reference_audio",
      "url": "https://xtai-media-temp-20260722.tos-cn-beijing.volces.com/path/music.mp3?<签名>",
      "sha256": "<音频原始字节的64位小写SHA-256>",
      "mime_type": "audio/mpeg",
      "codec": "mp3",
      "size_bytes": 1048576,
      "duration_seconds": "8.000000",
      "sample_rate_hz": 44100,
      "channels": 2
    }
  ]
}
```

参考视频项格式：

```json
{
  "role": "reference_video",
  "url": "https://xtai-media-temp-20260722.tos-cn-beijing.volces.com/path/reference.mp4?<签名>",
  "sha256": "<视频原始字节的64位小写SHA-256>",
  "mime_type": "video/mp4",
  "size_bytes": 20971520,
  "duration_seconds": "8.000000",
  "width_pixels": 1280,
  "height_pixels": 720
}
```

多图片使用 `images` 数组；`image_identities` 必须与图片数组等长、同顺序。`reference_videos` 和 `reference_audios` 的顺序也属于请求身份，不能在重试时改变。

同一业务任务超时、断网或收到 5xx/429 时，必须使用完全相同的 `request_id`、`Idempotency-Key` 和业务参数重试。签名 URL 可以刷新，只要素材 SHA-256 和元数据不变。禁止生成新 `request_id` 重提。

## 六、计费字段与下游动作

创建成功返回 HTTP 202；相同请求幂等重放返回 HTTP 200。下游保存 `id` 作为 `task_id`。

```json
{
  "id": "vjob_xxx",
  "request_id": "req_20260814_000001",
  "status": "queued",
  "result_delivery": "unavailable",
  "result": null,
  "billing": {
    "contract_version": "xtai-video-billing-v2.2",
    "status": "reserved",
    "currency": "CNY",
    "reserve_basis": "ark_official_1_5",
    "reserved_amount": "11.923200",
    "charged_amount": null,
    "refund_amount": null,
    "supplement_amount": null,
    "pricing_revision": "official-fallback-2026-08-09.1"
  }
}
```

- `reserved_amount`：中转站预冻结金额。含参考视频使用方舟“含视频输入”价格 ×1.5；没有参考视频时使用“无视频输入”价格 ×1.5。
- `charged_amount`：中转站最终应收，等于本次任务可信上游实际净扣费 ×1.5。
- `refund_amount`：相对预冻结应退金额。
- `supplement_amount`：相对预冻结应补金额。

金额均为 CNY 六位小数字符串。下游用十进制定点数保存，不能用 JavaScript 浮点数重新计算。下游无需读取上游价格，也不要自行再乘 1.5。

视频生成成功但尚未取得真实账单时会返回：

```text
status = succeeded
billing.status = settlement_pending
result_delivery = pending_settlement
result = null
```

此时不得交付视频、不得把预冻结当最终消费、不得重新提交。最终只有同时满足下列条件才可交付：

```text
status == succeeded
billing.status == settled
result_delivery == ready
result.url 非空
```

下载 `result.url` 时继续携带同一个 Authorization 和 v2.2 合同头。

## 七、查询与 Webhook

下游以 `GET /v1/videos/{task_id}` 为权威数据源。建议每 30—60 秒兜底查询；收到回调后立即查询一次，不需要高频轮询。

生产回调：`https://ds.aixingtuyun.com/auth/api/webhooks/video`  
测试回调：`https://ds.aixingtuyun.com/auth-video-v2-staging/api/webhooks/video`

生产和测试使用两把独立 HMAC-SHA256 密钥，密钥由双方通过安全渠道配置，不写进本文。

主要事件：

| 事件 | 含义 |
| --- | --- |
| `video.task.succeeded` | 视频生成完成，可能仍在等待真实账单 |
| `video.billing.settled` | 最终费用已结算，可按 `result_delivery` 交付 |
| `video.task.failed` | 任务失败，读取退款字段 |
| `video.billing.pending_review` | 证据冲突，保持原任务，禁止重提 |

回调头：

```http
X-XingTu-Contract-Version: xtai-video-billing-v2.2
X-XingTu-Event-Id: evt_xxx
X-XingTu-Timestamp: 1786406400
X-XingTu-Delivery-Attempt: 1
X-XingTu-Signature: v1=<小写十六进制HMAC-SHA256>
```

验签原文：

```text
signing_input = X-XingTu-Timestamp + "." + raw_body_bytes
expected = "v1=" + lowercase_hex(HMAC_SHA256(webhook_secret, signing_input))
```

下游必须使用原始请求体字节验签、恒定时间比较、校验时间戳偏差不超过 5 分钟，并以 `event_id` 建唯一索引。重复事件直接返回 2xx，不得重复处理金额。回调保存成功后尽快返回 2xx，业务处理放后台队列。

Webhook 的 `data` 包含中转站任务状态、`billing`、素材 URL-free 摘要及在可交付时的中转站 `result.url`，不包含渠道名、上游任务号、上游成本或凭据。

## 八、错误处理

错误响应统一为：

```json
{
  "error": {
    "code": "reference_audio_format_invalid",
    "category": "validation",
    "message": "具体安全错误说明",
    "http_status": 400,
    "retryable": false,
    "uncertain": false,
    "phase": "validate"
  }
}
```

- 400/409 且 `retryable=false`：修正参数或素材后，原业务可使用新 `request_id` 提交。
- 429/503 且 `retryable=true`：保持原 `request_id` 和原业务参数退避重试。
- 已获得 `task_id` 后只查询该任务，禁止再次创建。
- 下游应直接展示安全 `message`，不要统一改写为“生成失败”。

## 九、下游直接修改清单

- [ ] 增加 v2.2 合同头和创建请求结构。
- [ ] 服务端上传 TOS、设置正确 Content-Type、计算 SHA-256 和媒体元数据。
- [ ] 接入 MP4、MP3、WAV、AAC、M4A；音频参考强制伴随图片或视频。
- [ ] 接入能力目录，只开放 `supported=true && available=true` 的组合。
- [ ] 永久保存 `request_id`、`task_id`、合同版本和金额字符串。
- [ ] 超时只用相同 `request_id` 幂等重试。
- [ ] 接入查询状态机和结果下载。
- [ ] 分别配置测试、生产 Webhook 密钥，完成原始字节验签与 `event_id` 去重。
- [ ] 按中转站返回的 `reserved_amount`、`charged_amount`、`refund_amount`、`supplement_amount` 更新下游自己的账务；不自行计算中转站成本。
