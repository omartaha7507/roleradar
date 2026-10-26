"""The live check against a small pretend job site."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from roleradar.livecheck import check

PAGES = {
    "/open": (200, "<h1>Junior AI Engineer</h1><button>Apply</button>"),
    "/closed": (200, "<div>No longer accepting applications</div>"),
    "/geschlossen": (200, "<p>Diese Stelle ist nicht mehr verfügbar.</p>"),
    "/gone": (404, "Not found"),
    "/blocked": (999, "Request denied"),  # LinkedIn's "go away" code
}


class Site(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        code, body = PAGES[self.path]
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize("path, status", [
    ("/open", "open"), ("/closed", "closed"), ("/geschlossen", "closed"),
    ("/gone", "closed"), ("/blocked", "unknown"),
])
def test_live_check(site, path, status):
    assert check(site + path)[0] == status


def test_unreachable_is_unknown():
    assert check("http://127.0.0.1:9/job", timeout=2)[0] == "unknown"
