"""Socket.IO coordinator client for collectors."""

from __future__ import annotations

import threading
import time
from typing import Any


class CoordinatorClient:
    """Register a collector and send heartbeats to the coordinator."""

    def __init__(
        self,
        coordinator_url: str,
        *,
        collector_id: str,
        capabilities: list[str],
        metadata: dict[str, Any],
        protocol_version: int = 1,
        socket_client: Any | None = None,
    ) -> None:
        self.coordinator_url = coordinator_url
        self.collector_id = collector_id
        self.capabilities = capabilities
        self.metadata = metadata
        self.protocol_version = protocol_version
        self.socket_client = socket_client or self._make_socket_client()
        self.work_dir: str | None = None
        self.connected = False
        self._stop = threading.Event()
        self._heartbeat_thread: threading.Thread | None = None
        self.paused = False
        self.config: dict[str, Any] = {}
        self._install_handlers()

    def _make_socket_client(self) -> Any:
        try:
            import socketio
        except ImportError as exc:  # pragma: no cover - depends on environment.
            raise RuntimeError(
                "python-socketio is required for coordinator registration"
            ) from exc
        return socketio.Client()

    def _install_handlers(self) -> None:
        if not hasattr(self.socket_client, "on"):
            return

        @self.socket_client.on("coordinator:collector_config")
        def handle_config(payload: dict[str, Any] | None = None) -> None:
            self.config.update(payload or {})

        @self.socket_client.on("coordinator:collector_pause")
        def handle_pause(payload: dict[str, Any] | None = None) -> None:
            if self._message_applies(payload):
                self.paused = True

        @self.socket_client.on("coordinator:collector_resume")
        def handle_resume(payload: dict[str, Any] | None = None) -> None:
            if self._message_applies(payload):
                self.paused = False

    def _message_applies(self, payload: dict[str, Any] | None) -> bool:
        if not payload:
            return True
        target = payload.get("collector_id")
        return target in (None, self.collector_id)

    def connect_and_register(
        self,
        *,
        connect_timeout_seconds: float = 10.0,
        call_timeout_seconds: float = 5.0,
    ) -> str:
        deadline = time.monotonic() + max(connect_timeout_seconds, 0)
        last_error: Exception | None = None
        while True:
            try:
                self.socket_client.connect(self.coordinator_url)
                break
            except Exception as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    raise RuntimeError(
                        f"could not connect to coordinator: {last_error}"
                    ) from exc
                time.sleep(0.25)
        self.connected = True
        payload = {
            "collector_id": self.collector_id,
            "protocol_version": self.protocol_version,
            "capabilities": self.capabilities,
            "metadata": self.metadata,
        }
        ack = self.socket_client.call(
            "collector:register",
            payload,
            timeout=call_timeout_seconds,
        )
        if not isinstance(ack, dict) or not ack.get("ok"):
            raise RuntimeError(f"collector registration failed: {ack!r}")
        work_dir = ack.get("work_dir")
        if not isinstance(work_dir, str) or not work_dir:
            raise RuntimeError(f"collector registration missing work_dir: {ack!r}")
        self.work_dir = work_dir
        return work_dir

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.connected:
            return None
        return self.socket_client.call("collector:heartbeat", payload, timeout=2)

    def start_heartbeat_loop(
        self,
        status_provider: Any,
        interval_seconds: float = 5.0,
    ) -> None:
        if self._heartbeat_thread is not None:
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(status_provider, interval_seconds),
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _heartbeat_loop(self, status_provider: Any, interval_seconds: float) -> None:
        while not self._stop.is_set():
            try:
                self.heartbeat(status_provider())
            except Exception:
                pass
            self._stop.wait(interval_seconds)

    def disconnect(self) -> None:
        self._stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)
        if self.connected:
            self.socket_client.disconnect()
            self.connected = False
        time.sleep(0)
