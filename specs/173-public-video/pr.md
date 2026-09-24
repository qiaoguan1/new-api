# ⚠️ 提交说明 / PR Notice
AI-assisted implementation based on the operator's explicit A approval. The manual confirmation checkbox is reserved for the maintainer.

## 📝 变更描述 / Description

Stacked on #172. Enable ordinary user API keys for the seven verified Nody video models and preserve Flare/Sunburst image tariff across authorized key groups. Coordinate native DB-authoritative quota reads/writes and conditional preconsume with a durable public task/wallet ledger. Preserve existing shared-service authentication and callbacks. Deployment uses an audited backport onto production baseline72d5a71c, with backups and controlled drain.

## 🚀 变更类型 / Type of change
- [ ] 🐛 Bug 修复 (Bug fix)
- [x] ✨ 新功能 (New feature)
- [x] ⚡ 性能优化 / 重构 (Refactor)
- [x] 📝 文档更新 (Documentation)

## 🔗 关联任务 / Related Issue
- Closes #173
- Depends on #172
- Pre-existing full-service-suite instability: #174
- Review: https://github.com/qiaoguan1/new-api/issues/173#issuecomment-5816801095

## ✅ 提交前检查项 / Checklist
- [ ] **人工确认:** 供维护者确认；本说明由AI辅助整理。
- [x] **非重复提交:** 已检查相关任务及现有实现。
- [x] **Bug fix 说明:** 本PR为已授权功能接入，不将需求差异描述为旧系统故障。
- [x] **变更理解:** 已检查鉴权、资金事务、重试与回滚边界。
- [x] **范围聚焦:** 普通密钥文图/视频访问及必要账户额度协调。
- [x] **本地验证:** 相关验证和真实验收证据已记录；不声称全套service测试绿色。
- [x] **安全合规:** 无密钥入库；跨用户404；不凭未知状态退款；新进程不输出含密钥/提示词的ORM SQL。

## 📸 运行证明 / Proof of Work

Production task vjob_b9db731232f62db98c921bc9a67194f6 succeeded/settled/ready, charged0.675CNY; user+token each337500quota, one consume record. Repeated request reused task. Cross-user404. MP4 downloaded with audio. Nine new model IDs listed with normal inherited-default key; all seven authorized key groups see seven videos. Original service key200. Isolated default-key Flare proof charged0.45CNY. See specs/173-public-video/deployment.md and the Chinese API handoff.

Full model/public-video/middleware packages and relevant billing/quota tests pass. Full service-suite affinity cache tests reproduce failures on unchanged baseline; tracked #174, not hidden or relabeled as passing. Production test keys revoked and synthetic unspent test credit removed; temporary canary services/database cleaned, native rollback retained.
