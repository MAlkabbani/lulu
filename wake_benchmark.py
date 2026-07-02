from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from time import perf_counter

from audio_handler import AudioHandler
from config import Settings
from wake_detection import mix_with_noise, synthesize_phrase_audio


@dataclass(frozen=True)
class SyntheticWakeSample:
    name: str
    transcript: str
    audio_phrase: str
    noise_type: str
    snr_db: float
    expected_match: bool
    allows_fast_path: bool


def build_synthetic_wake_corpus() -> list[SyntheticWakeSample]:
    positive_phrases = [
        ("hey lulu", "hey lulu", True),
        ("hey lulu", "hey lulu what time is it", False),
        ("hey lu lu", "hey lu lu", True),
        ("hay lou lou", "hay lou lou set a timer", False),
        ("hey lulu", "um hey lulu", True),
        ("hey luloo", "hey luloo play jazz", False),
    ]
    negative_phrases = [
        ("hey luma", "hey luma", False),
        ("hello lulu", "hello lulu", False),
        ("what time is it", "what time is it", False),
        ("play some music", "play some music", False),
        ("hey blue", "hey blue", False),
        ("hello there", "hello there", False),
    ]
    corpus: list[SyntheticWakeSample] = []
    noises = ["traffic", "office", "appliance"]
    snrs = [14.0, 10.0, 6.0]
    for noise_type in noises:
        for snr_db in snrs:
            for index, (audio_phrase, transcript, allows_fast_path) in enumerate(positive_phrases):
                corpus.append(
                    SyntheticWakeSample(
                        name=f"positive-{noise_type}-{snr_db:.0f}-{index}",
                        transcript=transcript,
                        audio_phrase=audio_phrase,
                        noise_type=noise_type,
                        snr_db=snr_db,
                        expected_match=True,
                        allows_fast_path=allows_fast_path,
                    )
                )
            for index, (audio_phrase, transcript, allows_fast_path) in enumerate(negative_phrases):
                corpus.append(
                    SyntheticWakeSample(
                        name=f"negative-{noise_type}-{snr_db:.0f}-{index}",
                        transcript=transcript,
                        audio_phrase=audio_phrase,
                        noise_type=noise_type,
                        snr_db=snr_db,
                        expected_match=False,
                        allows_fast_path=allows_fast_path,
                    )
                )
    return corpus


def run_synthetic_wake_benchmark(settings: Settings | None = None) -> dict[str, float | int]:
    active_settings = settings or Settings()
    handler = AudioHandler(active_settings)
    corpus = build_synthetic_wake_corpus()
    predictions: list[bool] = []
    latencies_ms: list[float] = []
    false_positives = 0
    false_negatives = 0

    for index, sample in enumerate(corpus):
        tempo_scale = 0.92 + ((index % 5) * 0.06)
        pitch_shift = float((index % 7) - 3) * 0.35
        audio = synthesize_phrase_audio(
            phrase=sample.audio_phrase,
            sample_rate=active_settings.sample_rate,
            tempo_scale=tempo_scale,
            pitch_shift=pitch_shift,
            amplitude=0.88,
        )
        noisy_audio = mix_with_noise(
            audio,
            sample.noise_type,
            active_settings.sample_rate,
            snr_db=sample.snr_db,
            seed=index + 11,
        )
        started = perf_counter()
        analysis = handler.analyze_wake_audio(noisy_audio)
        if analysis.fast_path_eligible and sample.allows_fast_path:
            predicted = True
        else:
            predicted = handler.match_wake_phrase(sample.transcript, analysis).matched
        latencies_ms.append((perf_counter() - started) * 1000.0)
        predictions.append(predicted)
        if predicted and not sample.expected_match:
            false_positives += 1
        if sample.expected_match and not predicted:
            false_negatives += 1

    total = len(corpus)
    correct = sum(
        1 for predicted, sample in zip(predictions, corpus, strict=True) if predicted == sample.expected_match
    )
    sorted_latencies = sorted(latencies_ms)
    p95_index = min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))
    positive_total = sum(1 for sample in corpus if sample.expected_match)
    negative_total = total - positive_total
    return {
        "total_samples": total,
        "accuracy": correct / total,
        "false_positive_rate": false_positives / max(1, negative_total),
        "false_negative_rate": false_negatives / max(1, positive_total),
        "average_latency_ms": mean(latencies_ms),
        "p95_latency_ms": sorted_latencies[p95_index],
        "fast_path_candidates": sum(
            1
            for predicted, sample in zip(predictions, corpus, strict=True)
            if predicted and sample.allows_fast_path
        ),
    }
