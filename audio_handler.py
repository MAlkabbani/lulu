from __future__ import annotations

import queue
import re
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
from huggingface_hub import snapshot_download

from config import Settings
from wake_detection import WakeAudioAnalysis, WakeWordEngine, combine_wake_confidence

try:
    import sounddevice as sd
except (ImportError, OSError) as exc:
    sd: Any = None
    _SOUNDDEVICE_IMPORT_ERROR: Exception | None = exc
else:
    _SOUNDDEVICE_IMPORT_ERROR = None

try:
    from mlx_whisper import transcribe
except (ImportError, OSError) as exc:
    transcribe: Any = None
    _MLX_WHISPER_IMPORT_ERROR: Exception | None = exc
else:
    _MLX_WHISPER_IMPORT_ERROR = None


class AudioCaptureError(RuntimeError):
    """Raised when microphone capture fails for dependency or device reasons."""


class AudioTranscriptionError(RuntimeError):
    """Raised when local transcription fails."""


class TTSPlaybackError(RuntimeError):
    """Raised when native macOS speech playback fails for a chunk."""

    def __init__(self, chunk: str, message: str) -> None:
        super().__init__(message)
        self.chunk = chunk


def audio_input_available() -> bool:
    return sd is not None


def _require_transcribe() -> Callable[..., Any]:
    if transcribe is None:
        raise AudioTranscriptionError(
            "Local Whisper transcription is unavailable. Install the MLX "
            "runtime and whisper dependencies before transcribing audio."
        ) from _MLX_WHISPER_IMPORT_ERROR
    return transcribe


def _split_remote_model_reference(model_reference: str, revision: str) -> tuple[str, str | None]:
    clean_reference = model_reference.strip()
    if "@" in clean_reference:
        repo_id, explicit_revision = clean_reference.rsplit("@", 1)
        repo_id = repo_id.strip()
        explicit_revision = explicit_revision.strip()
        return repo_id, explicit_revision or None
    return clean_reference, revision.strip() or None


@dataclass(frozen=True)
class WakeMatch:
    matched: bool
    remainder: str = ""
    score: float = 0.0
    confidence: float = 0.0
    transcript_score: float = 0.0
    acoustic_score: float = 0.0
    dtw_score: float = 0.0
    threshold: float = 0.0
    snr_db: float = 0.0
    matched_prefix: str = ""
    reason: str = ""


