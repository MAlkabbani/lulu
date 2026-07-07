from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


API_VERSION = "v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(StrictModel):
    api_version: str = API_VERSION
    status: str
    service: str
    ready: bool


class DependencyHealthResponse(StrictModel):
    api_version: str = API_VERSION
    ollama_reachable: bool
    ollama_version: str
    chat_model_available: bool
    embedding_model_available: bool
    audio_input_available: bool
    tts_available: bool
    ffmpeg_available: bool
    memory_path_available: bool
    issues: list[str]


class SettingsResponse(StrictModel):
    api_version: str = API_VERSION
    path_mode: str
    config_path: str
    chat_model: str
    embedding_model: str
    whisper_model: str
    whisper_language: str
    chroma_path: str
    logs_path: str
    exports_path: str
    wake_phrase: str
    practical_voice_mode: bool


class SettingsUpdateRequest(StrictModel):
    chat_model: str | None = None
    embedding_model: str | None = None
    whisper_model: str | None = None
    whisper_language: str | None = None
    wake_phrase: str | None = None
    practical_voice_mode: bool | None = None


class SettingsUpdateResponse(StrictModel):
    api_version: str = API_VERSION
    saved: bool
    restart_required: bool
    config_path: str


class RuntimeControlRequest(StrictModel):
    mode: Literal["continuous", "turn-based"] = "continuous"


class RuntimeStateResponse(StrictModel):
    api_version: str = API_VERSION
    mode: str
    runtime_mode: str
    status_line: str
    degraded: bool
    last_error: str


class RuntimeDiagnosticsResponse(StrictModel):
    api_version: str = API_VERSION
    mode: str
    runtime_mode: str
    status_line: str
    last_error: str
    runtime_active: bool
    transcript: str
    response: str
    invocation_summary: str
    action_summary: str
    current_tool_status: str
    memory_hit_count: int
    emitted_chunk_count: int
    spoken_chunk_count: int
    emitted_char_count: int
    spoken_char_count: int
    last_emitted_chunk: str
    last_spoken_chunk: str
    playback_gap_count: int
    tail_merge_count: int
    recent_saves: list[str]
    recent_events: list[str]
    recent_wake_attempts: list[str]
    latencies_ms: dict[str, float]
    conversation_window_remaining: float | None = None
    cooldown_remaining: float | None = None
    wake_guidance: str
    last_wake_score: float | None = None
    last_wake_decision: str
    wake_score_threshold: float | None = None
    accepted_wake_attempts: int
    rejected_wake_attempts: int
    last_wake_confidence: float | None = None
    last_wake_acoustic_score: float | None = None
    last_wake_dtw_score: float | None = None
    last_wake_snr_db: float | None = None
    last_wake_feature_frames: int


class ModeRequest(StrictModel):
    mode: Literal["continuous", "turn-based"]


class PDFJobRequest(StrictModel):
    pdf_path: str = Field(min_length=1)
    output_dir: str = Field(min_length=1)
    title: str | None = None
    author: str | None = None
    genre: str | None = None
    chapter_splitting: str = "auto"
    dry_run: bool = False
    portable_format: str = "none"
    preview_chars: int = 400
    pronunciation_file: str | None = None


class PDFJobResponse(StrictModel):
    api_version: str = API_VERSION
    job_id: str
    status: str
    dry_run: bool
    output_dir: str | None = None
    manifest_path: str | None = None
    error: str | None = None
    section_count: int = 0
    progress: list[str] = Field(default_factory=list)


class EventEnvelope(StrictModel):
    api_version: str = API_VERSION
    event_type: str
    timestamp: str
    payload: dict
