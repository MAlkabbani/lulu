from __future__ import annotations

from types import SimpleNamespace

from audio_handler import MacOSTTS, PhraseChunker, TTSPlaybackError
from config import Settings
from main import _stream_and_chunk


class FakeUI:
    def __init__(self) -> None:
        self.responses: list[str] = []
        self.emitted_chunks: list[str] = []
        self.playback_gaps = 0
        self.tail_merges = 0
        self.state = SimpleNamespace(emitted_chunk_count=0, spoken_chunk_count=0)

    def set_response(self, response: str) -> None:
        self.responses.append(response)

    def record_emitted_chunk(self, chunk: str) -> None:
        self.emitted_chunks.append(chunk)
        self.state.emitted_chunk_count += 1

    def record_playback_gap(self) -> None:
        self.playback_gaps += 1

    def record_tail_merge(self) -> None:
        self.tail_merges += 1


class FakeTTS:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def enqueue_chunk(self, chunk: str) -> None:
        self.chunks.append(chunk)


def build_settings() -> Settings:
    return Settings(
        tts_stream_min_chunk_chars=8,
        tts_stream_start_buffer_chars=18,
        tts_stream_group_target_chars=32,
        tts_stream_max_group_sentences=2,
        tts_stream_tail_merge_chars=24,
        tts_stream_tail_merge_overflow_chars=24,
        tts_stream_soft_chunk_chars=32,
        tts_stream_max_chunk_chars=56,
    )


def test_phrase_chunker_emits_sentence_boundary_chunks() -> None:
    chunker = PhraseChunker(build_settings())

    ready = chunker.push("Thanks for the note. I found it.")

    assert ready == ["Thanks for the note. I found it."]
    assert chunker.finish() == []


def test_phrase_chunker_flushes_tail_without_punctuation() -> None:
    chunker = PhraseChunker(build_settings())

    ready = chunker.push("This stays buffered until the end")

    assert ready == []
    assert chunker.finish() == ["This stays buffered until the end"]


def test_phrase_chunker_does_not_emit_short_leading_punctuation_chunk() -> None:
    chunker = PhraseChunker(
        Settings(
            tts_stream_min_chunk_chars=24,
            tts_stream_soft_chunk_chars=72,
            tts_stream_max_chunk_chars=140,
        )
    )

    ready = chunker.push("Sure, I can help with that request right now.")

    assert ready == ["Sure, I can help with that request right now."]


def test_phrase_chunker_waits_for_sentence_boundary_before_force_split() -> None:
    chunker = PhraseChunker(Settings())

    ready = chunker.push(
        "This response keeps going without punctuation so the current chunker waits for a full sentence instead of chopping the audio early"
    )

    assert ready == []
    assert chunker.finish() == [
        "This response keeps going without punctuation so the current chunker waits for a full sentence instead of chopping the audio early"
    ]


def test_phrase_chunker_groups_two_short_sentences_into_one_chunk() -> None:
    chunker = PhraseChunker(
        Settings(
            tts_stream_min_chunk_chars=8,
            tts_stream_group_target_chars=28,
            tts_stream_max_group_sentences=2,
            tts_stream_soft_chunk_chars=28,
            tts_stream_max_chunk_chars=80,
        )
    )

    ready = chunker.push("Short one. Short two. Third sentence.")

    assert ready == ["Short one. Short two.", "Third sentence."]
    assert chunker.finish() == []


def test_phrase_chunker_prefers_clause_break_before_hard_split() -> None:
    chunker = PhraseChunker(
        Settings(
            tts_stream_min_chunk_chars=8,
            tts_stream_group_target_chars=32,
            tts_stream_max_group_sentences=1,
            tts_stream_clause_boundary_chars=24,
            tts_stream_soft_chunk_chars=40,
            tts_stream_max_chunk_chars=56,
        )
    )

    ready = chunker.push(
        "This answer has a useful pause, so the chunker can break here before it hard splits the rest"
    )

    assert ready[0] == "This answer has a useful pause,"
    assert ready[1] == "so the chunker can break here before it hard splits the"
    assert chunker.finish() == ["rest"]


