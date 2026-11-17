from __future__ import annotations

from praattypen.config import AppConfig
from praattypen.engines.base import TranscriptionEngine
from praattypen.engines.cloud_groq import CloudGroqEngine
from praattypen.engines.local_parakeet import LocalParakeetEngine
from praattypen.engines.local_whisper import LocalWhisperEngine


def create_engine(
    config: AppConfig, prompt_source=None, task_source=None
) -> TranscriptionEngine:
    engine_name = "cloud_groq" if config.cloud_boost else config.engine
    if engine_name == "local_whisper":
        return LocalWhisperEngine(
            language=config.language, model=config.models.whisper,
            prompt_source=prompt_source,
            task_source=task_source,
        )
    if engine_name == "local_parakeet":
        return LocalParakeetEngine(model=config.models.parakeet)
    if engine_name == "cloud_groq":
        return CloudGroqEngine(language=config.language, model=config.models.groq)
    raise ValueError(f"Unknown engine: {engine_name}")
