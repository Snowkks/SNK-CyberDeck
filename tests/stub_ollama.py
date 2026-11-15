"""A tiny fake Ollama server for tests.

It speaks the same shape as the real API: GET /api/tags and a streamed
POST /api/generate, so the client code is exercised over real HTTP
without a model or a network.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class StubOllama:
    def __init__(
        self,
        models: tuple[str, ...] = ("qwen2.5:1.5b",),
        chunks: tuple[str, ...] = ("Hello ", "there."),
        generate_status: int = 200,
    ) -> None:
        self.models = list(models)
        self.chunks = list(chunks)
        self.generate_status = generate_status
        self.generate_requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def do_GET(self):
                if self.path != "/api/tags":
                    self.send_response(404)
                    self.end_headers()
                    return
                body = json.dumps({"models": [{"name": n} for n in stub.models]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                stub.generate_requests.append(json.loads(self.rfile.read(length)))
                if stub.generate_status != 200:
                    self.send_response(stub.generate_status)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                try:
                    for chunk in stub.chunks:
                        line = json.dumps({"response": chunk, "done": False}) + "\n"
                        self.wfile.write(line.encode())
                        self.wfile.flush()
                    self.wfile.write((json.dumps({"response": "", "done": True}) + "\n").encode())
                    self.wfile.flush()
                except BrokenPipeError:  # client cancelled mid-stream
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "StubOllama":
        self._thread.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.server.shutdown()
        self.server.server_close()
