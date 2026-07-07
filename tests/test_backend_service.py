from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app_core.event_bus import EventBus
from app_core.runtime_models import DependencyHealth, RuntimeSnapshot, make_event
from backend_service.api_app import build_service_app
from backend_service.websocket_events import (
    EVENT_QUEUE_MAX_SIZE,
    MAX_DROPPED_EVENTS,
    WebSocketEventBridge,
)
from config import Settings


class FakeController:
    def __init__(self, tmp_path: Path) -> None:
        self.event_bus = EventBus()
        self.settings = Settings(
            config_path=tmp_path / "config.json",
            chroma_path=tmp_path / "vault_db",
            logs_path=tmp_path / "logs",
            exports_path=tmp_path / "exports",
        )
        self._state = RuntimeSnapshot(
            mode="ready",
            runtime_mode="continuous",
            status_line="Ready",
            degraded=False,
        )

    def get_state(self) -> RuntimeSnapshot:
        return self._state

    def current_dependency_health(self) -> DependencyHealth:
        return DependencyHealth(
            ollama_reachable=True,
            ollama_version="0.3.0",
            chat_model_available=True,
            embedding_model_available=True,
            issues=[],
        )

    def start_runtime(self, mode: str) -> RuntimeSnapshot:
        self._state = RuntimeSnapshot(
            mode="ready",
            runtime_mode=mode,
            status_line=f"Runtime started in {mode} mode.",
        )
        return self._state

    def stop_runtime(self) -> RuntimeSnapshot:
        self._state = RuntimeSnapshot(
            mode="idle",
            runtime_mode=self._state.runtime_mode,
            status_line="Runtime stopped.",
        )
        return self._state

    def restart_runtime(self, mode: str | None = None) -> RuntimeSnapshot:
        return self.start_runtime(mode or self._state.runtime_mode)

    def set_runtime_mode(self, mode: str) -> None:
        self._state = RuntimeSnapshot(
            mode=self._state.mode,
            runtime_mode=mode,
            status_line=self._state.status_line,
        )

    def get_diagnostics(self) -> dict[str, object]:
        return {
            "mode": self._state.mode,
            "runtime_mode": self._state.runtime_mode,
            "status_line": self._state.status_line,
            "last_error": self._state.last_error,
            "runtime_active": False,
            "transcript": "hello",
            "response": "Echo: hello",
            "invocation_summary": "No backend action requested.",
            "action_summary": "No backend tool used.",
            "current_tool_status": "idle",
            "memory_hit_count": 1,
            "emitted_chunk_count": 2,
            "spoken_chunk_count": 2,
            "emitted_char_count": 12,
            "spoken_char_count": 12,
            "last_emitted_chunk": "Hello",
            "last_spoken_chunk": "Hello",
            "playback_gap_count": 0,
            "tail_merge_count": 0,
            "recent_saves": ["remembered birthday"],
            "recent_events": ["Voice runtime ready."],
            "recent_wake_attempts": ["accepted: hey lulu"],
            "latencies_ms": {"total": 123.0},
            "conversation_window_remaining": None,
            "cooldown_remaining": None,
            "wake_guidance": "Say the wake phrase clearly.",
            "last_wake_score": 0.91,
            "last_wake_decision": "Accepted wake attempt.",
            "wake_score_threshold": 0.68,
            "accepted_wake_attempts": 1,
            "rejected_wake_attempts": 0,
            "last_wake_confidence": 0.88,
            "last_wake_acoustic_score": 0.86,
            "last_wake_dtw_score": 0.82,
            "last_wake_snr_db": 17.2,
            "last_wake_feature_frames": 24,
        }


