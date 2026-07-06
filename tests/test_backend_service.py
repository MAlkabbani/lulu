from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app_core.event_bus import EventBus
from app_core.runtime_models import DependencyHealth, RuntimeSnapshot, make_event
from backend_service.api_app import build_service_app
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
        self._state = RuntimeSnapshot(mode="ready", runtime_mode="continuous", status_line="Ready", degraded=False)

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
        self._state = RuntimeSnapshot(mode="ready", runtime_mode=mode, status_line=f"Runtime started in {mode} mode.")
        return self._state

    def stop_runtime(self) -> RuntimeSnapshot:
        self._state = RuntimeSnapshot(mode="idle", runtime_mode=self._state.runtime_mode, status_line="Runtime stopped.")
        return self._state

    def restart_runtime(self, mode: str | None = None) -> RuntimeSnapshot:
        return self.start_runtime(mode or self._state.runtime_mode)

    def set_runtime_mode(self, mode: str) -> None:
        self._state = RuntimeSnapshot(mode=self._state.mode, runtime_mode=mode, status_line=self._state.status_line)

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
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 401


def test_healthz_returns_ready_status_with_auth(tmp_path: Path) -> None:
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.get("/healthz", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["service"] == "lulu-backend"


def test_dependencies_endpoint_returns_health_payload(tmp_path: Path) -> None:
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.get("/v1/dependencies", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["ollama_reachable"] is True
    assert body["chat_model_available"] is True


def test_mode_endpoint_updates_runtime_mode(tmp_path: Path) -> None:
    controller = FakeController(tmp_path)
    app = build_service_app(controller, launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.post("/v1/mode", headers=auth_headers(), json={"mode": "turn-based"})

    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "turn-based"


def test_runtime_start_rejects_removed_text_mode(tmp_path: Path) -> None:
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.post("/v1/runtime/start", headers=auth_headers(), json={"mode": "text"})

    assert response.status_code == 422


def test_runtime_diagnostics_endpoint_returns_snapshot_payload(tmp_path: Path) -> None:
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.get("/v1/runtime/diagnostics", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["runtime_mode"] == "continuous"
    assert body["memory_hit_count"] == 1
    assert body["latencies_ms"]["total"] == 123.0
    assert body["recent_saves"] == ["remembered birthday"]


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
