from __future__ import annotations

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


@dataclass(frozen=True)
class WakeMatch:
    matched: bool
    remainder: str = ""


WAKE_PHRASE_VARIANTS = (
    "hey lulu",
    "hey lu lu",
    "hey loo loo",
    "hey lou lou",
    "hey looloo",
    "hey luluu",
    "hay lulu",
)


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
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def set_on_chunk_spoken(self, callback: Callable[[str], None] | None) -> None:
        self._on_chunk_spoken = callback

    def start_turn(self) -> None:
        # The speech worker is long-lived; turns are delimited by queue drain only.
        return

    def enqueue_chunk(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return
        self._queue.put(clean_text)

    def finish_turn(self) -> None:
        self._queue.join()

    def speak(self, text: str) -> None:
        self.start_turn()
        self.enqueue_chunk(text)
        self.finish_turn()

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
                subprocess.run(["say", chunk], check=False)
                if self._on_chunk_spoken is not None:
                    self._on_chunk_spoken(chunk)
            finally:
                self._queue.task_done()


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

        if not speech_frames:
            return None

        audio = np.concatenate(speech_frames).astype(np.float32)
        return np.clip(audio, -1.0, 1.0)

    def transcribe_audio(self, audio: np.ndarray) -> str:
        wav_path = self._write_temp_wav(audio)
        try:
            result = transcribe(
                str(wav_path),
                path_or_hf_repo=self.settings.whisper_model,
            )
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
            return WakeMatch(matched=False)

        aliases = _wake_aliases(wake_phrase)
        for alias in aliases:
            if normalized == alias:
                return WakeMatch(matched=True, remainder="")
            if normalized.startswith(f"{alias} "):
                return WakeMatch(matched=True, remainder=normalized[len(alias) :].strip())
        return WakeMatch(matched=False)

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
    return re.sub(r"\s+", " ", text.strip().lower())


def _wake_aliases(wake_phrase: str) -> tuple[str, ...]:
    if wake_phrase == "hey lulu":
        return WAKE_PHRASE_VARIANTS
    return (wake_phrase,)
