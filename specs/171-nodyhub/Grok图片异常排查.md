# Grok Imagine Image2.0排查与单次重测

2026-09-23，用户授权查因或重测。没有上线该图片模型，没有改变生产价格，没有重复提交原任务。

## 原任务核验

- 模型：grok-imagine-image-2.0。
- POST /v1/images/generations，去掉size后HTTP200返回code200、data[0].status=submitted，task_id=task_01M373ZZNB7QND2Q9ZQ7NF9MXD。
- 没有url或b64图片数据。HTTP200在此只证明请求被提交，不代表生成完成。
- Nody账户普通图片消费日志240记225000quota=.45额度=.675元。
- Nody /api/task/self按该ID查询total0；常见任务查询以及/v1/images/tasks/{id}均未找回结果。
- 普通消费记录other中有request_conversion=openai_image，没有task字段。

## 本轮重测

仅尝试一次Nody广场声明的POST /v1/chat/completions，模型不变、单张简单测试提示词、stream=false。

- HTTP422，Content-Type text/event-stream，但响应正文为JSON error。
- error.type=apimart_error，code=bad_response_status_code。
- message要求稍后重试，request ID：20260923215402331988206xMwhx9nQ。
- 耗时约10.58秒，无图片。
- 复核账户余额仍9.591176额度，测试令牌累计used仍9.62额度，故本次未新增扣费。
- 测试令牌2553已在finally中停用，状态2。原生产图片密钥未动。

## 归因边界

已确认：密钥能通过认证，账户余额充足；问题出现在该图片模型的上游响应/异步结果交付环节。

高置信推断：普通图片入口透传了异步提交票据并记录消费，但未建立可供本站查询的Nody网关任务映射。重测还暴露了APIMart类型的上游服务错误。

不能宣称：已读到Nody内部代码、确知内部故障根因、原任务实际生成失败、原扣费已退款，或更换密钥就能恢复。

需要NodyHub处理：按原task_id查内部供应商任务，给出能返回原图片的查询接口/结果；无法交付时核对并退回.45额度；确认同步图片API或完整异步协议及该模型稳定性。Nody的密钥没有被发送到APIMart或其他第三方域名。

证据保存在服务器私有目录/opt/ai-api-stack/backups/nodyhub171-evidence/grok-image-test-response.json及grok-image-chat-retest.json。
