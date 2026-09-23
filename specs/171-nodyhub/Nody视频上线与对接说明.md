# NodyHub 视频接入说明

日期：2026-09-23。Grok 图片 `grok-imagine-image-2.0` 按用户要求跳过，不进入生产路由。已上线的 Flare / Sunburst 图片模型不变。

## 首批开放规格

均为文字生成视频、16:9，`generate_audio=true`，保留上游默认音轨。只开放实测成功的组合；不宣称支持其他时长、分辨率、参考素材或关闭音轨。

| model | 分辨率 | 时长（秒） | 验证成本（元/条） | 本站预扣（元/条） |
|---|---|---:|---:|---:|
| wan3.0-video | 480p | 2 | 0.75 | 1.125 |
| wan3.0-video-prime | 480p | 2 | 1.08 | 1.62 |
| grok-imagine-1.5-video | 480p | 6 | 0.90 | 1.35 |
| grok-video-3 | 720p | 6 | 0.90 | 1.35 |
| grok-imagine-video-official | 480p | 1 | 0.45 | 0.675 |
| omni-flash | 720p | 4 | 2.55 | 3.825 |
| flux-3-video | 720p | 5 | 7.125 | 10.6875 |

成本来源：Nody 实际成功任务扣费，账户额度换算为1额度=1.5元人民币。预扣为验证成本×1.5；最终以该任务的上游实际净成本×1.5结算，多退少补。上述成本为本站内部资料，不是下游的转售定价指令。

新模型的预扣来源标识为 `verified_upstream_1_5`，不会伪装为方舟官方价。原有 Seedance 模型、路由和方舟预扣规则均保留。

## 下游请求

使用既有星途视频服务鉴权凭据，不是普通用户的 NewAPI API Key。凭据只在既有安全渠道交接，本文件不包含密钥。

```http
POST https://api.aixingtuyun.com/v1/videos
Authorization: Bearer <既有视频服务Token>
X-XingTu-Contract-Version: xtai-video-billing-v2.2
Idempotency-Key: <与request_id相同的唯一值>
Content-Type: application/json
```

```json
{
  "provider_id": "video-aixingtu-api",
  "request_id": "your-unique-request-id",
  "model": "grok-imagine-video-official",
  "prompt": "A small ball rolls across a table with a soft rolling sound.",
  "resolution": "480p",
  "duration": 1,
  "aspect_ratio": "16:9",
  "generate_audio": true
}
```

响应中的 `id` 为 `vjob_...`。相同 request_id 和相同请求只创建一次；不要遇到超时就换 request_id 重发。

模型目录：`GET /v1/capabilities`；精确价格：`GET /v1/video-prices`。均带上述鉴权和版本请求头。下游应读取目录中的时长、分辨率与可用性，不硬编码扩大范围。

查询：`GET /v1/videos/{id}`，使用相同鉴权。完成后重点读取：

- `status=succeeded`：生成完成。
- `billing.status=settled`：实际费用已核实并幂等结算。
- `billing.reserved_amount / charged_amount / refund_amount / supplement_amount`：人民币精确金额字符串。
- `result_delivery=ready`：结果可交付。
- `result_url`：通过本站视频服务鉴权访问的视频地址，保留音轨。

生成完成但账单尚未出现时，仍返回 `settlement_pending`，不会用预估费用假装实际结算。失败必须有明确退款证据；证据不足不重放任务、不凭空认定零成本。

沿用既有签名 Webhook 与重试机制，结算事件为 `video.billing.settled`；生产目标地址仍为既有星途AI回调地址，密钥不变。下游按事件ID去重，验签并关联自身 request_id；回调异常时使用任务查询补偿。本站结算金额是本站向下游的收费，下游自身用户的收费规则仍由下游决定。

## 运维与回滚

新镜像：`xtai/video-job-gateway:nody-7ede5308`。旧容器停止并保留为 `xtai-video-job-gateway-v2-production-rollback-nody171`，备份位于 `/opt/ai-api-stack/backups/nodyhub-video-20260923`。上线后回滚不得把旧数据库覆盖新任务数据。

Nody账单会话独立安全保存，每小时由既有刷新任务续期；单个渠道异常不得阻断其他渠道。此次不更改日报、自动改价或其他渠道启停策略。

## 上线验收结果

2026-09-23 23:27（北京时间）：生产已生效。`api.aixingtuyun.com` 和既有 `sub.aixingtuyun.com` 的能力/价格接口均返回200；7款新视频可用，原有模型条目与路由保持不变。

- 154项自动测试通过，独立代码复核通过。
- 生产验收任务：`vjob_64f51906c2954cd5b43b2ad32dcbfd5a`。
- 生成约28秒；状态 `succeeded`、结算 `settled`、交付 `ready`。
- 预扣0.675元、实际结算0.675元，退款及补扣均0元；成功扣费依据为上游任务账单0.45元实际成本。
- 相同请求重复提交返回同一任务号，无重复生成。
- 公网视频下载200，MP4含视频和音频轨道。
- `video.task.succeeded` 和 `video.billing.settled` 均已送达既有生产回调，均只发送1次，无错误。
- 本轮独立环境和生产各新增1个低成本验收视频，上游成本合计0.90元，不额外充值。
- 既有3笔成功账单与2笔失败退款记录通过只读核验；定时任务日志确认Nody账单授权自动刷新成功。

这里只确认中转站已开放接口及回调送达；下游是否把新模型显示在自己的菜单中，仍取决于下游读取目录或更新白名单，不等于本站替下游修改了客户端。
