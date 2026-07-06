from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app_core.event_bus import EventBus
from app_core.runtime_models import DependencyHealth, RuntimeSnapshot
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
        return self._state

    def restart_runtime(self, mode: str | None = None) -> RuntimeSnapshot:
        return self.start_runtime(mode or self._state.runtime_mode)

    def set_runtime_mode(self, mode: str) -> None:
        self._state = RuntimeSnapshot(mode=self._state.mode, runtime_mode=mode, status_line=self._state.status_line)

def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def wait_for_job(client: TestClient, job_id: str) -> dict:
    deadline = time.time() + 3.0
    while time.time() < deadline:
        response = client.get(f"/v1/pdf-audiobook/jobs/{job_id}", headers=auth_headers())
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} did not settle in time")


def write_text_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page = writer.add_blank_page(width=612, height=792)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("utf-8"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def test_pdf_job_create_validates_required_fields(tmp_path: Path) -> None:
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    response = client.post("/v1/pdf-audiobook/jobs", headers=auth_headers(), json={"pdf_path": ""})

    assert response.status_code == 422


def test_pdf_job_dry_run_completes_for_text_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "book.pdf"
    output_dir = tmp_path / "exports"
    write_text_pdf(pdf_path, "Chapter 1 Hello from Lulu PDF service.")
    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)

    create = client.post(
        "/v1/pdf-audiobook/jobs",
        headers=auth_headers(),
        json={
            "pdf_path": str(pdf_path),
            "output_dir": str(output_dir),
            "dry_run": True,
            "portable_format": "none",
        },
    )

    assert create.status_code == 200
    payload = wait_for_job(client, create.json()["job_id"])
    assert payload["status"] == "completed"
    assert payload["dry_run"] is True
    assert payload["section_count"] >= 1
    assert payload["manifest_path"]


def test_pdf_job_reports_failure_for_image_only_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    output_dir = tmp_path / "exports"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    app = build_service_app(FakeController(tmp_path), launch_token="test-token", enforce_loopback=False)
    client = TestClient(app)
    create = client.post(
        "/v1/pdf-audiobook/jobs",
        headers=auth_headers(),
        json={
            "pdf_path": str(pdf_path),
            "output_dir": str(output_dir),
            "dry_run": True,
            "portable_format": "none",
        },
    )

    assert create.status_code == 200
    payload = wait_for_job(client, create.json()["job_id"])
    assert payload["status"] == "failed"
    assert "OCR support is deferred" in payload["error"]
