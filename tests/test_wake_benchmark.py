from __future__ import annotations

import numpy as np

from audio_handler import AudioHandler
from config import Settings
from wake_benchmark import build_synthetic_wake_corpus, run_synthetic_wake_benchmark
from wake_detection import mix_with_noise, synthesize_phrase_audio


def build_settings() -> Settings:
    return Settings(
        wake_match_score_threshold=0.84,
        wake_confidence_threshold=0.73,
        wake_acoustic_candidate_threshold=0.54,
        wake_fast_path_threshold=0.89,
        wake_noise_tolerance=0.7,
        wake_mispronunciation_tolerance=0.78,
        wake_noise_reduction_strength=0.64,
        wake_echo_suppression_strength=0.24,
    )


def test_synthetic_corpus_contains_100_plus_samples() -> None:
    corpus = build_synthetic_wake_corpus()

    assert len(corpus) >= 100
    assert any(sample.expected_match for sample in corpus)
    assert any(not sample.expected_match for sample in corpus)


def test_wake_audio_analysis_extracts_real_time_features() -> None:
    settings = build_settings()
    handler = AudioHandler(settings)
    audio = synthesize_phrase_audio("hey lulu", settings.sample_rate)

    analysis = handler.analyze_wake_audio(audio)

    assert analysis.feature_frames > 0
    assert analysis.spectral_centroid_mean > 0.0
    assert analysis.zero_crossing_rate_mean >= 0.0
    assert analysis.dtw_score > 0.0


def test_wake_match_uses_probabilistic_confidence_for_mispronunciations() -> None:
    settings = build_settings()
    handler = AudioHandler(settings)
    audio = synthesize_phrase_audio(
        "hay lou lou",
        settings.sample_rate,
        tempo_scale=1.08,
        pitch_shift=-0.4,
    )
    noisy_audio = mix_with_noise(
        audio,
        "office",
        settings.sample_rate,
        snr_db=8.0,
        seed=41,
    )
    analysis = handler.analyze_wake_audio(noisy_audio)

    match = handler.match_wake_phrase("hay lou lou set a timer", analysis)

    assert analysis.confidence >= 0.0
    assert match.matched is True
    assert match.transcript_score >= settings.wake_match_score_threshold
    assert match.remainder == "set a timer"


def test_fast_path_accepts_short_clean_bare_wake_phrase() -> None:
    settings = build_settings()
    handler = AudioHandler(settings)
    audio = synthesize_phrase_audio("hey lulu", settings.sample_rate, amplitude=0.95)

    analysis = handler.analyze_wake_audio(audio)

    assert analysis.fast_path_eligible is True
    match = handler.build_fast_path_wake_match(analysis)
    assert match.matched is True
    assert match.reason == "acoustic-fast-path"


def test_synthetic_benchmark_meets_accuracy_and_latency_targets() -> None:
    metrics = run_synthetic_wake_benchmark(build_settings())

    assert metrics["total_samples"] >= 100
    assert metrics["accuracy"] >= 0.95
    assert metrics["false_positive_rate"] <= 0.05
    assert metrics["false_negative_rate"] <= 0.05
    assert metrics["p95_latency_ms"] < 200.0
    assert metrics["average_latency_ms"] < 120.0
