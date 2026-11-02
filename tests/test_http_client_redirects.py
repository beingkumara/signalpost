"""Every redirect hop is an outbound request and is counted against the budget."""

import http.server
import threading

from signalpost.http_client import HttpClient


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/a":
            self.send_response(302)
            self.send_header("Location", "/b")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # noqa: ANN002
        pass


def test_redirect_hops_are_counted():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = HttpClient(budget=10)
        resp = client.get(f"http://127.0.0.1:{server.server_port}/a", accept="text/plain", retries=0)
        assert resp.status == 200 and resp.final_url.endswith("/b")
        assert client.used == 2
    finally:
        server.shutdown()
