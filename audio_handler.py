from __future__ import annotations

from difflib import SequenceMatcher
from dataclasses import dataclass
from collections.abc import Callable
import queue
import re
import subprocess
import tempfile
import threading
import wave
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from mlx_whisper import transcribe

from config import Settings


class AudioCaptureError(RuntimeError):
    """Raised when microphone capture fails for dependency or device reasons."""


class AudioTranscriptionError(RuntimeError):
    """Raised when local transcription fails."""


class TTSPlaybackError(RuntimeError):
    """Raised when native macOS speech playback fails for a chunk."""

    def __init__(self, chunk: str, message: str) -> None:
        super().__init__(message)
        self.chunk = chunk


@dataclass(frozen=True)
class WakeMatch:
    matched: bool
    remainder: str = ""
    score: float = 0.0
    matched_prefix: str = ""
    reason: str = ""


class PhraseChunker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.buffer = ""
        self._boundary_pattern = re.compile(r"(.+?[,\.;:!?])(\s+|$)", re.DOTALL)

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
        return ready

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

        last_good_end = None
        for match in self._boundary_pattern.finditer(self.buffer):
            if match.end(1) <= self.settings.tts_stream_soft_chunk_chars:
                last_good_end = match.end(1)
            else:
                break

        if last_good_end is not None:
            return self._pop_chunk(last_good_end)

        if len(self.buffer) >= self.settings.tts_stream_soft_chunk_chars:
            soft_break = self._find_break_before(self.settings.tts_stream_soft_chunk_chars)
            if soft_break is not None:
                return self._pop_chunk(soft_break)

        if len(self.buffer) >= self.settings.tts_stream_max_chunk_chars:
            hard_break = self._find_break_before(self.settings.tts_stream_max_chunk_chars)
            if hard_break is None:
                hard_break = self.settings.tts_stream_max_chunk_chars
            return self._pop_chunk(hard_break)

        return None

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
    def __init__(self) -> None:
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
                # Phrase-boundary chunking is intentionally chosen for lower latency.
                # This may be swapped later if sentence-level chunks sound smoother.
                try:
                    result = subprocess.run(
                        ["say", chunk],
                        check=False,
                        capture_output=True,
                        text=True,
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

    def record_until_silence(self) -> np.ndarray | None:
        return self._record_until_silence(
            max_record_seconds=self.settings.vad_max_record_seconds,
            min_speech_seconds=self.settings.vad_min_speech_seconds,
        )

    def record_wake_scan(self) -> np.ndarray | None:
        return self._record_until_silence(
            max_record_seconds=self.settings.wake_scan_max_record_seconds,
            min_speech_seconds=self.settings.wake_scan_min_speech_seconds,
        )

    def _record_until_silence(
        self,
        max_record_seconds: float,
        min_speech_seconds: float,
    ) -> np.ndarray | None:
        sample_rate = self.settings.sample_rate
        frames_per_chunk = int(sample_rate * self.settings.vad_chunk_seconds)
        max_chunks = int(max_record_seconds / self.settings.vad_chunk_seconds)
        silence_limit = max(
            1, int(self.settings.vad_silence_seconds / self.settings.vad_chunk_seconds)
        )
        min_speech_chunks = max(
            1,
            int(min_speech_seconds / self.settings.vad_chunk_seconds),
        )
        pre_roll = deque(maxlen=3)
        speech_frames: list[np.ndarray] = []
        speech_started = False
        silence_chunks = 0

        try:
            with sd.InputStream(
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
                "Microphone capture failed. Check microphone permission and audio device availability."
            ) from exc

        if not speech_frames:
            return None

        audio = np.concatenate(speech_frames).astype(np.float32)
        return np.clip(audio, -1.0, 1.0)

    def transcribe_audio(self, audio: np.ndarray) -> str:
        wav_path = self._write_temp_wav(audio)
        try:
            try:
                result = transcribe(
                    str(wav_path),
                    path_or_hf_repo=self.settings.whisper_model,
                )
            except Exception as exc:
                raise AudioTranscriptionError(
                    "Local Whisper transcription failed. Check the configured model and local MLX runtime."
                ) from exc
        finally:
            wav_path.unlink(missing_ok=True)

        text = (result.get("text") or "").strip()
        return text

    def listen_and_transcribe(self) -> str:
        audio = self.record_until_silence()
        if audio is None:
            return ""
        return self.transcribe_audio(audio)

    def match_wake_phrase(self, transcript: str) -> WakeMatch:
        normalized = normalize_text(transcript)
        wake_phrase = normalize_text(self.settings.wake_phrase)
        if not normalized or not wake_phrase:
            return WakeMatch(matched=False, reason="empty")

        score_result = score_wake_phrase_match(
            transcript=normalized,
            wake_phrase=wake_phrase,
            threshold=self.settings.wake_match_score_threshold,
        )
        return score_result

    def _write_temp_wav(self, audio: np.ndarray) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)

        pcm16 = (audio * 32767).astype(np.int16)
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(self.settings.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.settings.sample_rate)
            wav_file.writeframes(pcm16.tobytes())
        return wav_path


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
    best_prefix_length = 0
    best_prefix_text = ""

    for prefix_length in range(
        len(wake_tokens), min(len(transcript_tokens), len(wake_tokens) + 1) + 1
    ):
        prefix_tokens = transcript_tokens[:prefix_length]
        prefix_text = " ".join(prefix_tokens)
        score = _wake_similarity_score(prefix_text, wake_phrase)
        if score > best_score:
            best_score = score
            best_prefix_length = prefix_length
            best_prefix_text = prefix_text

    if best_score < threshold:
        return WakeMatch(
            matched=False,
            score=best_score,
            matched_prefix=best_prefix_text,
            reason="below-threshold",
        )

    remainder = " ".join(transcript_tokens[best_prefix_length:]).strip()
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


def _wake_signature(text: str) -> str:
    tokens = [_normalize_wake_token(token) for token in text.split()]
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
