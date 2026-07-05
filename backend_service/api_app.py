from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from app_core.runtime_controller import RuntimeController
from backend_service.api_models import (
    AcceptedResponse,
    DependencyHealthResponse,
    HealthResponse,
    ModeRequest,
    PDFJobRequest,
    PDFJobResponse,
    RuntimeControlRequest,
    RuntimeDiagnosticsResponse,
    RuntimeStateResponse,
    SettingsResponse,
    SettingsUpdateRequest,
    SettingsUpdateResponse,
    TextTurnRequest,
)
from backend_service.auth import require_http_auth, require_websocket_auth
from backend_service.websocket_events import WebSocketEventBridge
from pdf_audiobook import ServiceJobResult, run_service_job


PdfJobRunner = Callable[[str, PDFJobRequest, Callable[[str], None]], ServiceJobResult]


class PdfJobStore:
    def __init__(self, runner: PdfJobRunner) -> None:
        self._runner = runner
        self._jobs: dict[str, PDFJobResponse] = {}
        self._lock = threading.Lock()

    def create_job(self, request: PDFJobRequest) -> PDFJobResponse:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = PDFJobResponse(job_id=job_id, status="pending", dry_run=request.dry_run)
        worker = threading.Thread(target=self._run_job, args=(job_id, request), daemon=True)
        worker.start()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> PDFJobResponse:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return job

    def _run_job(self, job_id: str, request: PDFJobRequest) -> None:
        progress_lines: list[str] = []

        def progress(message: str) -> None:
            progress_lines.append(message)
            with self._lock:
                current = self._jobs[job_id]
                self._jobs[job_id] = current.model_copy(update={"progress": list(progress_lines)})

        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = current.model_copy(update={"status": "running"})

        result = self._runner(job_id, request, progress)
        with self._lock:
            self._jobs[job_id] = PDFJobResponse(
                job_id=result.job_id,
                status=result.status,
                dry_run=result.dry_run,
                output_dir=str(result.output_dir) if result.output_dir else None,
                manifest_path=str(result.manifest_path) if result.manifest_path else None,
                error=result.error,
                section_count=result.section_count,
                progress=list(progress_lines),
            )


def default_pdf_job_runner(
    job_id: str,
    request: PDFJobRequest,
    progress: Callable[[str], None],
) -> ServiceJobResult:
    result, _ = run_service_job(
        job_id=job_id,
        input_pdf=Path(request.pdf_path).expanduser(),
        output_dir=Path(request.output_dir).expanduser(),
        title=request.title,
        author=request.author,
        genre=request.genre,
        chapter_splitting=request.chapter_splitting,
        dry_run=request.dry_run,
        portable_format=request.portable_format,
        preview_chars=request.preview_chars,
        pronunciation_file=Path(request.pronunciation_file).expanduser()
        if request.pronunciation_file
        else None,
        progress=progress,
    )
    return result


