# Implementation Plan: 当前用户微信订单状态同步

**Branch**: `codex/230-wechat-order-reconciliation` | **Date**: 2026-08-25 | **Spec**: [spec.md](spec.md)

## Summary

在当前用户充值列表控制器返回前，对当前页中最多五个待支付原生微信订单执行一个共享五秒截止时间的最佳努力查单。复用现有交易字段校验、幂等入账和待支付状态更新函数；任何配置、上游、超时或单订单错误只保留持久化状态，不使列表失败。

## Technical Context

**Language/Version**: Go 1.26（兼容项目 Go 1.22+）
**Primary Dependencies**: Gin、GORM、wechatpay-go native payments
**Storage**: 现有 `top_ups` 和 `users` 表；无迁移
**Testing**: Go testing、testify、SQLite 内存数据库；现有跨数据库 CI
**Target Platform**: new-api Linux 服务，SQLite/MySQL/PostgreSQL
**Performance Goals**: 单请求最多五次微信查单，共享五秒截止时间；失败不阻塞列表
**Constraints**: 当前用户身份来自 Gin 上下文；禁止客户端 user_id；复用行锁事务；不记录敏感响应
**Scale/Scope**: 一个列表控制器、一个可注入查询器接口、同步 helper、控制器/模型回归和生产只读验证

## Constitution Check

- **I Production invariants — PASS**: 不改变支付金额、计费、回调、路由或公开字段。
- **II Selective adoption — PASS**: 本功能不是上游升级，不引入批量合并。
- **III Test-first — PASS**: 有界筛选、状态映射、失败开放和控制器行为先写失败测试。
- **IV Cross-platform/billing safety — PASS**: 复用 GORM 与现有事务，三数据库兼容；不改变 quota 计算。
- **V Reversible delivery — PASS**: 独立分支和 PR；生产部署不包含在实现 PR。
- **Post-design — PASS**: 无例外或新数据库结构。

## Project Structure

```text
controller/topup.go
controller/topup_wechat.go
controller/topup_wechat_test.go
model/topup.go
specs/141-wechat-order-reconciliation/
```

**Structure Decision**: 列表控制器拥有“是否同步当前页”的编排；微信文件拥有查询器接口与单订单/批量最佳努力逻辑；模型继续拥有事务更新和幂等入账。

## Complexity Tracking

无违规。
