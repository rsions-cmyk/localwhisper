from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np

from praattypen.audio import Recording
from praattypen.engines.base import TranscriptionEngine

# One speech model, shared by dictation and the Recordings tab: MLX is not
# safe to drive from two threads at once, so every call takes this lock. Long
# recordings are transcribed in short chunks, so a dictation never waits long.
MLX_LOCK = threading.RLock()

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
        prompt_source=None,
        task_source=None,
    ) -> None:
        # Callable returning "transcribe" or "translate" (speech -> English).
        self.task_source = task_source
        # Callable returning the custom-vocabulary prompt (or None).
        self.prompt_source = prompt_source
        # "nl,en" means: detect the language, but only choose between these.
        # Plain auto-detection misfires on short phrases ("Ja, prima." comes
        # out as Russian), so restricting the choice keeps bilingual use safe.
        allowed = [part.strip() for part in language.split(",") if part.strip()]
        self.allowed_languages = allowed if len(allowed) > 1 else []
        self.language = None if language == "auto" or self.allowed_languages else language
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
        with MLX_LOCK:
            return self._transcribe_unlocked(audio)

    def _transcribe_unlocked(self, audio: str | np.ndarray) -> str:
        try:
            import mlx_whisper
        except ImportError as error:
            raise RuntimeError(
                "mlx-whisper is not installed. Install local STT dependencies with "
                "`.venv/bin/python -m pip install mlx-whisper`."
            ) from error

        kwargs: dict[str, Any] = {"path_or_hf_repo": self.model}
        language = self.language
        if self.allowed_languages:
            if isinstance(audio, str):
                from mlx_whisper.audio import load_audio

                audio = load_audio(audio)
            language = self._pick_language(audio)
        if language:
            kwargs["language"] = language
        prompt = self.prompt_source() if self.prompt_source else None
        if prompt:
            kwargs["initial_prompt"] = prompt
        task = self.task_source() if self.task_source else "transcribe"
        if task == "translate":
            kwargs["task"] = "translate"
        result = mlx_whisper.transcribe(audio, **kwargs)
        return str(result.get("text", "")).strip()

    def _pick_language(self, audio: np.ndarray) -> str:
        import mlx.core as mx
        from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
        from mlx_whisper.transcribe import ModelHolder

        # Same model object mlx_whisper.transcribe uses, so nothing loads twice.
        model = ModelHolder.get_model(self.model, mx.float16)
        mel = log_mel_spectrogram(audio, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        segment = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)
        _, probs = model.detect_language(segment)
        probs = probs[0] if isinstance(probs, list) else probs
        # The first listed language is the default. Real speech in another
        # language scores >0.99; a scrap like "Ja, prima." scores <0.1 for
        # every language, and there a coin flip would often pick wrong.
        primary = self.allowed_languages[0]
        best = max(self.allowed_languages, key=lambda code: probs.get(code, 0.0))
        return best if probs.get(best, 0.0) > 0.5 else primary
