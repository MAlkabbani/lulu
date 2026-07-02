from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from time import perf_counter
import math
import re

import numpy as np

from config import Settings

EPSILON = 1e-8


@dataclass(frozen=True)
class WakeSignalFeatures:
    processed_audio: np.ndarray
    feature_matrix: np.ndarray
    duration_seconds: float
    snr_db: float
    voiced_ratio: float
    syllable_peaks: int
    spectral_centroid_mean: float
    zero_crossing_rate_mean: float


@dataclass(frozen=True)
class WakeAudioAnalysis:
    processed_audio: np.ndarray
    acoustic_score: float
    dtw_score: float
    confidence: float
    dynamic_threshold: float
    duration_seconds: float
    snr_db: float
    voiced_ratio: float
    syllable_peaks: int
    spectral_centroid_mean: float
    zero_crossing_rate_mean: float
    feature_frames: int
    candidate: bool
    fast_path_eligible: bool
    reason: str
    latency_ms: float


class WakeWordEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._template_bank = self._build_template_bank()

    def analyze(self, audio: np.ndarray) -> WakeAudioAnalysis:
        started = perf_counter()
        processed, snr_db = preprocess_wake_audio(audio, self.settings)
        features = extract_wake_features(processed, self.settings, snr_db=snr_db)
        dtw_score = max(
            (_dtw_similarity(features.feature_matrix, template) for template in self._template_bank),
            default=0.0,
        )
        acoustic_score = _score_acoustic_shape(features, self.settings)
        confidence = float(
            np.clip(
                (0.42 * acoustic_score)
                + (0.42 * dtw_score)
                + (_noise_support(features.snr_db, self.settings) * 0.16),
                0.0,
                1.0,
            )
        )
        dynamic_threshold = float(
            np.clip(
                self.settings.wake_acoustic_candidate_threshold
                - (self.settings.wake_noise_tolerance * _noise_support(features.snr_db, self.settings) * 0.07),
                0.40,
                0.76,
            )
        )
        candidate = (
            confidence >= dynamic_threshold
            or dtw_score >= max(0.74, self.settings.wake_acoustic_candidate_threshold + 0.18)
        )
        fast_path_eligible = (
            candidate
            and confidence >= self.settings.wake_fast_path_threshold
            and features.duration_seconds <= self.settings.wake_fast_path_max_seconds
            and 2 <= features.syllable_peaks <= 4
        )
        reason = "acoustic-candidate" if candidate else "acoustic-reject"
        return WakeAudioAnalysis(
            processed_audio=features.processed_audio,
            acoustic_score=acoustic_score,
            dtw_score=dtw_score,
            confidence=confidence,
            dynamic_threshold=dynamic_threshold,
            duration_seconds=features.duration_seconds,
            snr_db=features.snr_db,
            voiced_ratio=features.voiced_ratio,
            syllable_peaks=features.syllable_peaks,
            spectral_centroid_mean=features.spectral_centroid_mean,
            zero_crossing_rate_mean=features.zero_crossing_rate_mean,
            feature_frames=int(features.feature_matrix.shape[0]),
            candidate=candidate,
            fast_path_eligible=fast_path_eligible,
            reason=reason,
            latency_ms=(perf_counter() - started) * 1000,
        )

    def _build_template_bank(self) -> list[np.ndarray]:
        variants = [
            ("hey lulu", 0.92, -0.5),
            ("hey lulu", 1.00, 0.0),
            ("hey lulu", 1.12, 0.6),
            ("hey lu lu", 1.02, 0.1),
            ("hay lou lou", 0.98, -0.2),
            ("hey luloo", 1.08, 0.3),
        ]
        templates: list[np.ndarray] = []
        for phrase, tempo_scale, pitch_shift in variants:
            sample = synthesize_phrase_audio(
                phrase=phrase,
                sample_rate=self.settings.sample_rate,
                tempo_scale=tempo_scale,
                pitch_shift=pitch_shift,
                amplitude=0.95,
            )
            processed, snr_db = preprocess_wake_audio(sample, self.settings)
            features = extract_wake_features(processed, self.settings, snr_db=snr_db)
            templates.append(features.feature_matrix)
        return templates


