# Verification

- Four fail-closed patcher tests passed, as did Python compilation and
  `git diff --check`.
- Production source commit: `e184444b75dcd9b522fe23d1ce1b9e95c38d2ea2`.
- Rollback bundle:
  `/opt/ai-api-stack/backups/issue32-model-health-20260724-185746`.
- Deployed image:
  `new-api-fixed:model-only-health-20260724-185800`.
- The image build completed both the TypeScript frontend and Go backend stages.
- A real administrator browser session loaded `/channel-health`. The safe page
  displayed model monitoring and hourly aggregation copy. The production source
  and embedded image contain the model-performance endpoint and no legacy
  private endpoint or stale five-minute copy.
- Ten consecutive production rounds passed site status, pricing, payment-route
  authentication, all nine containers, actual-cost pricing state, model-only
  source invariants, hourly Beijing cron, and restored test-account role.
- Ten consecutive external rounds passed monitor Basic Auth boundaries and
  confirmed public TCP 8791 remained blocked.