def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def test_healthz_requires_auth(tmp_path: Path) -> None:
    app = build_service_app(
        FakeController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 401


def test_healthz_returns_ready_status_with_auth(tmp_path: Path) -> None:
    app = build_service_app(
        FakeController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.get("/healthz", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["service"] == "lulu-backend"


def test_dependencies_endpoint_returns_health_payload(tmp_path: Path) -> None:
    app = build_service_app(
        FakeController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.get("/v1/dependencies", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["ollama_reachable"] is True
    assert body["chat_model_available"] is True
    assert "ffmpeg_available" in body


def test_mode_endpoint_updates_runtime_mode(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.post("/v1/mode", headers=auth_headers(), json={"mode": "turn-based"})

    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "turn-based"


def test_runtime_start_rejects_removed_text_mode(tmp_path: Path) -> None:
    app = build_service_app(
        FakeController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.post("/v1/runtime/start", headers=auth_headers(), json={"mode": "text"})

    assert response.status_code == 422


def test_runtime_diagnostics_endpoint_returns_snapshot_payload(tmp_path: Path) -> None:
    app = build_service_app(
        FakeController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.get("/v1/runtime/diagnostics", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_mode"] == "continuous"
    assert body["memory_hit_count"] == 1
    assert body["latencies_ms"]["total"] == 123.0
    assert body["recent_saves"] == ["remembered birthday"]


def test_runtime_restart_returns_visible_runtime_error_state(tmp_path: Path) -> None:
    class StuckController(FakeController):
        def restart_runtime(self, mode: str | None = None) -> RuntimeSnapshot:
            runtime_mode = mode or self._state.runtime_mode
            self._state = RuntimeSnapshot(
                mode="runtime_error",
                runtime_mode=runtime_mode,
                status_line="Runtime stop timed out; background voice worker is still running.",
                degraded=True,
                last_error="Runtime stop timed out; background voice worker is still running.",
            )
            return self._state

    app = build_service_app(
        StuckController(tmp_path),
        launch_token="test-token",
        enforce_loopback=False,
    )
    client = TestClient(app)

    response = client.post(
        "/v1/runtime/restart",
        headers=auth_headers(),
        json={"mode": "continuous"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "runtime_error"
    assert body["degraded"] is True
    assert "still running" in body["last_error"]


def test_websocket_stream_serializes_runtime_events(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    with client.websocket_connect("/v1/events/ws", headers=auth_headers()) as websocket:
        connected = websocket.receive_json()
        controller.event_bus.publish(make_event("response.final", text="Hello from event bus"))
        event = websocket.receive_json()

    assert connected["event_type"] == "service.connected"
    assert event["api_version"] == "v1"
    assert event["event_type"] == "response.final"
    assert event["payload"]["text"] == "Hello from event bus"


def test_websocket_stream_disconnects_slow_consumers(tmp_path: Path) -> None:
    async def exercise() -> list[str]:
        controller = FakeController(tmp_path)
        bridge = WebSocketEventBridge(controller.event_bus)
        sent_event_types: list[str] = []
        connected = asyncio.Event()
        release_send = asyncio.Event()

        async def send_json(payload: dict[str, object]) -> None:
            sent_event_types.append(str(payload["event_type"]))
            if payload["event_type"] == "service.connected":
                connected.set()
                return
            await release_send.wait()

        task = asyncio.create_task(bridge.stream(send_json=send_json))
        await asyncio.wait_for(connected.wait(), timeout=1.0)

        publish_count = EVENT_QUEUE_MAX_SIZE + MAX_DROPPED_EVENTS + 1
        for index in range(publish_count):
            controller.event_bus.publish(
                make_event("response.partial", text=f"chunk-{index}")
            )

        for _ in range(10):
            await asyncio.sleep(0)

        release_send.set()
        await asyncio.wait_for(task, timeout=1.0)
        return sent_event_types

    observed_types = asyncio.run(exercise())

    assert "service.connected" in observed_types
    assert "service.overload" in observed_types


def test_websocket_query_token_is_rejected(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/events/ws?token=test-token"):
            pass


def test_update_settings_persists_json_overlay(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.put(
        "/v1/settings",
        headers=auth_headers(),
        json={"chat_model": "llama3.2:1b", "practical_voice_mode": False},
    )

    assert response.status_code == 200
    assert controller.settings.config_path.exists()
    persisted = controller.settings.config_path.read_text(encoding="utf-8")
    assert '"chat_model": "llama3.2:1b"' in persisted
    assert '"practical_voice_mode": false' in persisted


def test_update_settings_rejects_malformed_existing_json(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    controller.settings.config_path.write_text("{bad json", encoding="utf-8")
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.put(
        "/v1/settings",
        headers=auth_headers(),
        json={"chat_model": "llama3.2:1b"},
    )

    assert response.status_code == 409
    assert controller.settings.config_path.read_text(encoding="utf-8") == "{bad json"


def test_update_settings_preserves_existing_keys_and_creates_backup(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    controller.settings.config_path.write_text(
        '{"chat_model": "old-model", "wake_phrase": "hey lulu"}',
        encoding="utf-8",
    )
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.put(
        "/v1/settings",
        headers=auth_headers(),
        json={"chat_model": "new-model"},
    )

    assert response.status_code == 200
    persisted = json.loads(controller.settings.config_path.read_text(encoding="utf-8"))
    assert persisted["chat_model"] == "new-model"
    assert persisted["wake_phrase"] == "hey lulu"
    backup_path = (
        controller.settings.config_path.parent / f"{controller.settings.config_path.name}.bak"
    )
    assert backup_path.exists()


def test_update_settings_normalizes_exports_path(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)
    export_root = tmp_path / "custom-exports"

    response = client.put(
        "/v1/settings",
        headers=auth_headers(),
        json={"exports_path": str(export_root)},
    )

    assert response.status_code == 200
    persisted = json.loads(controller.settings.config_path.read_text(encoding="utf-8"))
    assert persisted["exports_path"] == str(export_root.resolve())
