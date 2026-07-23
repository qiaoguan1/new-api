import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[3]
MODEL_STATUS_PAGE = ROOT / "web/default/src/features/channel-monitor/index.tsx"
MODEL_STATUS_ROUTE = (
    ROOT / "web/default/src/routes/_authenticated/channel-health/index.tsx"
)
SIDEBAR = ROOT / "web/default/src/hooks/use-sidebar-data.ts"
API_ROUTER = ROOT / "router/api-router.go"
PERF_METRICS_CONTROLLER = ROOT / "controller/perf_metrics.go"
README = ROOT / "ops/channel-monitor/README.md"


class PublicModelStatusPolicyTests(unittest.TestCase):
    def test_customer_page_uses_only_the_model_performance_summary(self):
        source = MODEL_STATUS_PAGE.read_text(encoding="utf-8")

        self.assertIn("/api/perf-metrics/summary", source)
        self.assertNotIn("/api/channel-monitor", source)

        forbidden_terms = (
            "渠道",
            "上游",
            "毛利",
            "利润",
            "成本",
            "余额",
            "最近错误",
            "upstream",
            "gross_margin",
            "gross_profit",
            "revenue_cny",
            "cost_24h",
            "last_error",
        )
        for term in forbidden_terms:
            with self.subTest(term=term):
                self.assertNotIn(term, source)

    def test_model_status_route_is_available_to_every_signed_in_user(self):
        source = MODEL_STATUS_ROUTE.read_text(encoding="utf-8")

        self.assertNotIn("ROLE.ADMIN", source)
        self.assertNotIn("/403", source)

    def test_time_range_uses_customer_friendly_labels(self):
        source = MODEL_STATUS_PAGE.read_text(encoding="utf-8")

        self.assertIn("<SelectValue>{rangeLabel}</SelectValue>", source)
        self.assertNotIn("近 ${hours} 小时", source)

    def test_sidebar_exposes_one_customer_facing_model_status_entry(self):
        source = SIDEBAR.read_text(encoding="utf-8")

        self.assertEqual(source.count("title: '模型状态'"), 1)
        self.assertEqual(source.count("url: '/channel-health'"), 1)
        self.assertLess(source.index("title: '模型状态'"), source.index("id: 'admin'"))

    def test_internal_monitor_endpoint_remains_root_only(self):
        source = API_ROUTER.read_text(encoding="utf-8")

        self.assertIn(
            'apiRouter.GET("/channel-monitor", middleware.RootAuth(), '
            "controller.GetChannelMonitor)",
            source,
        )

    def test_model_performance_api_does_not_expose_internal_errors(self):
        source = PERF_METRICS_CONTROLLER.read_text(encoding="utf-8")

        self.assertNotIn('"message": err.Error()', source)
        self.assertGreaterEqual(source.count('"message": "模型性能数据暂时不可用"'), 2)

    def test_documented_monitor_materialization_schedule_is_hourly(self):
        source = README.read_text(encoding="utf-8")

        self.assertIn(
            "0 * * * * generate-monitor-data.py (local dashboard materialization)",
            source,
        )
        self.assertNotIn("*/5 * * * * generate-monitor-data.py", source)


if __name__ == "__main__":
    unittest.main()