class PhraseChunker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.buffer = ""
        self._sentence_boundary_pattern = re.compile(r"(.+?[.!?])(\s+|$)", re.DOTALL)
        self._clause_boundary_pattern = re.compile(r"(.+?[,;:])(\s+|$)", re.DOTALL)

    def push(self, text: str) -> list[str]:
        if text:
            self.buffer += text
        return self._drain_ready_chunks(final=False)

    def finish(self) -> list[str]:
        return self._drain_ready_chunks(final=True)

    def _drain_ready_chunks(self, final: bool) -> list[str]:
        ready: list[str] = []
        while True:
            chunk = self._extract_next_chunk(final=final)
            if chunk is None:
                break
            ready.append(chunk)
        if final:
            ready = self._merge_short_final_tail(ready)
        return ready

    def _merge_short_final_tail(self, ready: list[str]) -> list[str]:
        if len(ready) < 2:
            return ready

        last_chunk = ready[-1].strip()
        if len(last_chunk) > self.settings.tts_stream_tail_merge_chars:
            return ready

        merged_candidate = f"{ready[-2].rstrip()} {last_chunk}".strip()
        max_merged_length = (
            self.settings.tts_stream_max_chunk_chars
            + self.settings.tts_stream_tail_merge_overflow_chars
        )
        if len(merged_candidate) > max_merged_length:
            return ready
        return [*ready[:-2], merged_candidate]

    def _extract_next_chunk(self, final: bool) -> str | None:
        trimmed = self.buffer.lstrip()
        if not trimmed:
            self.buffer = ""
            return None
        self.buffer = trimmed

        if final:
            chunk = self.buffer.strip()
            self.buffer = ""
            return chunk or None

        if len(self.buffer) < self.settings.tts_stream_min_chunk_chars:
            return None

        sentence_break = self._find_grouped_sentence_break_before(
            self.settings.tts_stream_soft_chunk_chars
        )
        if sentence_break is not None:
            return self._pop_chunk(sentence_break)

        clause_break = self._find_clause_break_before(self.settings.tts_stream_soft_chunk_chars)
        if clause_break is not None:
            return self._pop_chunk(clause_break)

        if len(self.buffer) < self.settings.tts_stream_max_chunk_chars:
            return None

        sentence_break = self._find_grouped_sentence_break_before(
            self.settings.tts_stream_max_chunk_chars
        )
        if sentence_break is not None:
            return self._pop_chunk(sentence_break)

        clause_break = self._find_clause_break_before(self.settings.tts_stream_max_chunk_chars)
        if clause_break is not None:
            return self._pop_chunk(clause_break)

        hard_break = self._find_break_before(self.settings.tts_stream_max_chunk_chars)
        if hard_break is None:
            hard_break = self.settings.tts_stream_max_chunk_chars
        return self._pop_chunk(hard_break)

    def _find_grouped_sentence_break_before(self, limit: int) -> int | None:
        boundaries: list[int] = []
        for match in self._sentence_boundary_pattern.finditer(self.buffer):
            if match.end(1) > limit:
                break
            candidate = self.buffer[: match.end(1)].strip()
            if len(candidate) >= self.settings.tts_stream_min_chunk_chars:
                boundaries.append(match.end(1))
        if not boundaries:
            return None

        max_sentences = max(1, self.settings.tts_stream_max_group_sentences)
        best_group_end: int | None = None
        for end_index in boundaries[:max_sentences]:
            candidate = self.buffer[:end_index].strip()
            if len(candidate) <= self.settings.tts_stream_group_target_chars:
                best_group_end = end_index

        if best_group_end is not None:
            return best_group_end
        return boundaries[0]

    def _find_clause_break_before(self, limit: int) -> int | None:
        if limit < self.settings.tts_stream_clause_boundary_chars:
            return None

        best_boundary: int | None = None
        for match in self._clause_boundary_pattern.finditer(self.buffer):
            clause_end = match.end(1)
            if clause_end > limit:
                break
            candidate = self.buffer[:clause_end].strip()
            if len(candidate) < self.settings.tts_stream_clause_boundary_chars:
                continue
            best_boundary = clause_end
        return best_boundary

    def _find_break_before(self, limit: int) -> int | None:
        capped = self.buffer[:limit]
        for index in range(len(capped) - 1, -1, -1):
            if capped[index].isspace():
                return index
        return None

    def _pop_chunk(self, end_index: int) -> str | None:
        chunk = self.buffer[:end_index].strip()
        self.buffer = self.buffer[end_index:].lstrip()
        return chunk or None


