from __future__ import annotations

import subprocess
import tempfile
import wave
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd
from mlx_whisper import transcribe

from config import Settings


class MacOSTTS:
    def speak(self, text: str) -> None:
        clean_text = text.strip()
        if not clean_text:
            return

        # Call the native macOS voice without shell interpolation.
        subprocess.run(["say", clean_text], check=False)


class AudioHandler:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def record_until_silence(self) -> np.ndarray | None:
        sample_rate = self.settings.sample_rate
        frames_per_chunk = int(sample_rate * self.settings.vad_chunk_seconds)
        max_chunks = int(
            self.settings.vad_max_record_seconds / self.settings.vad_chunk_seconds
        )
        silence_limit = max(
            1, int(self.settings.vad_silence_seconds / self.settings.vad_chunk_seconds)
        )
        min_speech_chunks = max(
            1,
            int(
                self.settings.vad_min_speech_seconds / self.settings.vad_chunk_seconds
            ),
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
