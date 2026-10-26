"""The real Ollama HTTP client against a small pretend Ollama server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from roleradar.ollama import OllamaClient, OllamaError


class PretendOllama(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"models": [{"name": "llama3.1:8b"}, {"name": "llama3.2:latest"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        PretendOllama.seen.append(body)
        if body["model"] == "missing":
            self._send({"error": "model 'missing' not found"}, 404)
            return
        self._send({"message": {"role": "assistant", "content": json.dumps({"fit": 77})}})


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), PretendOllama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_models_and_has_model(server):
    client = OllamaClient(server)
    assert client.has_model("llama3.1:8b") and client.has_model("llama3.2")
    assert not client.has_model("mistral")


def test_chat_json_sends_schema_and_parses(server):
    schema = {"type": "object", "properties": {"fit": {"type": "integer"}}}
    answer = OllamaClient(server).chat_json("llama3.1:8b", "sys", "user", schema)
    sent = PretendOllama.seen[-1]
    assert answer == {"fit": 77}
    assert sent["format"] == schema and sent["stream"] is False and sent["options"]["temperature"] == 0
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]


def test_errors_are_readable(server):
    with pytest.raises(OllamaError, match="404"):
        OllamaClient(server).chat_json("missing", "s", "u", {})
    with pytest.raises(OllamaError, match="not running"):
        OllamaClient("http://127.0.0.1:9").models()
