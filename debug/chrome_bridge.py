from __future__ import annotations

import json
import threading
import time
from typing import Any

import eventlet
from eventlet import wsgi
from eventlet.websocket import WebSocketWSGI


BRIDGE_WS_PATH = "/radar-chrome-bridge-ws"


class ChromeBridgeServer:
    """Minimal JSON-RPC host for the Wingman/Radar Chrome bridge extension."""

    def __init__(self, *, host: str = "127.0.0.1", port: int = 9223) -> None:
        self.host = host
        self.port = port
        self._ws: Any | None = None
        self._server_thread: Any | None = None
        self._next_id = 1
        self._lock = threading.RLock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._connected_at: int | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._server_thread is not None:
            return
        self._server_thread = eventlet.spawn(self._serve)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._ws is not None,
            "host": self.host,
            "port": self.port,
            "path": BRIDGE_WS_PATH,
            "connected_at": self._connected_at,
            "last_error": self._last_error,
        }

    def invoke(self, method: str, params: Any | None = None, *, timeout_seconds: float = 10.0) -> Any:
        with self._lock:
            if self._ws is None:
                raise RuntimeError("chrome bridge is not connected")
            request_id = self._next_id
            self._next_id += 1
            condition = threading.Condition()
            self._pending[request_id] = {
                "condition": condition,
                "response": None,
            }
            message = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params
            self._ws.send(json.dumps(message, separators=(",", ":")))

        deadline = time.monotonic() + timeout_seconds
        with condition:
            while True:
                pending = self._pending.get(request_id)
                response = pending.get("response") if pending else None
                if response is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    with self._lock:
                        self._pending.pop(request_id, None)
                    raise TimeoutError(f"chrome bridge request timed out: {method}")
                condition.wait(remaining)

        with self._lock:
            self._pending.pop(request_id, None)

        if "error" in response:
            error = response["error"]
            if isinstance(error, dict):
                raise RuntimeError(error.get("message", str(error)))
            raise RuntimeError(str(error))
        return response.get("result")

    def _serve(self) -> None:
        listener = eventlet.listen((self.host, self.port))
        wsgi.server(listener, self._dispatch, log_output=False)

    def _dispatch(self, environ: dict[str, Any], start_response: Any) -> Any:
        path = environ.get("PATH_INFO", "")
        if path == BRIDGE_WS_PATH:
            return WebSocketWSGI(self._handle_ws)(environ, start_response)
        if path == "/health":
            body = json.dumps({"ok": True, **self.status()}).encode("utf-8")
            start_response(
                "200 OK",
                [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(body))),
                ],
            )
            return [body]
        body = b"not found"
        start_response(
            "404 Not Found",
            [
                ("Content-Type", "text/plain"),
                ("Content-Length", str(len(body))),
            ],
        )
        return [body]

    def _handle_ws(self, ws: Any) -> None:
        with self._lock:
            self._ws = ws
            self._connected_at = int(time.time() * 1000)
            self._last_error = None
        try:
            while True:
                raw = ws.wait()
                if raw is None:
                    break
                self._handle_message(raw)
        except Exception as exc:  # pragma: no cover - defensive runtime guard.
            self._last_error = str(exc)
        finally:
            with self._lock:
                if self._ws is ws:
                    self._ws = None
                for pending in self._pending.values():
                    pending["response"] = {
                        "error": {
                            "message": "chrome bridge disconnected",
                        }
                    }
                    with pending["condition"]:
                        pending["condition"].notify_all()

    def _handle_message(self, raw: str) -> None:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            return

        if "id" in message and ("result" in message or "error" in message):
            request_id = message["id"]
            with self._lock:
                pending = self._pending.get(request_id)
            if pending:
                with pending["condition"]:
                    pending["response"] = message
                    pending["condition"].notify_all()
            return

        if "id" in message and "method" in message:
            self._send_response(message["id"], {"ok": False, "error": "unsupported request"})

    def _send_response(self, request_id: int, result: Any) -> None:
        with self._lock:
            ws = self._ws
        if ws is None:
            return
        ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": result,
                },
                separators=(",", ":"),
            )
        )