def test_stream_and_chunk_accumulates_text_and_emits_in_order() -> None:
    chunker = PhraseChunker(build_settings())
    tts = FakeTTS()
    ui = FakeUI()
    response_parts: list[str] = []

    streamed = list(
        _stream_and_chunk(
            stream_source=iter(["Hello there, ", "friend."]),
            settings=build_settings(),
            chunker=chunker,
            tts=tts,
            ui=ui,
            response_parts=response_parts,
        )
    )

    assert streamed == ["Hello there, ", "friend."]
    assert "".join(response_parts) == "Hello there, friend."
    assert tts.chunks == ["Hello there, friend."]
    assert ui.emitted_chunks == ["Hello there, friend."]


def test_stream_and_chunk_delays_first_playback_until_buffer_threshold() -> None:
    settings = Settings(
        tts_stream_min_chunk_chars=8,
        tts_stream_start_buffer_chars=40,
        tts_stream_group_target_chars=26,
        tts_stream_max_group_sentences=1,
        tts_stream_soft_chunk_chars=28,
        tts_stream_max_chunk_chars=56,
    )
    chunker = PhraseChunker(settings)
    tts = FakeTTS()
    ui = FakeUI()
    response_parts: list[str] = []

    stream = _stream_and_chunk(
        stream_source=iter(["Hello world. ", "Another sentence. ", "Final bit."]),
        settings=settings,
        chunker=chunker,
        tts=tts,
        ui=ui,
        response_parts=response_parts,
    )

    assert next(stream) == "Hello world. "
    assert tts.chunks == []
    assert next(stream) == "Another sentence. "
    assert tts.chunks == ["Hello world."]
    assert list(stream) == ["Final bit."]
    assert tts.chunks == ["Hello world.", "Another sentence.", "Final bit."]
    assert ui.playback_gaps == 1


def test_stream_and_chunk_merges_short_final_tail_before_playback() -> None:
    settings = Settings()
    chunker = PhraseChunker(settings)
    tts = FakeTTS()
    ui = FakeUI()
    response_parts: list[str] = []
    text = " ".join(["word"] * 52) + " done now."

    list(
        _stream_and_chunk(
            stream_source=iter([text[:200], text[200:]]),
            settings=settings,
            chunker=chunker,
            tts=tts,
            ui=ui,
            response_parts=response_parts,
        )
    )

    assert tts.chunks == [text]
    assert ui.tail_merges == 1


def test_macos_tts_queue_preserves_chunk_order(monkeypatch) -> None:
    spoken: list[str] = []

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> SimpleNamespace:
        spoken.append(command[1])
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("audio_handler.subprocess.run", fake_run)
    tts = MacOSTTS()

    try:
        tts.start_turn()
        tts.enqueue_chunk("first chunk")
        tts.enqueue_chunk("second chunk")
        tts.finish_turn()
    finally:
        tts.close()

    assert spoken == ["first chunk", "second chunk"]


def test_macos_tts_reports_failures_without_marking_chunk_spoken(monkeypatch) -> None:
    spoken: list[str] = []
    reported_errors: list[TTSPlaybackError] = []

    def fake_run(
        command: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stderr=f"failed to speak {command[1]}")

    monkeypatch.setattr("audio_handler.subprocess.run", fake_run)
    tts = MacOSTTS()
    tts.set_on_chunk_spoken(spoken.append)
    tts.set_on_chunk_error(reported_errors.append)

    try:
        tts.start_turn()
        tts.enqueue_chunk("broken chunk")
        turn_errors = tts.finish_turn()
    finally:
        tts.close()

    assert spoken == []
    assert len(reported_errors) == 1
    assert len(turn_errors) == 1
    assert reported_errors[0].chunk == "broken chunk"
    assert "failed to speak broken chunk" in str(turn_errors[0])
