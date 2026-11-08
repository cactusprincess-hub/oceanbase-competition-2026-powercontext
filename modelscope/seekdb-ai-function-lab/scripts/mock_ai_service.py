#!/usr/bin/env python3
"""Deterministic local HTTP service for seekdb AI SQL integration tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

MAX_BODY_BYTES = 2 * 1024 * 1024


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _vector(value: Any, dimensions: int) -> list[float]:
    seed = hashlib.sha256(_text(value).encode("utf-8")).digest()
    values = [((seed[index % len(seed)] / 255.0) * 2.0) - 1.0 for index in range(dimensions)]
    norm = math.sqrt(sum(item * item for item in values)) or 1.0
    return [round(item / norm, 8) for item in values]


def _prompt_from_messages(messages: Any) -> str:
    if not isinstance(messages, list):
        return _text(messages)
    parts: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            parts.append(_text(message.get("content", "")))
        else:
            parts.append(_text(message))
    return "\n".join(parts)


def _completion(prompt: str) -> str:
    lowered = prompt.lower()
    if "json" in lowered or "标签" in prompt:
        return json.dumps({"label": "mock", "length": len(prompt)}, ensure_ascii=False, separators=(",", ":"))
    return f"mock:{prompt}"


def _tokens(value: Any) -> set[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in _text(value))
    return {token for token in normalized.split() if token}


class MockState:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.requests: deque[dict[str, Any]] = deque(maxlen=args.max_log_entries)
        self.seen = 0
        self.lock = threading.Lock()

    def record(self, path: str, payload: Any, headers: dict[str, str]) -> int:
        with self.lock:
            self.seen += 1
            request_number = self.seen
            self.requests.append(
                {
                    "number": request_number,
                    "path": path,
                    "headers": {
                        key: value
                        for key, value in headers.items()
                        if key.lower() not in {"authorization", "cookie", "proxy-authorization"}
                    },
                    "body": payload,
                }
            )
            return request_number

    def reset(self) -> None:
        with self.lock:
            self.requests.clear()
            self.seen = 0


class MockServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: MockState) -> None:
        super().__init__(address, MockHandler)
        self.state = state


class MockHandler(BaseHTTPRequestHandler):
    server: MockServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: object) -> None:
        if self.server.state.args.verbose:
            super().log_message(format_string, *args)

    def _send_json(self, status: int, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"request body exceeds {MAX_BODY_BYTES} bytes")
        body = self.rfile.read(length)
        return json.loads(body or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "requests": self.server.state.seen})
        elif path == "/requests":
            self._send_json(HTTPStatus.OK, {"requests": list(self.server.state.requests)})
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown route"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return

        if parsed.path == "/reset":
            self.server.state.reset()
            self._send_json(HTTPStatus.OK, {"ok": True})
            return

        request_number = self.server.state.record(parsed.path, payload, dict(self.headers.items()))
        query = parse_qs(parsed.query)
        delay_ms = int(query.get("delay_ms", [self.server.state.args.delay_ms])[0])
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        forced_status = query.get("status", [None])[0]
        if forced_status is not None:
            self._send_json(int(forced_status), {"error": "query-injected failure"})
            return
        if request_number <= self.server.state.args.fail_first:
            self._send_json(self.server.state.args.failure_status, {"error": "configured transient failure"})
            return

        reverse = query.get("reverse", ["0"])[0].lower() in {"1", "true", "yes"}
        try:
            response = self._route(parsed.path, payload, reverse)
        except (KeyError, TypeError, ValueError) as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        self._send_json(HTTPStatus.OK, response)

    def _route(self, path: str, payload: Any, reverse: bool) -> Any:
        if not isinstance(payload, dict):
            raise TypeError("JSON body must be an object")
        if path.endswith("/chat/completions"):
            prompt = _prompt_from_messages(payload.get("messages", []))
            return {
                "id": "chatcmpl-seekdb-lab",
                "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": _completion(prompt)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": len(prompt), "completion_tokens": 1, "total_tokens": len(prompt) + 1},
            }
        if path.endswith("/embeddings") and not path.startswith("/api/"):
            values = payload.get("input", "")
            inputs = values if isinstance(values, list) else [values]
            dimensions = int(payload.get("dimensions", self.server.state.args.dimensions))
            if dimensions <= 0 or dimensions > 4096:
                raise ValueError("dimensions must be between 1 and 4096")
            data = [
                {"object": "embedding", "index": index, "embedding": _vector(value, dimensions)}
                for index, value in enumerate(inputs)
            ]
            if reverse:
                data.reverse()
            return {"object": "list", "data": data, "model": payload.get("model", "mock-embed")}
        if path.endswith("/rerank"):
            query_tokens = _tokens(payload.get("query", ""))
            documents = payload.get("documents", [])
            if not isinstance(documents, list):
                raise TypeError("documents must be an array")
            results = []
            for index, document in enumerate(documents):
                document_text = document.get("text", "") if isinstance(document, dict) else document
                overlap = len(query_tokens & _tokens(document_text))
                results.append({"index": index, "relevance_score": overlap + (1.0 / (index + 2))})
            results.sort(key=lambda item: (-item["relevance_score"], item["index"]))
            if reverse:
                results.reverse()
            return {"id": "rerank-seekdb-lab", "results": results}
        if path == "/api/generate":
            return {"model": payload.get("model", "mock"), "response": _completion(_text(payload.get("prompt", ""))), "done": True}
        if path == "/api/chat":
            prompt = _prompt_from_messages(payload.get("messages", []))
            return {"model": payload.get("model", "mock"), "message": {"role": "assistant", "content": _completion(prompt)}, "done": True}
        if path == "/api/embed":
            values = payload.get("input", "")
            inputs = values if isinstance(values, list) else [values]
            dimensions = int(payload.get("dimensions", self.server.state.args.dimensions))
            return {"model": payload.get("model", "mock"), "embeddings": [_vector(value, dimensions) for value in inputs]}
        if path == "/api/embeddings":
            dimensions = int(payload.get("dimensions", self.server.state.args.dimensions))
            return {"embedding": _vector(payload.get("prompt", ""), dimensions)}
        raise KeyError(f"unknown route: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="bind host; keep loopback unless remote access is intentional")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dimensions", type=int, default=8, help="default embedding dimensions")
    parser.add_argument("--delay-ms", type=int, default=0, help="delay every inference response")
    parser.add_argument("--fail-first", type=int, default=0, help="fail the first N inference requests")
    parser.add_argument("--failure-status", type=int, default=429)
    parser.add_argument("--max-log-entries", type=int, default=200)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not 1 <= args.dimensions <= 4096:
        parser.error("dimensions must be between 1 and 4096")
    if args.delay_ms < 0 or args.fail_first < 0 or args.max_log_entries < 1:
        parser.error("delay, fail count, and log size must be non-negative (log size at least 1)")
    if not 400 <= args.failure_status <= 599:
        parser.error("failure status must be an HTTP 4xx or 5xx code")
    return args


def main() -> None:
    args = parse_args()
    server = MockServer((args.host, args.port), MockState(args))
    print(f"seekdb AI mock listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
