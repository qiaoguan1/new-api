# Quickstart

Run `python -m unittest discover -s ops/video-job-gateway/tests -p "test_*.py"`.

Verify v2.1 rejects all reference-video/audio aliases. Verify v2.2 returns a pre-freeze unavailable error and leaves the job table empty. Verify capabilities and prices publish unsupported profiles. Do not send a paid upstream request.
