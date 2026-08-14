# 实施计划

1. 验证主域名和候选域名的 DNS、TLS、公开状态、模型目录。
2. 验证主域名的账号登录、余额和文字渠道密钥。
3. 备份 PostgreSQL、`upstreams.json` 与 `upstream-credentials.json`。
4. 原子更新监控文件；事务更新渠道地址、文字模型和能力状态。
5. 重启 NewAPI、运行余额采集并完成最小请求与多轮验证。
