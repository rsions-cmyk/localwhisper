import sys
import types
from pathlib import Path

import numpy as np

from praattypen.audio import Recording
from praattypen.config import AppConfig
from praattypen.engines import create_engine
from praattypen.engines.base import TranscriptionEngine
from praattypen.engines.local_parakeet import LocalParakeetEngine
from praattypen.engines.local_whisper import LocalWhisperEngine


def test_create_engine_prefers_cloud_when_cloud_boost_is_enabled() -> None:
    config = AppConfig(engine="local_whisper", cloud_boost=True)

    engine = create_engine(config)

    assert engine.name == "cloud_groq"


def test_create_engine_uses_configured_local_engine() -> None:
    config = AppConfig(engine="local_whisper", cloud_boost=False)

    engine = create_engine(config)

    assert engine.name == "local_whisper"
    assert isinstance(engine, TranscriptionEngine)


def test_create_engine_rejects_unknown_engine() -> None:
    config = AppConfig(engine="missing")

    try:
        create_engine(config)
    except ValueError as error:
        assert "Unknown engine" in str(error)
    else:
        raise AssertionError("Expected ValueError")


class FileOnlyEngine(TranscriptionEngine):
    """Stands in for the engines that can only be handed a path."""

    name = "file_only"

    def __init__(self) -> None:
        self.seen: list[Path] = []

    def transcribe(self, audio_path: Path) -> str:
        self.seen.append(audio_path)
        assert audio_path.exists(), "temp WAV must exist while transcribing"
        return "tekst"


def fake_mlx_whisper(monkeypatch, text: str = "tekst"):
    """Replace mlx_whisper with a recorder of what it was handed."""
    calls: list[dict] = []

    def transcribe(audio, **kwargs):
        calls.append({"audio": audio, **kwargs})
        return {"text": text}

    module = types.ModuleType("mlx_whisper")
    module.transcribe = transcribe
    monkeypatch.setitem(sys.modules, "mlx_whisper", module)
    return calls


def test_file_only_engine_spills_a_recording_to_a_temp_wav_and_removes_it() -> None:
    engine = FileOnlyEngine()
    recording = Recording(np.full(16000, 0.1, dtype=np.float32), 16000)

    assert engine.transcribe_recording(recording) == "tekst"

    audio_path = engine.seen[0]
    assert audio_path.name.startswith("praattypen-")
    assert not audio_path.exists()


def test_warm_up_is_a_no_op_for_engines_without_a_cold_start() -> None:
    assert FileOnlyEngine().warm_up() is None


def test_local_whisper_hands_samples_straight_to_the_model(monkeypatch) -> None:
    calls = fake_mlx_whisper(monkeypatch)
    engine = LocalWhisperEngine(language="nl", model="model-id")
    samples = np.full(16000, 0.1, dtype=np.float32)

    assert engine.transcribe_recording(Recording(samples, 16000)) == "tekst"

    assert calls[0]["audio"] is samples
    assert calls[0]["path_or_hf_repo"] == "model-id"
    assert calls[0]["language"] == "nl"


def test_local_whisper_falls_back_to_a_file_for_other_sample_rates(monkeypatch) -> None:
    calls = fake_mlx_whisper(monkeypatch)
    engine = LocalWhisperEngine(model="model-id")

    engine.transcribe_recording(Recording(np.full(44100, 0.1, dtype=np.float32), 44100))

    # A path, so ffmpeg resamples it: raw samples would be transcribed at the
    # wrong speed.
    assert isinstance(calls[0]["audio"], str)
    assert calls[0]["audio"].endswith(".wav")


def test_local_whisper_warm_up_runs_the_model_once_on_silence(monkeypatch) -> None:
    calls = fake_mlx_whisper(monkeypatch, text="")

    LocalWhisperEngine(model="model-id").warm_up()

    assert len(calls) == 1
    assert not calls[0]["audio"].any()


def test_local_whisper_omits_language_when_set_to_auto(monkeypatch) -> None:
    calls = fake_mlx_whisper(monkeypatch)

    LocalWhisperEngine(language="auto").transcribe_recording(
        Recording(np.zeros(16000, dtype=np.float32), 16000)
    )

    assert "language" not in calls[0]


def test_local_parakeet_command_uses_current_venv_script(tmp_path) -> None:
    engine = LocalParakeetEngine(model="model-id")

    command = engine.build_command(tmp_path / "audio.wav", tmp_path / "out")

    assert command[0].endswith("parakeet-mlx")
    assert command[1:] == [
        str(tmp_path / "audio.wav"),
        "--model",
        "model-id",
        "--output-dir",
        str(tmp_path / "out"),
        "--output-format",
        "txt",
    ]
