from pathlib import Path

import numpy as np

from praattypen.audio import Recording
from praattypen.pipeline import DictationPipeline


class FakeEngine:
    name = "fake"

    def __init__(self) -> None:
        self.paths: list[Path] = []
        self.recordings: list[Recording] = []

    def transcribe(self, audio_path: Path) -> str:
        self.paths.append(audio_path)
        return "ruwe tekst"

    def transcribe_recording(self, recording: Recording) -> str:
        self.recordings.append(recording)
        return "ruwe tekst"


class FakeCleanup:
    def __init__(self) -> None:
        self.inputs: list[str] = []

    def clean(self, text: str) -> str:
        self.inputs.append(text)
        return "Schone tekst."


def test_pipeline_transcribes_cleans_and_injects_text(tmp_path: Path) -> None:
    audio_path = tmp_path / "spraak.wav"
    audio_path.write_bytes(b"fake wav")
    engine = FakeEngine()
    cleanup = FakeCleanup()
    injected: list[str] = []

    pipeline = DictationPipeline(
        engine=engine,
        cleanup=cleanup,
        inject=injected.append,
    )

    result = pipeline.process_audio_file(audio_path)

    assert result == "Schone tekst."
    assert engine.paths == [audio_path]
    assert cleanup.inputs == ["ruwe tekst"]
    assert injected == ["Schone tekst."]


def test_pipeline_transcribes_an_in_memory_recording_without_touching_disk() -> None:
    engine = FakeEngine()
    injected: list[str] = []
    recording = Recording(np.full(16000, 0.1, dtype=np.float32), 16000)

    pipeline = DictationPipeline(engine=engine, cleanup=FakeCleanup(), inject=injected.append)
    result = pipeline.process_recording(recording)

    assert result == "Schone tekst."
    assert engine.recordings == [recording]
    assert engine.paths == []
    assert injected == ["Schone tekst."]


def test_pipeline_does_not_inject_empty_transcript(tmp_path: Path) -> None:
    class EmptyEngine:
        name = "empty"

        def transcribe(self, _audio_path: Path) -> str:
            return " "

    injected: list[str] = []
    pipeline = DictationPipeline(
        engine=EmptyEngine(),
        cleanup=FakeCleanup(),
        inject=injected.append,
    )

    result = pipeline.process_audio_file(tmp_path / "empty.wav")

    assert result == ""
    assert injected == []


def test_pipeline_remembers_that_the_transcript_was_pasted(tmp_path: Path) -> None:
    audio_path = tmp_path / "spraak.wav"
    audio_path.write_bytes(b"fake wav")

    pipeline = DictationPipeline(
        engine=FakeEngine(),
        cleanup=FakeCleanup(),
        inject=lambda _text: None,
    )
    pipeline.process_audio_file(audio_path)

    assert pipeline.last_pasted is True


def test_pipeline_remembers_a_clipboard_only_delivery(tmp_path: Path) -> None:
    audio_path = tmp_path / "spraak.wav"
    audio_path.write_bytes(b"fake wav")

    pipeline = DictationPipeline(
        engine=FakeEngine(),
        cleanup=FakeCleanup(),
        inject=lambda _text: False,
    )
    pipeline.process_audio_file(audio_path)

    assert pipeline.last_pasted is False
