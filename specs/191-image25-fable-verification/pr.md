# ⚠️ 提交说明 / PR Notice

> This is AI-assisted work. The source was independently reviewed and the
> summary below was checked against the actual diff; human confirmation is not
> claimed. This draft is not deployed or all-model stability acceptance.

## 📝 变更描述 / Description

Preserve a supplier's returned request ID in the generic Image2.5 adapter so
success and uncertain failures retain the same bounded correlation evidence.
Forward only safe correlation IDs in the established header priority. Do not
invent an invoice ID from client input or alter the image-only endpoint, size,
authorization, pricing, concurrency or single-submit behavior.

Record the user's current CNY10 limit, conditional provider-switch authority,
real candidate tests and cost-coverage blockers separately from source review.
Code Plan's three candidate images fail1024-size compatibility. Nody's two
images and exact Fable produce output but do not certify the ordinary native
chain or automatically enable a loss-making fallback.

## 🚀 变更类型 / Type of change

- [x] 🐛 Bug 修复 (Bug fix): discarded returned supplier correlation evidence
- [ ] ✨ 新功能 (New feature)
- [ ] ⚡ 性能优化 / 重构 (Refactor)
- [x] 📝 文档更新 (Documentation)

## 🔗 关联任务 / Related Issue

Related #191; does not close remaining production/model acceptance gates.
Stacked on the existing funded-routing source branch; does not modify its
production configuration. Source review is posted on issue191.

## ✅ 提交前检查项 / Checklist

- [ ] **人工确认:** Awaiting human review; do not represent AI review as human.
- [x] **非重复提交:** This is a focused follow-up, not the wider pending native rollout.
- [x] **Bug fix 说明:** Missing supplier correlation retention is proven by RED tests.
- [x] **变更理解:** No business replay, size, authentication or money behavior changes.
- [x] **范围聚焦:** Only the generic adapter and its tests plus scoped evidence docs.
- [x] **本地验证:** Generic13 and unchanged Nody14 mock tests; diff check pass.
- [x] **安全合规:** No credentials, user prompts or wallet identifiers published.

## 📸 运行证明 / Proof of Work

- RED contracts failed on the previous generic implementation.
- GREEN `python -B -m unittest discover -s ops/image25-adapter -p test_app.py`:13pass.
- Existing Nody equivalent tests:14pass.
- Independent seven-criteria/security review:0unaddressed findings.
- Source base SHA matches the serving generic adapter, but no production
  deployment is claimed. Provider IDs are correlation only, not automatic
  exact financial receipt association.
- See `specs/191-image25-fable-verification/results.md` for limited real
  candidate results, dimension failures and unresolved cost/funding decisions.
