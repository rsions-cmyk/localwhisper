from pathlib import Path

from praattypen.config import AppConfig, CleanupConfig, load_config


def test_load_config_merges_yaml_with_defaults(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
hotkey: "f5"
language: "en"
engine: "local_whisper"
cleanup:
  enabled: false
  model: "llama3.2:3b"
sample_rate: 24000
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config == AppConfig(
        hotkey="f5",
        language="en",
        engine="local_whisper",
        cloud_boost=False,
        cleanup=CleanupConfig(
            enabled=False,
            provider="ollama",
            model="llama3.2:3b",
            remove_fillers=True,
        ),
        paste_method="clipboard",
        sample_rate=24000,
    )


def test_load_config_returns_defaults_when_file_is_missing(tmp_path: Path) -> None:
    config = load_config(tmp_path / "missing.yaml")

    assert config.hotkey == "fn"
    assert config.language == "nl"
    assert config.engine == "local_whisper"
    assert config.models.whisper == "mlx-community/whisper-large-v3-turbo-q4"
    assert config.cleanup.enabled is False
    assert config.sample_rate == 16000
    assert config.min_speech_seconds == 0.3
    assert config.auto_cache_clean is True
    assert config.max_model_cache_gb == 3.0
    assert config.overlay_enabled is True


def test_load_config_merges_models_section(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
models:
  whisper: "mlx-community/whisper-tiny"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.models.whisper == "mlx-community/whisper-tiny"
    assert config.models.parakeet == "mlx-community/parakeet-tdt-0.6b-v3"
