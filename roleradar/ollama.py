"""A tiny client for Ollama, the app that runs open AI models on your own computer.

Ollama listens on http://127.0.0.1:11434. We only need two of its endpoints:
- GET  /api/tags  which models are downloaded
- POST /api/chat  ask a model a question

Python's built-in urllib is enough, so no extra package is needed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class OllamaError(Exception):
    """Ollama is not running, the model is missing, or the answer was unusable."""


class OllamaClient:
    def __init__(self, base_url: str, timeout: int = 300) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"Content-Type": "application/json"} if data else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise OllamaError(f"Ollama answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, ConnectionError) as exc:
            raise OllamaError(
                "Ollama is not running. Open the Ollama app (or run 'ollama serve') and try again."
            ) from exc
        except TimeoutError as exc:
            raise OllamaError(f"Ollama took longer than {timeout or self.timeout} seconds") from exc

    def models(self) -> list[str]:
        return [m["name"] for m in self._request("/api/tags", timeout=5).get("models", [])]

    def has_model(self, model: str) -> bool:
        names = self.models()
        # "llama3.1" means "llama3.1:latest"; "llama3.1:8b" must match exactly.
        return model in names or f"{model}:latest" in names

    def chat_json(self, model: str, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Ask the model and get back JSON that follows `schema`.

        Passing the schema as `format` makes Ollama constrain the model's output
        to valid JSON of that shape (structured outputs), so we never have to
        dig an answer out of free text.
        """
        reply = self._request("/api/chat", {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "format": schema,
            "stream": False,
            # temperature 0: the same job gives the same answer every time.
            "options": {"temperature": 0, "num_ctx": 4096},
        })
        content = reply.get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"The model did not return JSON: {content[:200]}") from exc