def preprocess_wake_audio(audio: np.ndarray, settings: Settings) -> tuple[np.ndarray, float]:
    mono = np.asarray(audio, dtype=np.float32).flatten()
    if mono.size == 0:
        return mono, -20.0

    centered = mono - float(np.mean(mono))
    pre_emphasized = np.empty_like(centered)
    pre_emphasized[0] = centered[0]
    pre_emphasized[1:] = centered[1:] - (0.97 * centered[:-1])

    framed = _frame_signal(pre_emphasized, settings.sample_rate, frame_ms=25.0, hop_ms=10.0)
    if framed.size == 0:
        normalized = _normalize_rms(pre_emphasized, settings.wake_normalization_target_rms)
        return normalized.astype(np.float32), -20.0

    frame_energies = np.mean(np.square(framed), axis=1)
    sorted_energies = np.sort(frame_energies)
    noise_energy = float(np.mean(sorted_energies[: max(1, len(sorted_energies) // 5)]))
    signal_energy = float(np.mean(frame_energies))
    snr_db = 10.0 * math.log10((signal_energy + EPSILON) / (noise_energy + EPSILON))

    denoised = _spectral_gate(
        pre_emphasized,
        sample_rate=settings.sample_rate,
        reduction_strength=settings.wake_noise_reduction_strength,
    )
    deechoed = _echo_suppress(
        denoised,
        sample_rate=settings.sample_rate,
        strength=settings.wake_echo_suppression_strength,
    )
    normalized = _normalize_rms(deechoed, settings.wake_normalization_target_rms)
    return np.clip(normalized, -1.0, 1.0).astype(np.float32), snr_db


def extract_wake_features(
    audio: np.ndarray,
    settings: Settings,
    *,
    snr_db: float,
) -> WakeSignalFeatures:
    frames = _frame_signal(
        audio,
        settings.sample_rate,
        frame_ms=settings.wake_feature_frame_ms,
        hop_ms=settings.wake_feature_hop_ms,
    )
    if frames.size == 0:
        empty = np.zeros((1, settings.wake_mfcc_count + 3), dtype=np.float32)
        return WakeSignalFeatures(
            processed_audio=audio,
            feature_matrix=empty,
            duration_seconds=max(0.0, len(audio) / max(1, settings.sample_rate)),
            snr_db=snr_db,
            voiced_ratio=0.0,
            syllable_peaks=0,
            spectral_centroid_mean=0.0,
            zero_crossing_rate_mean=0.0,
        )

    window = np.hanning(frames.shape[1]).astype(np.float32)
    windowed = frames * window
    magnitude = np.abs(np.fft.rfft(windowed, axis=1))
    power = np.maximum(magnitude**2, EPSILON)
    frame_energies = np.mean(np.square(frames), axis=1)
    voiced_threshold = max(float(np.quantile(frame_energies, 0.35)), EPSILON)
    voiced_mask = frame_energies >= voiced_threshold
    voiced_ratio = float(np.mean(voiced_mask.astype(np.float32)))
    zcr = np.mean(np.abs(np.diff(np.signbit(frames), axis=1)), axis=1).astype(np.float32)
    freqs = np.fft.rfftfreq(frames.shape[1], 1.0 / settings.sample_rate)
    centroid = np.sum(magnitude * freqs[None, :], axis=1) / (np.sum(magnitude, axis=1) + EPSILON)
    mel_bank = _mel_filterbank(
        sample_rate=settings.sample_rate,
            n_fft=frames.shape[1],
        n_mels=settings.wake_mel_bins,
    )
    mel_energy = power @ mel_bank.T
    log_mel = np.log(np.maximum(mel_energy, EPSILON))
    dct = _dct_basis(settings.wake_mel_bins, settings.wake_mfcc_count)
    mfcc = log_mel @ dct.T

    energy_norm = _normalize_vector(frame_energies)
    centroid_norm = _normalize_vector(centroid)
    zcr_norm = _normalize_vector(zcr)
    feature_matrix = np.concatenate(
        [
            mfcc.astype(np.float32),
            centroid_norm[:, None],
            zcr_norm[:, None],
            energy_norm[:, None],
        ],
        axis=1,
    ).astype(np.float32)
    syllable_peaks = _count_syllable_peaks(energy_norm)
    return WakeSignalFeatures(
        processed_audio=audio,
        feature_matrix=feature_matrix,
        duration_seconds=len(audio) / max(1, settings.sample_rate),
        snr_db=snr_db,
        voiced_ratio=voiced_ratio,
        syllable_peaks=syllable_peaks,
        spectral_centroid_mean=float(np.mean(centroid)),
        zero_crossing_rate_mean=float(np.mean(zcr)),
    )


def combine_wake_confidence(
    transcript_score: float,
    analysis: WakeAudioAnalysis,
    settings: Settings,
) -> tuple[float, float]:
    acoustic_advantage = max(0.0, analysis.dtw_score - transcript_score)
    confidence = float(
        np.clip(
            (settings.wake_text_score_weight * transcript_score)
            + (settings.wake_acoustic_score_weight * analysis.acoustic_score)
            + (settings.wake_dtw_score_weight * analysis.dtw_score)
            + (settings.wake_mispronunciation_tolerance * acoustic_advantage * 0.35)
            + (_noise_support(analysis.snr_db, settings) * settings.wake_noise_tolerance * 0.05),
            0.0,
            1.0,
        )
    )
    dynamic_threshold = float(
        np.clip(
            settings.wake_confidence_threshold
            - (settings.wake_mispronunciation_tolerance * acoustic_advantage * 0.12)
            - (_noise_support(analysis.snr_db, settings) * settings.wake_noise_tolerance * 0.04),
            0.55,
            0.92,
        )
    )
    return confidence, dynamic_threshold


def synthesize_phrase_audio(
    phrase: str,
    sample_rate: int,
    *,
    tempo_scale: float = 1.0,
    pitch_shift: float = 0.0,
    amplitude: float = 0.9,
) -> np.ndarray:
    phrase_tokens = _normalize_text(phrase).split()
    if not phrase_tokens:
        return np.zeros(0, dtype=np.float32)

    pieces: list[np.ndarray] = []
    for token in phrase_tokens:
        syllables = _token_to_syllables(token)
        for syllable_index, syllable in enumerate(syllables):
            duration = (0.14 + 0.015 * (syllable_index % 2)) * tempo_scale
            pieces.append(
                _render_syllable(
                    syllable=syllable,
                    sample_rate=sample_rate,
                    duration=duration,
                    pitch_shift=pitch_shift,
                    amplitude=amplitude,
                )
            )
            pieces.append(np.zeros(int(sample_rate * 0.028 * tempo_scale), dtype=np.float32))
        pieces.append(np.zeros(int(sample_rate * 0.05 * tempo_scale), dtype=np.float32))
    rendered = np.concatenate(pieces).astype(np.float32)
    return np.clip(rendered, -1.0, 1.0)


def synthesize_noise(
    noise_type: str,
    length: int,
    sample_rate: int,
    *,
    seed: int,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    time_axis = np.arange(length, dtype=np.float32) / max(1, sample_rate)
    broadband = rng.normal(0.0, 1.0, length).astype(np.float32)
    if noise_type == "traffic":
        rumble = (
            0.8 * np.sin(2 * math.pi * 55.0 * time_axis)
            + 0.45 * np.sin(2 * math.pi * 110.0 * time_axis)
            + 0.25 * np.sin(2 * math.pi * 220.0 * time_axis)
        )
        return (0.5 * broadband + rumble).astype(np.float32)
    if noise_type == "office":
        chatter = np.sin(2 * math.pi * 3.3 * time_axis) * broadband
        clicks = np.sign(np.sin(2 * math.pi * 0.9 * time_axis)) * 0.2
        return (0.7 * chatter + clicks).astype(np.float32)
    if noise_type == "appliance":
        hum = (
            np.sin(2 * math.pi * 60.0 * time_axis)
            + 0.6 * np.sin(2 * math.pi * 120.0 * time_axis)
            + 0.35 * np.sin(2 * math.pi * 180.0 * time_axis)
        )
        hiss = np.convolve(broadband, np.ones(64, dtype=np.float32) / 64.0, mode="same")
        return (hum + 0.45 * hiss).astype(np.float32)
    return broadband


def mix_with_noise(
    audio: np.ndarray,
    noise_type: str,
    sample_rate: int,
    *,
    snr_db: float,
    seed: int,
) -> np.ndarray:
    if len(audio) == 0:
        return audio.astype(np.float32)
    noise = synthesize_noise(noise_type, len(audio), sample_rate, seed=seed)
    signal_rms = float(np.sqrt(np.mean(np.square(audio)) + EPSILON))
    noise_rms = float(np.sqrt(np.mean(np.square(noise)) + EPSILON))
    target_noise_rms = signal_rms / (10 ** (snr_db / 20.0))
    scaled_noise = noise * (target_noise_rms / max(noise_rms, EPSILON))
    mixed = audio + scaled_noise
    return np.clip(mixed, -1.0, 1.0).astype(np.float32)


def _score_acoustic_shape(features: WakeSignalFeatures, settings: Settings) -> float:
    duration_score = _band_score(
        features.duration_seconds,
        lower=max(0.20, settings.wake_fast_path_max_seconds * 0.25),
        ideal_lower=0.38,
        ideal_upper=min(settings.wake_fast_path_max_seconds, 1.05),
        upper=1.70,
    )
    voiced_score = _band_score(
        features.voiced_ratio,
        lower=0.18,
        ideal_lower=0.38,
        ideal_upper=0.95,
        upper=1.0,
    )
    centroid_score = _band_score(
        features.spectral_centroid_mean,
        lower=120.0,
        ideal_lower=280.0,
        ideal_upper=2200.0,
        upper=3600.0,
    )
    zcr_score = _band_score(
        features.zero_crossing_rate_mean,
        lower=0.01,
        ideal_lower=0.03,
        ideal_upper=0.22,
        upper=0.38,
    )
    if 2 <= features.syllable_peaks <= 4:
        peak_score = 1.0
    elif features.syllable_peaks in {1, 5}:
        peak_score = 0.55
    else:
        peak_score = 0.2
    snr_score = _noise_support(features.snr_db, settings)
    return float(
        np.clip(
            (0.23 * duration_score)
            + (0.18 * voiced_score)
            + (0.17 * centroid_score)
            + (0.12 * zcr_score)
            + (0.15 * peak_score)
            + (0.15 * snr_score),
            0.0,
            1.0,
        )
    )


def _frame_signal(
    audio: np.ndarray,
    sample_rate: int,
    *,
    frame_ms: float,
    hop_ms: float,
) -> np.ndarray:
    frame_length = max(16, int(sample_rate * frame_ms / 1000.0))
    hop_length = max(8, int(sample_rate * hop_ms / 1000.0))
    if len(audio) < frame_length:
        padded = np.pad(audio, (0, max(0, frame_length - len(audio))))
        return padded[None, :]
    frame_count = 1 + max(0, (len(audio) - frame_length) // hop_length)
    strides = (audio.strides[0] * hop_length, audio.strides[0])
    frames = np.lib.stride_tricks.as_strided(
        audio,
        shape=(frame_count, frame_length),
        strides=strides,
        writeable=False,
    )
    return np.array(frames, copy=True, dtype=np.float32)


def _spectral_gate(audio: np.ndarray, *, sample_rate: int, reduction_strength: float) -> np.ndarray:
    window_size = max(256, int(sample_rate * 0.032))
    hop_size = max(128, window_size // 2)
    frames = _stft(audio, window_size, hop_size)
    power = np.abs(frames) ** 2
    noise_profile = np.quantile(power, 0.25, axis=0)
    gain = 1.0 - (reduction_strength * (noise_profile[None, :] / (power + EPSILON)))
    gain = np.clip(gain, 0.18, 1.0)
    filtered = frames * gain
    return _istft(filtered, window_size, hop_size, output_length=len(audio))


def _echo_suppress(audio: np.ndarray, *, sample_rate: int, strength: float) -> np.ndarray:
    delay_samples = max(1, int(sample_rate * 0.04))
    if len(audio) <= delay_samples or strength <= 0.0:
        return audio
    suppressed = np.array(audio, copy=True)
    suppressed[delay_samples:] -= strength * audio[:-delay_samples]
    return suppressed.astype(np.float32)


def _normalize_rms(audio: np.ndarray, target_rms: float) -> np.ndarray:
    rms = float(np.sqrt(np.mean(np.square(audio)) + EPSILON))
    if rms <= EPSILON:
        return audio.astype(np.float32)
    scaled = audio * (target_rms / rms)
    peak = float(np.max(np.abs(scaled)) + EPSILON)
    if peak > 1.0:
        scaled = scaled / peak
    return scaled.astype(np.float32)


def _normalize_vector(values: np.ndarray) -> np.ndarray:
    values = values.astype(np.float32)
    minimum = float(np.min(values))
    maximum = float(np.max(values))
    if math.isclose(minimum, maximum):
        return np.zeros_like(values, dtype=np.float32)
    return ((values - minimum) / (maximum - minimum + EPSILON)).astype(np.float32)


def _count_syllable_peaks(energy_norm: np.ndarray) -> int:
    if len(energy_norm) == 0:
        return 0
    peaks = 0
    refractory = 2
    last_peak = -refractory
    for index in range(1, len(energy_norm) - 1):
        if index - last_peak < refractory:
            continue
        if (
            energy_norm[index] >= 0.42
            and energy_norm[index] >= energy_norm[index - 1]
            and energy_norm[index] >= energy_norm[index + 1]
        ):
            peaks += 1
            last_peak = index
    return peaks


def _dtw_similarity(left: np.ndarray, right: np.ndarray) -> float:
    if left.size == 0 or right.size == 0:
        return 0.0
    left_len = left.shape[0]
    right_len = right.shape[0]
    band = max(4, abs(left_len - right_len) + max(left_len, right_len) // 3)
    costs = np.full((left_len + 1, right_len + 1), np.inf, dtype=np.float32)
    costs[0, 0] = 0.0
    for left_index in range(1, left_len + 1):
        start = max(1, left_index - band)
        end = min(right_len, left_index + band)
        for right_index in range(start, end + 1):
            distance = float(np.linalg.norm(left[left_index - 1] - right[right_index - 1]))
            costs[left_index, right_index] = distance + min(
                costs[left_index - 1, right_index],
                costs[left_index, right_index - 1],
                costs[left_index - 1, right_index - 1],
            )
    normalized_cost = costs[left_len, right_len] / max(left_len + right_len, 1)
    return float(1.0 / (1.0 + normalized_cost))


def _stft(audio: np.ndarray, window_size: int, hop_size: int) -> np.ndarray:
    padded = np.pad(audio, (0, window_size), mode="constant")
    frames = []
    window = np.hanning(window_size).astype(np.float32)
    for start in range(0, max(1, len(padded) - window_size + 1), hop_size):
        frame = padded[start : start + window_size]
        if len(frame) < window_size:
            frame = np.pad(frame, (0, window_size - len(frame)))
        frames.append(np.fft.rfft(frame * window))
    return np.array(frames, dtype=np.complex64)


def _istft(stft_matrix: np.ndarray, window_size: int, hop_size: int, *, output_length: int) -> np.ndarray:
    window = np.hanning(window_size).astype(np.float32)
    output_size = hop_size * max(0, len(stft_matrix) - 1) + window_size
    signal = np.zeros(output_size, dtype=np.float32)
    normalizer = np.zeros(output_size, dtype=np.float32)
    for index, spectrum in enumerate(stft_matrix):
        frame = np.fft.irfft(spectrum).astype(np.float32)
        start = index * hop_size
        signal[start : start + window_size] += frame[:window_size] * window
        normalizer[start : start + window_size] += window**2
    signal /= np.maximum(normalizer, EPSILON)
    return signal[:output_length]


@lru_cache(maxsize=16)
def _mel_filterbank(sample_rate: int, n_fft: int, n_mels: int) -> np.ndarray:
    fft_bins = n_fft // 2 + 1
    mel_min = _hz_to_mel(20.0)
    mel_max = _hz_to_mel(sample_rate / 2.0)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2, dtype=np.float32)
    hz_points = _mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bank = np.zeros((n_mels, fft_bins), dtype=np.float32)
    for mel_index in range(n_mels):
        left = bins[mel_index]
        center = bins[mel_index + 1]
        right = bins[mel_index + 2]
        if center <= left:
            center = left + 1
        if right <= center:
            right = center + 1
        for freq_bin in range(left, min(center, fft_bins)):
            bank[mel_index, freq_bin] = (freq_bin - left) / max(center - left, 1)
        for freq_bin in range(center, min(right, fft_bins)):
            bank[mel_index, freq_bin] = (right - freq_bin) / max(right - center, 1)
    return bank


@lru_cache(maxsize=8)
def _dct_basis(input_size: int, output_size: int) -> np.ndarray:
    basis = np.zeros((output_size, input_size), dtype=np.float32)
    scale = math.pi / float(input_size)
    for output_index in range(output_size):
        factor = math.sqrt(1.0 / input_size) if output_index == 0 else math.sqrt(2.0 / input_size)
        for input_index in range(input_size):
            basis[output_index, input_index] = factor * math.cos(
                (input_index + 0.5) * output_index * scale
            )
    return basis


def _hz_to_mel(value: float) -> float:
    return 2595.0 * math.log10(1.0 + (value / 700.0))


def _mel_to_hz(values: np.ndarray) -> np.ndarray:
    return 700.0 * (10 ** (values / 2595.0) - 1.0)


def _noise_support(snr_db: float, settings: Settings) -> float:
    baseline = np.clip((snr_db + 4.0) / 18.0, 0.0, 1.0)
    return float(np.clip((0.55 * baseline) + (0.45 * settings.wake_noise_tolerance), 0.0, 1.0))


def _band_score(value: float, *, lower: float, ideal_lower: float, ideal_upper: float, upper: float) -> float:
    if value <= lower or value >= upper:
        return 0.0
    if ideal_lower <= value <= ideal_upper:
        return 1.0
    if value < ideal_lower:
        return float((value - lower) / max(ideal_lower - lower, EPSILON))
    return float((upper - value) / max(upper - ideal_upper, EPSILON))


def _normalize_text(text: str) -> str:
    lowered = text.strip().lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _token_to_syllables(token: str) -> list[str]:
    if token in {"lulu", "luloo", "louloo", "looloo"}:
        return ["lu", "lu"]
    if token in {"lolo", "loloo"}:
        return ["lo", "lo"]
    if token in {"lu", "lou", "loo", "luh"}:
        return ["lu"]
    if token in {"hey", "hay", "he"}:
        return ["hey"]
    if token in {"hello"}:
        return ["he", "lo"]
    if token in {"luma"}:
        return ["lu", "ma"]
    vowels = re.findall(r"[aeiouy]+", token)
    if vowels:
        return [vowels[0]]
    return [token[:2] or "uh"]


def _render_syllable(
    *,
    syllable: str,
    sample_rate: int,
    duration: float,
    pitch_shift: float,
    amplitude: float,
) -> np.ndarray:
    frame_count = max(32, int(sample_rate * duration))
    time_axis = np.arange(frame_count, dtype=np.float32) / max(sample_rate, 1)
    base_frequency = _syllable_frequency(syllable) * (2 ** (pitch_shift / 12.0))
    envelope = np.clip(
        np.sin(np.linspace(0.0, math.pi, frame_count, dtype=np.float32)),
        0.0,
        None,
    ) ** 1.6
    body = (
        np.sin(2 * math.pi * base_frequency * time_axis)
        + 0.45 * np.sin(2 * math.pi * base_frequency * 2.0 * time_axis)
        + 0.18 * np.sin(2 * math.pi * base_frequency * 3.0 * time_axis)
    )
    breath = 0.03 * np.sign(np.sin(2 * math.pi * (base_frequency / 7.0) * time_axis))
    return (amplitude * envelope * (body + breath)).astype(np.float32)


def _syllable_frequency(syllable: str) -> float:
    lookup = {
        "hey": 210.0,
        "he": 205.0,
        "lu": 178.0,
        "lo": 168.0,
        "ma": 188.0,
    }
    if syllable in lookup:
        return lookup[syllable]
    token_hash = sum(ord(char) for char in syllable)
    return 150.0 + float(token_hash % 70)
