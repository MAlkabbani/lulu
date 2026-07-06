from __future__ import annotations

import json
import os
import threading
import tempfile
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Request, WebSocket, status
from starlette.websockets import WebSocketDisconnect

from app_core.runtime_controller import RuntimeController
from backend_service.api_models import (
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
)
from backend_service.auth import require_http_auth, require_websocket_auth
from backend_service.websocket_events import WebSocketEventBridge
from pdf_audiobook import ServiceJobResult, run_service_job


PdfJobRunner = Callable[[str, PDFJobRequest, Callable[[str], None]], ServiceJobResult]


class PdfJobStore:
    def __init__(
        self,
        runner: PdfJobRunner,
        *,
        max_workers: int = 2,
        max_pending_jobs: int = 4,
        retention_seconds: float = 300.0,
        retention_limit: int = 50,
    ) -> None:
        self._runner = runner
        self._jobs: dict[str, PDFJobResponse] = {}
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="lulu-pdf-job")
        self._max_pending_jobs = max_pending_jobs
        self._retention_seconds = retention_seconds
        self._retention_limit = retention_limit
        self._job_updated_at: dict[str, float] = {}

    def create_job(self, request: PDFJobRequest) -> PDFJobResponse:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._prune_jobs_locked()
            active_jobs = sum(1 for job in self._jobs.values() if job.status in {"pending", "running"})
            if active_jobs >= self._max_pending_jobs:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="PDF job queue is full. Try again after active jobs finish.",
                )
            created_job = PDFJobResponse(job_id=job_id, status="pending", dry_run=request.dry_run)
            self._jobs[job_id] = created_job
            self._job_updated_at[job_id] = time.time()
        self._executor.submit(self._run_job, job_id, request)
        return created_job

    def get_job(self, job_id: str) -> PDFJobResponse:
        with self._lock:
            self._prune_jobs_locked()
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
                self._job_updated_at[job_id] = time.time()

        with self._lock:
            current = self._jobs[job_id]
            self._jobs[job_id] = current.model_copy(update={"status": "running"})
            self._job_updated_at[job_id] = time.time()

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
            self._job_updated_at[job_id] = time.time()

    def _prune_jobs_locked(self) -> None:
        if not self._jobs:
            return
        now = time.time()
        finished_job_ids = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {"completed", "failed"}
        ]
        for job_id in finished_job_ids:
            updated_at = self._job_updated_at.get(job_id, now)
            if now - updated_at >= self._retention_seconds:
                self._jobs.pop(job_id, None)
                self._job_updated_at.pop(job_id, None)

        finished_job_ids = [
            job_id
            for job_id, job in self._jobs.items()
            if job.status in {"completed", "failed"}
        ]
        if len(self._jobs) <= self._retention_limit:
            return
        overflow = len(self._jobs) - self._retention_limit
        finished_job_ids.sort(key=lambda job_id: self._job_updated_at.get(job_id, 0.0))
        for job_id in finished_job_ids[:overflow]:
            self._jobs.pop(job_id, None)
            self._job_updated_at.pop(job_id, None)


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


def _load_existing_settings_strict(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read settings file: {config_path}.",
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Settings file is malformed and must be repaired before updates: {config_path}.",
        ) from exc
    if not isinstance(loaded, dict):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Settings file must contain a JSON object: {config_path}.",
        )
    return loaded


def _persist_settings_atomically(config_path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True)
    backup_path = config_path.parent / f"{config_path.name}.bak"
    temp_path: Path | None = None
    try:
        if config_path.exists():
            backup_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
        file_descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{config_path.name}.",
            suffix=".tmp",
            dir=config_path.parent,
        )
        temp_path = Path(temp_name)
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    except OSError as exc:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not persist settings file: {config_path}.",
        ) from exc


def build_service_app(
    controller: RuntimeController,
    *,
    launch_token: str,
    enforce_loopback: bool = True,
    pdf_job_runner: PdfJobRunner | None = None,
    pdf_job_max_workers: int = 2,
    pdf_job_max_pending: int = 4,
    pdf_job_retention_seconds: float = 300.0,
    pdf_job_retention_limit: int = 50,
) -> FastAPI:
    app = FastAPI(title="Lulu Backend Service", version="1.0.0")
    ws_bridge = WebSocketEventBridge(controller.event_bus)
    job_store = PdfJobStore(
        pdf_job_runner or default_pdf_job_runner,
        max_workers=pdf_job_max_workers,
        max_pending_jobs=pdf_job_max_pending,
        retention_seconds=pdf_job_retention_seconds,
        retention_limit=pdf_job_retention_limit,
    )

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
        existing = _load_existing_settings_strict(config_path)
        updates = payload.model_dump(exclude_none=True)
        existing.update(updates)
        _persist_settings_atomically(config_path, existing)
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
        try:
            return job_store.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF job not found.") from exc

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
