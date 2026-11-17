from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import yaml

# Checked first, so an app bundle has a config you can edit without touching the
# checkout. Falls back to the config.yaml next to the code.
USER_CONFIG_PATH = Path.home() / ".config" / "praattypen" / "config.yaml"
REPO_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def default_config_path() -> Path:
    if USER_CONFIG_PATH.exists():
        return USER_CONFIG_PATH
    return REPO_CONFIG_PATH


@dataclass(frozen=True)
class CleanupConfig:
    enabled: bool = False
    provider: str = "ollama"
    model: str = "qwen2.5:3b"
    remove_fillers: bool = True


@dataclass(frozen=True)
class ModelsConfig:
    whisper: str = "mlx-community/whisper-large-v3-turbo-q4"
    parakeet: str = "mlx-community/parakeet-tdt-0.6b-v3"
    groq: str = "whisper-large-v3-turbo"


@dataclass(frozen=True)
class AppConfig:
    hotkey: str = "fn"
    language: str = "nl"
    engine: str = "local_whisper"
    cloud_boost: bool = False
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    paste_method: str = "clipboard"
    sample_rate: int = 16000
    min_speech_seconds: float = 0.3
    min_speech_rms: float = 0.0015
    auto_cache_clean: bool = True
    max_model_cache_gb: float = 3.0
    overlay_enabled: bool = True
    menu_bar_enabled: bool = True
    dock_icon: bool = True
    max_recording_seconds: float = 900.0
    status_window_enabled: bool = True
    history_enabled: bool = True
    history_size: int = 200
    sounds_enabled: bool = True
    sound_volume: float = 0.25
    # Local Ollama model for the "to Dutch" and "Feedback" modes.
    assistant_model: str = "gemma4:12b"


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        return AppConfig()

    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Config file must contain a YAML mapping: {config_path}")

    cleanup_raw = raw.pop("cleanup", {}) or {}
    if not isinstance(cleanup_raw, dict):
        raise ValueError("cleanup config must be a YAML mapping")

    models_raw = raw.pop("models", {}) or {}
    if not isinstance(models_raw, dict):
        raise ValueError("models config must be a YAML mapping")

    cleanup = _merge_dataclass(CleanupConfig(), cleanup_raw)
    models = _merge_dataclass(ModelsConfig(), models_raw)
    return _merge_dataclass(AppConfig(cleanup=cleanup, models=models), raw)


def _merge_dataclass(defaults: Any, values: dict[str, Any]) -> Any:
    allowed = {field.name for field in fields(defaults)}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unknown config key(s): {', '.join(unknown)}")
    return replace(defaults, **values)
