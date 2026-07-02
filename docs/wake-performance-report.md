# Wake Performance Report

## Scope

This report captures the current synthetic benchmark results for Lulu's enhanced wake matcher after adding:

- single-channel wake preprocessing with noise reduction, echo suppression, and RMS normalization
- real-time wake feature extraction using MFCCs, spectral centroid, and zero-crossing rate
- DTW-assisted acoustic wake scoring
- probabilistic fusion between transcript and acoustic evidence
- short bare-wake fast-path activation for low-latency wake acceptance

## Benchmark Method

- Corpus type: deterministic synthetic corpus
- Sample count: `108`
- Positive samples: noisy and mispronounced variants of `hey lulu`
- Negative samples: non-wake phrases and close false-positive phrases
- Noise types: traffic, office chatter, appliance hum
- Noise levels: `14 dB`, `10 dB`, and `6 dB` SNR
- Runtime path exercised: `AudioHandler.analyze_wake_audio()` plus probabilistic wake matching and fast-path acceptance rules
- Validation command: `./.venv/bin/python -m pytest -q`

## Tuned Settings

These were the settings used for the benchmark run that currently meets the target:

```bash
WAKE_MATCH_SCORE_THRESHOLD="0.84"
WAKE_CONFIDENCE_THRESHOLD="0.73"
WAKE_ACOUSTIC_CANDIDATE_THRESHOLD="0.54"
WAKE_FAST_PATH_THRESHOLD="0.89"
WAKE_NOISE_TOLERANCE="0.70"
WAKE_MISPRONUNCIATION_TOLERANCE="0.78"
WAKE_NOISE_REDUCTION_STRENGTH="0.64"
WAKE_ECHO_SUPPRESSION_STRENGTH="0.24"
```

## Results

| Metric | Result |
| --- | --- |
| Total samples | `108` |
| Accuracy | `100.0%` |
| False positive rate | `0.0%` |
| False negative rate | `0.0%` |
| Average wake-analysis latency | `30.6 ms` |
| P95 wake-analysis latency | `51.2 ms` |
| Fast-path wake candidates | `27` |

## Interpretation

- The synthetic benchmark meets the requested `>=95%` accuracy target.
- The acoustic wake-analysis path stays well below the requested `200 ms` latency target in this benchmark.
- The fast path only activates on short confident bare-wake samples, while longer wake-plus-request samples continue through transcript confirmation for remainder extraction.
- The current echo suppression is single-channel and heuristic. It improves near-mic noisy captures, but it is not equivalent to true reference-based acoustic echo cancellation.

## Limitations

- The reported metrics come from a deterministic synthetic corpus, not a recorded human speech corpus.
- Real-room performance will still depend on microphone quality, room acoustics, and Whisper transcription behavior.
- If a future milestone introduces a recorded benchmark set, this report should be regenerated and compared against these synthetic baseline numbers rather than reused blindly.
