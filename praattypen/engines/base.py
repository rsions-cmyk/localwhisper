from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from praattypen.audio import Recording


class TranscriptionEngine(ABC):
    name: str

    @abstractmethod
    def transcribe(self, audio_path: Path) -> str:
        """Return transcribed text for a WAV/audio file."""

    def transcribe_recording(self, recording: Recording) -> str:
        """Return transcribed text for audio that is still in memory.

        Spills to a temp WAV by default, for engines that can only be handed a
        file (a CLI subprocess, an upload). Engines that accept raw samples
        override this to keep the recording off the disk entirely.
        """
        handle = tempfile.NamedTemporaryFile(prefix="praattypen-", suffix=".wav", delete=False)
        handle.close()
        audio_path = Path(handle.name)
        try:
            recording.write_wav(audio_path)
            return self.transcribe(audio_path)
        finally:
            audio_path.unlink(missing_ok=True)

    def warm_up(self) -> None:
        """Pay the one-off model load before the first real dictation.

        A no-op unless the engine has a cold start worth hiding.
        """
        return None
