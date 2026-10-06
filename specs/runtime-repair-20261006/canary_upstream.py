"""Private-network fake upstream for non-billable native retry/refund tests."""
import base64
import json
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

counts = Counter()
PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        return

    def send(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        # Malicious/conflicting upstream header must never replace native ID.
        self.send_header('X-XingTu-Relay-Request-ID', 'fake-provider-id')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send(200, dict(counts))

    def do_POST(self):
        route = self.path.split('/')[1]
        counts[route] += 1
        self.rfile.read(int(self.headers.get('Content-Length', '0')))
        if self.path.endswith('/v1/responses'):
            return self.send(200, {'id': 'resp_local_cache_fixture', 'object': 'response', 'model': 'gpt-6.1-sol', 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'OK'}]}], 'usage': {'input_tokens': 1000, 'output_tokens': 10, 'total_tokens': 1010, 'input_tokens_details': {'cached_tokens': 200, 'cache_write_tokens': 300, 'cached_creation_tokens': 300}}})
        if route == 'uncertain':
            return self.send(504, {'error': {'message': 'generation transport timed out', 'type': 'upstream_error', 'code': 'unknown_generation_outcome'}})
        if route == 'quota':
            return self.send(429, {'error': {'message': 'no available image quota', 'type': 'upstream_error', 'code': 'no_image_quota'}})
        if route == 'unsupported':
            return self.send(429, {'error': {'message': 'endpoint_requires_other_route', 'type': 'upstream_error', 'code': 'endpoint_requires_other_route'}})
        self.send(200, {'created': 1, 'data': [{'b64_json': PNG}]})


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 19000), Handler).serve_forever()
