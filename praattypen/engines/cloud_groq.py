from __future__ import annotations

from pathlib import Path

from praattypen.engines.base import TranscriptionEngine


class CloudGroqEngine(TranscriptionEngine):
    name = "cloud_groq"

    def __init__(self, language: str = "nl", model: str = "whisper-large-v3-turbo") -> None:
        self.language = None if language == "auto" else language
        self.model = model

    def transcribe(self, audio_path: Path) -> str:
        try:
            from groq import Groq
        except ImportError as error:
            raise RuntimeError(
                "groq is not installed. Install it with `.venv/bin/python -m pip install groq`."
            ) from error

        client = Groq()
        with audio_path.open("rb") as audio_file:
            kwargs = {
                "file": audio_file,
                "model": self.model,
                "response_format": "text",
            }
            if self.language:
                kwargs["language"] = self.language
            result = client.audio.transcriptions.create(**kwargs)
        return str(result).strip()
