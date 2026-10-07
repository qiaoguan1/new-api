"""Private non-paying native Claude fixture for protocol and settlement checks."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


RECORDS: list[dict] = []
LOCK = threading.Lock()


def usage_for_case(case: str) -> dict:
    """Return declared test vectors, never customer or upstream account data."""
    result = {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 200,
              "cache_creation_input_tokens": 80}
    if "total-only" in case:
        return result
    if "5m" in case:
        split = {"ephemeral_5m_input_tokens": 80, "ephemeral_1h_input_tokens": 0}
    elif "1h" in case:
        split = {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 80}
    else:
        split = {"ephemeral_5m_input_tokens": 50, "ephemeral_1h_input_tokens": 30}
    result["cache_creation"] = split
    return result


class Handler(BaseHTTPRequestHandler):
    """Capture only the fixture body, excluding all authorization headers."""

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:
        with LOCK:
            data = json.dumps({"posts": len(RECORDS), "records": RECORDS}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 100000:
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(size))
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
        case = text.split("FABLE_CASE:", 1)[-1].split('"', 1)[0] if "FABLE_CASE:" in text else "mixed"
        with LOCK:
            RECORDS.append({"path": self.path, "model": body.get("model"),
                            "thinking": body.get("thinking"), "output_config": body.get("output_config"),
                            "case": case, "stream": bool(body.get("stream"))})
        usage = usage_for_case(case)
        message = {"id": "msg_fable187_fixture", "type": "message", "role": "assistant",
                   "model": body.get("model"), "content": [{"type": "text", "text": "OK"}],
                   "stop_reason": "end_turn", "stop_sequence": None, "usage": usage}
        if not body.get("stream"):
            data = json.dumps(message).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        start = dict(message, content=[], stop_reason=None, usage=dict(usage, output_tokens=0))
        events = [
            ("message_start", {"type": "message_start", "message": start}),
            ("content_block_start", {"type": "content_block_start", "index": 0,
                                     "content_block": {"type": "text", "text": ""}}),
            ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                     "delta": {"type": "text_delta", "text": "OK"}}),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                               "usage": {"output_tokens": 100}}),
            ("message_stop", {"type": "message_stop"}),
        ]
        for event, data in events:
            self.wfile.write(("event: " + event + "\ndata: " + json.dumps(data) + "\n\n").encode())
            self.wfile.flush()


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 19087), Handler).serve_forever()