class MacOSTTS:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._on_chunk_spoken: Callable[[str], None] | None = None
        self._on_chunk_error: Callable[[TTSPlaybackError], None] | None = None
        self._turn_errors: list[TTSPlaybackError] = []
        self._turn_error_lock = threading.Lock()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def set_on_chunk_spoken(self, callback: Callable[[str], None] | None) -> None:
        self._on_chunk_spoken = callback

    def set_on_chunk_error(self, callback: Callable[[TTSPlaybackError], None] | None) -> None:
        self._on_chunk_error = callback

    def start_turn(self) -> None:
        # The speech worker is long-lived; turns are delimited by queue drain only.
        with self._turn_error_lock:
            self._turn_errors = []

    def enqueue_chunk(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        self._queue.put(clean_text)

    def finish_turn(self) -> list[TTSPlaybackError]:
        self._queue.join()
        with self._turn_error_lock:
            return list(self._turn_errors)

    def speak(self, text: str) -> list[TTSPlaybackError]:
        self.start_turn()
        self.enqueue_chunk(text)
        return self.finish_turn()

    def close(self) -> None:
        self._queue.put(None)
        self._queue.join()
        self._worker.join(timeout=1)

    def _run_worker(self) -> None:
        while True:
            chunk = self._queue.get()
            try:
                if chunk is None:
                    return
                # Each chunk is spoken by macOS `say`; smoother playback comes from
                # emitting fewer, larger chunks rather than restarting `say` too often.
                try:
                    result = subprocess.run(
                        ["say", chunk],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=self._settings.tts_say_timeout_seconds,
                    )
                except OSError as exc:
                    error = TTSPlaybackError(
                        chunk,
                        f"TTS playback failed because macOS 'say' could not run: {exc}",
                    )
                    self._record_turn_error(error)
                    if self._on_chunk_error is not None:
                        self._on_chunk_error(error)
                    continue
                except subprocess.TimeoutExpired:
                    error = TTSPlaybackError(
                        chunk,
                        "TTS playback timed out while waiting for macOS 'say' to finish."
                        f" Timeout: {self._settings.tts_say_timeout_seconds}s.",
                    )
                    self._record_turn_error(error)
                    if self._on_chunk_error is not None:
                        self._on_chunk_error(error)
                    continue

                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    detail = stderr or f"macOS 'say' exited with status {result.returncode}."
                    error = TTSPlaybackError(chunk, f"TTS playback failed: {detail}")
                    self._record_turn_error(error)
                    if self._on_chunk_error is not None:
                        self._on_chunk_error(error)
                    continue
                if self._on_chunk_spoken is not None:
                    self._on_chunk_spoken(chunk)
            finally:
                self._queue.task_done()

    def _record_turn_error(self, error: TTSPlaybackError) -> None:
        with self._turn_error_lock:
            self._turn_errors.append(error)


class AudioHandler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._wake_engine = WakeWordEngine(settings)
        self._whisper_model_lock = threading.RLock()
        self._whisper_model_path: str | None = None
        self._whisper_ready = False

    def record_until_silence(self) -> np.ndarray | None:
        return self._record_until_silence(
            max_record_seconds=self.settings.vad_max_record_seconds,
            min_speech_seconds=self.settings.vad_min_speech_seconds,
            silence_seconds=self.settings.vad_silence_seconds,
            pre_roll_chunks=3,
        )

    def record_wake_scan(self) -> np.ndarray | None:
        return self._record_until_silence(
            max_record_seconds=self.settings.wake_scan_max_record_seconds,
            min_speech_seconds=self.settings.wake_scan_min_speech_seconds,
            silence_seconds=self.settings.wake_scan_silence_seconds,
            pre_roll_chunks=self.settings.wake_scan_pre_roll_chunks,
        )

    def ensure_transcription_ready(self) -> None:
        with self._whisper_model_lock:
            if self._whisper_ready:
                return
            model_reference = self._resolve_whisper_model_reference()
            silent_audio = np.zeros(self.settings.sample_rate, dtype=np.float32)
            try:
                _require_transcribe()(
                    silent_audio,
                    path_or_hf_repo=model_reference,
                    language=self.settings.whisper_language,
                )
            except Exception as exc:
                raise AudioTranscriptionError(self._format_transcription_error(exc)) from exc
            self._whisper_ready = True

    def _record_until_silence(
        self,
        max_record_seconds: float,
        min_speech_seconds: float,
        silence_seconds: float,
        pre_roll_chunks: int,
    ) -> np.ndarray | None:
        if sd is None:
            raise AudioCaptureError(
                "Audio input dependency is unavailable. Install PortAudio "
                "and the sounddevice runtime before recording."
            ) from _SOUNDDEVICE_IMPORT_ERROR

        sample_rate = self.settings.sample_rate
        frames_per_chunk = int(sample_rate * self.settings.vad_chunk_seconds)
        max_chunks = int(max_record_seconds / self.settings.vad_chunk_seconds)
        silence_limit = max(1, int(silence_seconds / self.settings.vad_chunk_seconds))
        min_speech_chunks = max(
            1,
            int(min_speech_seconds / self.settings.vad_chunk_seconds),
        )
        pre_roll = deque(maxlen=max(1, pre_roll_chunks))
        speech_frames: list[np.ndarray] = []
        speech_started = False
        silence_chunks = 0

        try:
            with sd.InputStream(
                device=_resolve_input_device(self.settings.audio_input_device),
                samplerate=sample_rate,
                channels=self.settings.channels,
                dtype="float32",
                blocksize=frames_per_chunk,
            ) as stream:
                for _ in range(max_chunks):
                    chunk, _ = stream.read(frames_per_chunk)
                    chunk = np.squeeze(chunk)
                    rms = float(np.sqrt(np.mean(np.square(chunk))))
                    is_speech = rms >= self.settings.vad_threshold

                    if not speech_started:
                        pre_roll.append(chunk.copy())
                        if is_speech:
                            speech_started = True
                            speech_frames.extend(pre_roll)
                            silence_chunks = 0
                        continue

                    speech_frames.append(chunk.copy())
                    if is_speech:
                        silence_chunks = 0
                    else:
                        silence_chunks += 1

                    if len(speech_frames) >= min_speech_chunks and silence_chunks >= silence_limit:
                        break
        except Exception as exc:
            raise AudioCaptureError(
                "Microphone capture failed. Check microphone permission "
                "and audio device availability."
            ) from exc

        if not speech_frames:
            return None

        audio = np.concatenate(speech_frames).astype(np.float32)
        return np.clip(audio, -1.0, 1.0)

    def transcribe_audio(self, audio: np.ndarray, *, initial_prompt: str | None = None) -> str:
        model_reference = self._resolve_whisper_model_reference()
        pcm_source = np.asarray(audio, dtype=np.float32).reshape(-1)
        if pcm_source.size == 0:
            pcm_source = np.zeros(1, dtype=np.float32)
        pcm_source = np.nan_to_num(pcm_source, nan=0.0, posinf=1.0, neginf=-1.0)
        pcm_source = np.clip(pcm_source, -1.0, 1.0)
        try:
            result = _require_transcribe()(
                pcm_source,
                path_or_hf_repo=model_reference,
                language=self.settings.whisper_language,
                initial_prompt=initial_prompt,
            )
        except Exception as exc:
            raise AudioTranscriptionError(self._format_transcription_error(exc)) from exc

        text = (result.get("text") or "").strip()
        with self._whisper_model_lock:
            self._whisper_ready = True
        return text

    def listen_and_transcribe(self) -> str:
        audio = self.record_until_silence()
        if audio is None:
            return ""
        return self.transcribe_audio(audio)

    def analyze_wake_audio(self, audio: np.ndarray) -> WakeAudioAnalysis:
        return self._wake_engine.analyze(audio)

    def match_wake_phrase(
        self,
        transcript: str,
        analysis: WakeAudioAnalysis | None = None,
    ) -> WakeMatch:
        normalized = normalize_text(transcript)
        wake_phrase = normalize_text(self.settings.wake_phrase)
        if not normalized or not wake_phrase:
            return WakeMatch(matched=False, reason="empty")

        transcript_match = score_wake_phrase_match(
            transcript=normalized,
            wake_phrase=wake_phrase,
            threshold=self.settings.wake_match_score_threshold,
        )
        if analysis is None:
            return WakeMatch(
                matched=transcript_match.matched,
                remainder=transcript_match.remainder,
                score=transcript_match.score,
                confidence=transcript_match.score,
                transcript_score=transcript_match.score,
                matched_prefix=transcript_match.matched_prefix,
                reason=transcript_match.reason,
            )

        confidence, threshold = combine_wake_confidence(
            transcript_score=transcript_match.score,
            analysis=analysis,
            settings=self.settings,
        )
        matched = transcript_match.matched or (
            transcript_match.score >= self.settings.wake_transcript_score_floor
            and confidence >= threshold
        )
        reason = "probabilistic-match" if matched else transcript_match.reason or "below-threshold"
        return WakeMatch(
            matched=matched,
            remainder=transcript_match.remainder if matched else "",
            score=confidence,
            confidence=confidence,
            transcript_score=transcript_match.score,
            acoustic_score=analysis.acoustic_score,
            dtw_score=analysis.dtw_score,
            threshold=threshold,
            snr_db=analysis.snr_db,
            matched_prefix=transcript_match.matched_prefix,
            reason=reason,
        )

    def build_fast_path_wake_match(self, analysis: WakeAudioAnalysis) -> WakeMatch:
        return WakeMatch(
            matched=True,
            score=analysis.confidence,
            confidence=analysis.confidence,
            acoustic_score=analysis.acoustic_score,
            dtw_score=analysis.dtw_score,
            threshold=analysis.dynamic_threshold,
            snr_db=analysis.snr_db,
            matched_prefix=self.settings.wake_phrase,
            reason="acoustic-fast-path",
        )

    def _resolve_whisper_model_reference(self) -> str:
        configured = Path(self.settings.whisper_model).expanduser()
        if configured.exists():
            return str(configured)

        with self._whisper_model_lock:
            if self._whisper_model_path and Path(self._whisper_model_path).exists():
                return self._whisper_model_path

            repo_id, revision = _split_remote_model_reference(
                self.settings.whisper_model,
                self.settings.whisper_model_revision,
            )
            try:
                resolved_path = snapshot_download(
                    repo_id=repo_id,
                    local_files_only=True,
                    revision=revision,
                )
            except Exception as exc:
                if revision is None:
                    raise AudioTranscriptionError(
                        "Remote Whisper model downloads require a pinned revision. "
                        "Set MLX_WHISPER_MODEL to repo@revision or set MLX_WHISPER_REVISION."
                    ) from exc
                try:
                    resolved_path = snapshot_download(repo_id=repo_id, revision=revision)
                except Exception as exc:
                    raise AudioTranscriptionError(self._format_transcription_error(exc)) from exc

            self._whisper_model_path = resolved_path
            return resolved_path

    def _format_transcription_error(self, exc: Exception) -> str:
        detail = str(exc).strip() or type(exc).__name__
        return (
            "Local Whisper transcription failed. "
            f"Model: {self.settings.whisper_model}. Detail: {detail}"
        )


def normalize_text(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def score_wake_phrase_match(
    transcript: str,
    wake_phrase: str,
    threshold: float,
) -> WakeMatch:
    wake_tokens = wake_phrase.split()
    transcript_tokens = transcript.split()
    if len(transcript_tokens) < len(wake_tokens):
        return WakeMatch(matched=False, reason="too-short")

    best_score = 0.0
    best_end_index = 0
    best_prefix_text = ""

    max_start_index = min(2, max(0, len(transcript_tokens) - len(wake_tokens)))
    max_window_length = len(wake_tokens) + 2
    for start_index in range(max_start_index + 1):
        for prefix_length in range(
            len(wake_tokens),
            min(len(transcript_tokens) - start_index, max_window_length) + 1,
        ):
            end_index = start_index + prefix_length
            prefix_tokens = transcript_tokens[start_index:end_index]
            prefix_text = " ".join(prefix_tokens)
            score = _wake_similarity_score(prefix_text, wake_phrase) - (start_index * 0.04)
            if score > best_score:
                best_score = score
                best_end_index = end_index
                best_prefix_text = prefix_text

    if best_score < threshold:
        return WakeMatch(
            matched=False,
            score=best_score,
            matched_prefix=best_prefix_text,
            reason="below-threshold",
        )

    remainder = " ".join(transcript_tokens[best_end_index:]).strip()
    return WakeMatch(
        matched=True,
        remainder=remainder,
        score=best_score,
        matched_prefix=best_prefix_text,
        reason="score-match",
    )


def _wake_similarity_score(candidate: str, target: str) -> float:
    candidate_signature = _wake_signature(candidate)
    target_signature = _wake_signature(target)
    if not candidate_signature or not target_signature:
        return 0.0

    raw_score = SequenceMatcher(None, candidate_signature, target_signature).ratio()

    candidate_tokens = candidate_signature.split()
    target_tokens = target_signature.split()
    token_penalty = 0.0
    if candidate_tokens and target_tokens and candidate_tokens[0] != target_tokens[0]:
        token_penalty += 0.08
    if len(candidate_tokens) != len(target_tokens):
        token_penalty += 0.03

    return max(0.0, raw_score - token_penalty)


def text_similarity(left: str, right: str) -> float:
    normalized_left = normalize_text(left)
    normalized_right = normalize_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _resolve_input_device(device: str) -> int | str | None:
    selected = device.strip()
    if not selected:
        return None
    return int(selected) if selected.isdigit() else selected


def _wake_signature(text: str) -> str:
    tokens = [_normalize_wake_token(token) for token in text.split()]
    if len(tokens) >= 2 and tokens[0] == "i" and tokens[1] in {"love", "like"}:
        tokens = ["hey", "lulu", *tokens[2:]]
    if len(tokens) >= 2 and tokens[0] == "hey" and tokens[1] in {"helo", "hulu", "loks"}:
        tokens[1] = "lulu"
    collapsed: list[str] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token in {"lu", "lulu"}:
            run_length = 1
            next_index = index + 1
            while next_index < len(tokens) and tokens[next_index] == "lu":
                run_length += 1
                next_index += 1
            collapsed.append("lulu" if run_length >= 2 else token)
            index = next_index
            continue
        collapsed.append(token)
        index += 1

    return " ".join(collapsed)


def _normalize_wake_token(token: str) -> str:
    squashed = re.sub(r"(.)\1{1,}", r"\1", token)
    if squashed == "hay":
        return "hey"
    if squashed in {"ey", "he", "hey"}:
        return "hey"
    if squashed in {"lu", "loo", "lou", "luh", "lul", "lulo", "lulu", "luloo"}:
        return "lu" if squashed != "lulu" else "lulu"
    return squashed
