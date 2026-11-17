from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from praattypen.audio import Recording
from praattypen.engines.base import TranscriptionEngine

# mlx-whisper takes raw samples at this rate only; it does not resample them.
WHISPER_SAMPLE_RATE = 16000

# Long enough to run the encoder over a real (padded) window, short enough that
# startup does not stall on it.
WARM_UP_SECONDS = 0.5


class LocalWhisperEngine(TranscriptionEngine):
    name = "local_whisper"

    def __init__(
        self,
        language: str = "nl",
        model: str = "mlx-community/whisper-large-v3-turbo",
    ) -> None:
        self.language = None if language == "auto" else language
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        return self._transcribe(str(audio_path))

    def transcribe_recording(self, recording: Recording) -> str:
        # Handing mlx-whisper a path makes it shell out to ffmpeg to decode the
        # file back into exactly the samples we already have. Passing the array
        # skips both the WAV write and that subprocess.
        if recording.sample_rate != WHISPER_SAMPLE_RATE:
            return super().transcribe_recording(recording)
        return self._transcribe(recording.samples)

    def warm_up(self) -> None:
        # mlx-whisper caches the loaded model per process, but only from the
        # first transcribe onwards. Without this the user's first dictation of
        # the session pays the whole load plus Metal kernel compilation.
        self._transcribe(np.zeros(int(WARM_UP_SECONDS * WHISPER_SAMPLE_RATE), dtype=np.float32))

    def _transcribe(self, audio: str | np.ndarray) -> str:
        try:
            import mlx_whisper
        except ImportError as error:
            raise RuntimeError(
                "mlx-whisper is not installed. Install local STT dependencies with "
                "`.venv/bin/python -m pip install mlx-whisper`."
            ) from error

        kwargs: dict[str, Any] = {"path_or_hf_repo": self.model}
        if self.language:
            kwargs["language"] = self.language
        result = mlx_whisper.transcribe(audio, **kwargs)
        return str(result.get("text", "")).strip()
