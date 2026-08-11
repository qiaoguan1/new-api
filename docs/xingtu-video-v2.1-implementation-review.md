# 星图视频合同 v2.1 实现与安全审查

## 执行摘要

审查范围为 `xtai-video-billing-v2.1` 的音轨透传、预扣和结算、结果交付、请求体限制、
历史合同兼容、网关结算发布及下游协议。审查发现的6项问题均已在本变更中修复；当前范围
未留存 Critical、High、Medium 或 Low 未处理项。

## 已修复发现

### SEC-001：Redis冷缓存可能覆盖并发预扣

- Rule ID：GO-CONC-001
- 原严重度：High
- 位置：`model/user_cache.go:updateUserCache`、`model/user.go:ReserveUserQuotaIfEnough`
- 证据：旧实现从数据库读取余额后直接 `HSET Quota`；两个并发冷缓存请求可能用相同旧余额覆盖先完成的扣减。
- 影响：高并发下可能接受超过实际余额的多个视频任务。
- 修复：缓存刷新对Quota改用原子 `HSETNX`，只初始化不存在的Quota；完整预扣使用Redis Lua或数据库条件更新。
- 缓解：生产上线后继续监控余额负数断言和结算异常告警。
- 误报说明：即使生产当前未发生冷缓存并发，本修复仍移除了可触发竞态。

### SEC-002：非当前合同头可绕过256 KiB限制

- Rule ID：GO-HTTP-002
- 原严重度：Medium
- 位置：`middleware/xingtu_video_body_limit.go:XingTuVideoBodyLimit`
- 证据：初版只限制精确的v2.1头，旧版或未知星图合同头会先进入后续中间件。
- 影响：攻击者可用无效合同头提交超大请求，消耗解析、认证或分发资源。
- 修复：所有带星图合同头的 `/v1/videos` 创建请求均在认证、分发、扣费和上游调用前限制为256 KiB。
- 缓解：入口代理还应设置总请求体上限作为纵深防御。
- 误报说明：无合同头的普通OpenAI兼容接口保持原行为，这是明确的兼容边界。

### BILL-001：普通钱包允许负余额

- Rule ID：BUSINESS-HARD-QUOTA
- 原严重度：High
- 位置：`model/user.go:ReserveUserQuotaIfEnough`、`service/video_task_settlement.go:ApplyVideoTaskSettlement`
- 证据：旧逻辑只要求预扣前余额大于0，结算补扣也可能把钱包扣为负数。
- 影响：用户可获得超过可用余额的视频，平台形成不可控应收款。
- 修复：v2.1预扣和正向补扣均执行原子 `quota >= amount` 条件更新；不足时不扣款并进入 `payment_required`。
- 缓解：回归测试覆盖并发预扣和并发补扣。
- 误报说明：非视频和旧OpenAI接口未被本合同改造。

### COMPAT-001：旧欠费记录仍可交付结果

- Rule ID：BUSINESS-RESULT-GATE
- 原严重度：High
- 位置：`model/task.go:ResultDeliveryStatus`、`model/task.go:xingTuVideoBilling`、`ops/video-job-gateway/store.py:Store.snapshot`
- 证据：旧实现将 `settled_with_debt`视为可交付终态。
- 影响：欠费任务可读取成品视频，与硬额度规则冲突。
- 修复：只有 `settled`可以交付；历史 `settled_with_debt`统一映射为 `payment_required`并隐藏结果。
- 缓解：Go与Python均增加历史记录回归测试。
- 误报说明：历史原始数据库状态不强制改写，公共语义已安全迁移。

### AUDIO-001：Paisio适配器遗漏声音参数

- Rule ID：BUSINESS-AUDIO-PRESERVATION
- 原严重度：Medium
- 位置：`ops/video-job-gateway/adapters.py:_base_body`
- 证据：旧Paisio请求体未复制 `generate_audio`，上游记录出现 `null`并可能生成无声视频。
- 影响：下游明确请求带声但上游收到默认值或空值。
- 修复：Paisio、RollDek、Toonflow均保留显式布尔值；`true`不得拒绝或静默降级。
- 缓解：三个适配器分别覆盖 `true`，并覆盖 `false`不被改成 `true`。
- 误报说明：统一模型当前均为支持声音的Seedance路由。

### IDEMP-001：预扣不足后原请求ID无法恢复

- Rule ID：BUSINESS-IDEMPOTENCY
- 原严重度：Medium
- 位置：`model/video_request_claim.go:ReopenVideoRequestClaim`、`controller/relay.go:prepareXingTuVideoRequest`
- 证据：预扣不足会把幂等声明永久标记为failed，同一请求充值后仍返回冲突。
- 影响：下游只能换请求ID，增加重复任务和重复收费风险。
- 修复：仅对 `insufficient_user_quota`使用条件更新原子恢复一次；并发重试只有一个请求能继续。
- 缓解：错误码保持稳定，下游必须使用原请求内容和原ID。
- 误报说明：不确定提交、上游5xx或其他失败不允许自动恢复。

## 七项实现审查

1. 正确性：官方价1.5倍预扣、可信实际成本1.5倍结算、多退少补和交付门禁一致。
2. 可维护性：合同常量集中，旧版与当前版本使用统一兼容函数，未散落新的硬编码判断。
3. 测试：Go覆盖额度、并发、历史状态和请求边界；Python覆盖音轨、网关结算和发布器版本传播。
4. 性能：请求体最多读取256 KiB；余额扣减为单条条件更新或单次Redis Lua，不增加全表扫描。
5. 安全：限制在认证/分发前执行；资金更新原子化；密钥未写入仓库或文档。
6. 文档：主协议和下游反馈清单均升级到v2.1，删除了拒绝声音和欠费交付的旧规则。
7. 部署：新提交只接受v2.1，历史v2只读/结算兼容，不修改CLR和非视频计费。

## 验证环境说明

- Windows本地 `go test -race`因未安装CGO编译器无法执行；需在Linux构建/部署环境补跑。
- `go test ./...`除根包外均通过；根包仅因本地未生成 `web/classic/dist`嵌入资源而无法编译，
  与本次代码无关。生产镜像构建流程必须先生成前端资源。
