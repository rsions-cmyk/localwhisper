from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Protocol

from praattypen.audio import Recording


class Engine(Protocol):
    name: str

    def transcribe(self, audio_path: Path) -> str:
        ...

    def transcribe_recording(self, recording: Recording) -> str:
        ...

    def warm_up(self) -> None:
        ...


class Cleanup(Protocol):
    def clean(self, text: str) -> str:
        ...


class DictationPipeline:
    def __init__(
        self,
        engine: Engine,
        cleanup: Cleanup,
        inject,
        on_status: Callable[[str], None] | None = None,
        fix_casing: Callable[[str], str] | None = None,
        transform: Callable[[str], str] | None = None,
    ) -> None:
        self.fix_casing = fix_casing or (lambda text: text)
        # Mode step (translation, feedback) after cleanup, before casing.
        self.transform = transform or (lambda text: text)
        self.engine = engine
        self.cleanup = cleanup
        self.inject = inject
        self.on_status = on_status or (lambda _message: None)
        # Whether the last transcript was pasted or only left on the clipboard.
        # The status window shows the difference; without it a transcript that
        # never arrived looks identical to one that did.
        self.last_pasted = True

    def process_audio_file(self, audio_path: Path) -> str:
        self.on_status(f"Transcriberen met {self.engine.name}...")
        return self._deliver(self.engine.transcribe(audio_path))

    def process_recording(self, recording: Recording) -> str:
        self.on_status(f"Transcriberen met {self.engine.name}...")
        return self._deliver(self.engine.transcribe_recording(recording))

    def _deliver(self, raw_transcript: str) -> str:
        transcript = raw_transcript.strip()
        if not transcript:
            self.on_status("Geen transcript ontvangen.")
            return ""

        self.on_status(f"Transcript: {transcript}")
        cleaned = self.fix_casing(self.transform(self.cleanup.clean(transcript).strip()).strip())
        if not cleaned:
            self.on_status("Opschoning gaf lege tekst terug.")
            return ""

        self.on_status("Plakken...")
        pasted = self.inject(cleaned)
        self.last_pasted = pasted is not False
        if pasted is False:
            # inject() left the text on the clipboard; saying "Geplakt" here
            # would send you looking for a transcript that never arrived.
            self.on_status("Niet geplakt: de tekst staat op je klembord, plak met Cmd+V.")
        else:
            self.on_status("Geplakt.")
        return cleaned
