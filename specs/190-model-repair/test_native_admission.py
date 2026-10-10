"""Deterministic ingress contract tests; no real request or deployment."""
import unittest
from native_admission import HOST_ANCHOR, build_gate, is_paused


class AdmissionTests(unittest.TestCase):
    def test_native_paid_and_write_surfaces_are_paused(self):
        paths = ["/v1/images/generations", "/v1/images/edits", "/v1/chat/completions", "/v1/embeddings", "/v1/audio/speech", "/v1/models/gemini:generateContent", "/v1beta/models/gemini:generateContent", "/mj/submit/video", "/fast/mj/submit/imagine", "/suno/submit/music", "/pg/chat/completions", "/api/channel/test", "/api/user/topup", "/v1/videos", "/v1/video-jobs"]
        for method in ["POST", "PUT", "PATCH", "DELETE"]:
            for path in paths:
                with self.subTest(method=method, path=path):
                    self.assertTrue(is_paused(method, path))

    def test_get_side_effects_and_realtime_are_paused(self):
        paths = ["/v1/realtime", "/api/channel/test/1", "/api/channel/update_balance", "/api/user/token", "/api/user/epay/notify", "/api/subscription/epay/notify", "/api/oauth/github", "/api/models/sync_upstream/preview"]
        for path in paths:
            self.assertTrue(is_paused("GET", path), path)
            self.assertTrue(is_paused("HEAD", path), path)

    def test_existing_task_reads_and_sidecar_settlement_stay_open(self):
        paths = ["/v1/videos/vjob_" + "a" * 32, "/v1/videos/vjob_" + "a" * 32 + "/content", "/internal/xtai-image-jobs/v1/image-jobs/id", "/internal/xtai-video-jobs/v1/video-jobs/id", "/api/status", "/api/user/self", "/v1/models", "/v1/capabilities", "/api/pricing"]
        for method in ["GET", "HEAD"]:
            for path in paths:
                self.assertFalse(is_paused(method, path), (method, path))
        self.assertFalse(is_paused("POST", "/internal/xtai-video-jobs/v1/operations/video-settlements"))
        self.assertFalse(is_paused("POST", "/internal/xtai-video-jobs/callback"))
        self.assertFalse(is_paused("POST", "/internal/xtai-image-jobs/v1/image-jobs/existing/verify-delivery"))

    def test_internal_creates_are_paused(self):
        for path in ["/internal/xtai-image-jobs/v1/image-jobs", "/internal/xtai-video-jobs/v1/video-jobs", "/internal/xtai-video-jobs/v1/videos"]:
            self.assertTrue(is_paused("POST", path))

    def test_unrelated_server_bytes_preserved(self):
        original = b"server {\n" + HOST_ANCHOR + b"    location / { proxy_pass http://new-api:3000; }\n}\nserver { server_name other.example; }\n"
        result = build_gate(original, "a" * 32)
        self.assertIn(b"server { server_name other.example; }", result)
        self.assertIn(b"error_page 418 = @issue190_native_pause;", result)
        self.assertEqual(result.count(b"proxy_pass http://new-api:3000;"), 1)

    def test_ambiguous_or_unreviewed_other_native_ingress_rejected(self):
        original = b"server {\n" + HOST_ANCHOR + b"proxy_pass http://new-api:3000;\n}\n"
        for bad in [original + original, original + b"proxy_pass http://new-api:3000;", original + b"# 18090", original.replace(HOST_ANCHOR, b"server_name wrong;")]:
            with self.assertRaises(ValueError):
                build_gate(bad, "a" * 32)


if __name__ == "__main__":
    unittest.main()