def build_service_app(
    controller: RuntimeController,
    *,
    launch_token: str,
    enforce_loopback: bool = True,
    pdf_job_runner: PdfJobRunner | None = None,
) -> FastAPI:
    app = FastAPI(title="Lulu Backend Service", version="1.0.0")
    ws_bridge = WebSocketEventBridge(controller.event_bus)
    job_store = PdfJobStore(pdf_job_runner or default_pdf_job_runner)

    def authorize(request: Request) -> None:
        require_http_auth(request, expected_token=launch_token, enforce_loopback=enforce_loopback)

    @app.get("/healthz", response_model=HealthResponse)
    def healthz(request: Request) -> HealthResponse:
        authorize(request)
        state = controller.get_state()
        return HealthResponse(
            status="ok",
            service="lulu-backend",
            ready=not state.degraded,
        )

    @app.get("/v1/dependencies", response_model=DependencyHealthResponse)
    def dependencies(request: Request) -> DependencyHealthResponse:
        authorize(request)
        health = controller.current_dependency_health()
        return DependencyHealthResponse(**health.__dict__)

    @app.get("/v1/settings", response_model=SettingsResponse)
    def get_settings(request: Request) -> SettingsResponse:
        authorize(request)
        settings = controller.settings
        return SettingsResponse(
            path_mode=settings.path_mode,
            config_path=str(settings.config_path),
            chat_model=settings.chat_model,
            embedding_model=settings.embedding_model,
            whisper_model=settings.whisper_model,
            whisper_language=settings.whisper_language,
            chroma_path=str(settings.chroma_path),
            logs_path=str(settings.logs_path),
            exports_path=str(settings.exports_path),
            wake_phrase=settings.wake_phrase,
            practical_voice_mode=settings.practical_voice_mode,
        )

    @app.put("/v1/settings", response_model=SettingsUpdateResponse)
    def update_settings(request: Request, payload: SettingsUpdateRequest) -> SettingsUpdateResponse:
        authorize(request)
        config_path = controller.settings.config_path
        config_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, object] = {}
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing = {}
        updates = payload.model_dump(exclude_none=True)
        existing.update(updates)
        config_path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
        return SettingsUpdateResponse(saved=True, restart_required=True, config_path=str(config_path))

    @app.post("/v1/runtime/start", response_model=RuntimeStateResponse)
    def start_runtime(request: Request, payload: RuntimeControlRequest) -> RuntimeStateResponse:
        authorize(request)
        state = controller.start_runtime(payload.mode)
        return RuntimeStateResponse(**state.__dict__)

    @app.post("/v1/runtime/stop", response_model=RuntimeStateResponse)
    def stop_runtime(request: Request) -> RuntimeStateResponse:
        authorize(request)
        state = controller.stop_runtime()
        return RuntimeStateResponse(**state.__dict__)

    @app.post("/v1/runtime/restart", response_model=RuntimeStateResponse)
    def restart_runtime(request: Request, payload: RuntimeControlRequest) -> RuntimeStateResponse:
        authorize(request)
        state = controller.restart_runtime(payload.mode)
        return RuntimeStateResponse(**state.__dict__)

    @app.get("/v1/runtime/state", response_model=RuntimeStateResponse)
    def runtime_state(request: Request) -> RuntimeStateResponse:
        authorize(request)
        return RuntimeStateResponse(**controller.get_state().__dict__)

    @app.get("/v1/runtime/diagnostics", response_model=RuntimeDiagnosticsResponse)
    def runtime_diagnostics(request: Request) -> RuntimeDiagnosticsResponse:
        authorize(request)
        return RuntimeDiagnosticsResponse(**controller.get_diagnostics())

    @app.post("/v1/turns/text", response_model=AcceptedResponse)
    def submit_text_turn(request: Request, payload: TextTurnRequest) -> AcceptedResponse:
        authorize(request)
        controller.submit_text_turn_async(payload.text)
        return AcceptedResponse(accepted=True, request_id=payload.request_id, status="queued")

    @app.post("/v1/mode", response_model=RuntimeStateResponse)
    def set_mode(request: Request, payload: ModeRequest) -> RuntimeStateResponse:
        authorize(request)
        controller.set_runtime_mode(payload.mode)
        return RuntimeStateResponse(**controller.get_state().__dict__)

    @app.post("/v1/pdf-audiobook/jobs", response_model=PDFJobResponse)
    def create_pdf_job(request: Request, payload: PDFJobRequest) -> PDFJobResponse:
        authorize(request)
        return job_store.create_job(payload)

    @app.get("/v1/pdf-audiobook/jobs/{job_id}", response_model=PDFJobResponse)
    def get_pdf_job(request: Request, job_id: str) -> PDFJobResponse:
        authorize(request)
        return job_store.get_job(job_id)

    @app.websocket("/v1/events/ws")
    async def runtime_events(websocket: WebSocket) -> None:
        try:
            await require_websocket_auth(
                websocket,
                expected_token=launch_token,
                enforce_loopback=enforce_loopback,
            )
        except WebSocketDisconnect:
            return
        await websocket.accept()
        try:
            await ws_bridge.stream(send_json=websocket.send_json)
        except WebSocketDisconnect:
            return

    return app
