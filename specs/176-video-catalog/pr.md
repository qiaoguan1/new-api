# ⚠️ 提交说明 / PR Notice
AI-assisted implementation. Manual confirmation remains for the maintainer.

## 📝 变更描述 / Description
Add a read-only public pricing projection for the seven verified gateway videos, preserving native rows, native session metadata and exact group-normalized display prices. This closes the gap between /v1/models and the model square. No charge calculation or wallet mutation changes.

## 🚀 变更类型 / Type of change
- [x] 🐛 Bug 修复 (Bug fix)
- [ ] ✨ 新功能 (New feature)
- [ ] ⚡ 性能优化 / 重构 (Refactor)
- [x] 📝 文档更新 (Documentation)

## 🔗 关联任务 / Related Issue
- Closes #176
- Stacked on #175

## ✅ 提交前检查项 / Checklist
- [ ] **人工确认:** Reserved for maintainer.
- [x] **非重复提交:** Verified deployed catalog/API mismatch.
- [x] **Bug fix 说明:** Reproduced missing seven public pricing rows.
- [x] **变更理解:** Price normalization, metadata privacy and degradation reviewed.
- [x] **范围聚焦:** Catalog plus separately approved one-token group migration.
- [x] **本地验证:** Full public-video tests and independent public read checks passed.
- [x] **安全合规:** No credentials in source/output; no paid tests.

## 📸 运行证明 / Proof of Work
Original47 rows unchanged; seven added; video group12; total54; catalog ready. Same customer credential HTTP200 after explicit group-only migration; old service key200. Review/deployment evidence under specs/176-video-catalog.
