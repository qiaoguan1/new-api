# Research: 当前用户微信订单状态同步

## Decision 1: 服务端列表同步而非桌面 N+1

- **Decision**: 在中转站当前用户列表入口同步可见待支付微信订单。
- **Rationale**: Web 与桌面共享真实状态，避免每个客户端重复实现和泄漏订单协调逻辑。
- **Alternatives considered**: 桌面逐单查询会重复网络调用且 Web 仍陈旧；仅靠回调无法修复漏回调或旧订单。

## Decision 2: 有界最佳努力

- **Decision**: 每页最多五个订单，共享五秒 context deadline；任何错误保留本地状态并继续列表。
- **Rationale**: 支付历史必须可用，且不能为未付款订单执行无界外部请求。
- **Alternatives considered**: 全页并发会放大微信压力；同步所有历史会导致 DoS；错误直接 5xx 会让账户中心不可用。

## Decision 3: 复用既有可信状态路径

- **Decision**: SUCCESS 使用 `validateWechatTransaction` + `RechargeWechatPay`；终态使用 `UpdatePendingTopUpStatus`。
- **Rationale**: 保持 app/mch/order/amount/currency 校验、行锁和幂等入账不变。
- **Alternatives considered**: 在控制器直接更新余额会复制高风险账务逻辑并破坏跨库一致性。

## Decision 4: 可注入最小查询接口

- **Decision**: helper 接受只暴露 `QueryOrderByOutTradeNo` 的接口，生产适配现有 NativeApiService，测试使用无网络 fake。
- **Rationale**: 可确定性验证数量、超时、状态和失败开放，不改全局客户端或使用竞态 monkey patch。
- **Alternatives considered**: 全局函数变量会产生并发测试竞态；真实微信集成测试需要秘密和付费环境。
