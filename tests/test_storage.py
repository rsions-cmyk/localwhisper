from pathlib import Path

from praattypen.config import AppConfig, ModelsConfig
from praattypen.storage import (
    auto_clean_cache,
    cache_plan,
    current_local_model_repos,
    dir_size,
    find_praattypen_temp_audio,
    model_cache_dir_name,
    remove_paths,
)


def test_model_cache_dir_name_matches_huggingface_layout() -> None:
    assert (
        model_cache_dir_name("mlx-community/whisper-large-v3-turbo-q4")
        == "models--mlx-community--whisper-large-v3-turbo-q4"
    )


def test_current_local_model_repos_keeps_configured_local_models() -> None:
    config = AppConfig(
        engine="local_whisper",
        models=ModelsConfig(
            whisper="mlx-community/whisper-large-v3-turbo-q4",
            parakeet="mlx-community/parakeet-tdt-0.6b-v3",
            groq="whisper-large-v3-turbo",
        ),
    )

    assert current_local_model_repos(config) == {
        "mlx-community/whisper-large-v3-turbo-q4",
        "mlx-community/parakeet-tdt-0.6b-v3",
    }


def test_cache_plan_marks_unconfigured_whisper_models_as_removable(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    keep = hub / "models--mlx-community--whisper-large-v3-turbo-q4"
    stale = hub / "models--mlx-community--whisper-tiny"
    keep.mkdir(parents=True)
    stale.mkdir(parents=True)
    (keep / "weights.npz").write_bytes(b"123")
    (stale / "weights.npz").write_bytes(b"4567")

    config = AppConfig(
        models=ModelsConfig(
            whisper="mlx-community/whisper-large-v3-turbo-q4",
            parakeet="mlx-community/parakeet-tdt-0.6b-v3",
        )
    )

    plan = cache_plan(config, hub)

    assert plan.keep == [keep]
    assert plan.remove == [stale]
    assert plan.reclaimable_bytes == 4


def test_find_praattypen_temp_audio_only_matches_our_prefix(tmp_path: Path) -> None:
    own = tmp_path / "praattypen-abc.wav"
    other = tmp_path / "tmpabc.wav"
    own.write_bytes(b"1")
    other.write_bytes(b"2")

    assert find_praattypen_temp_audio(tmp_path) == [own]


def test_remove_paths_removes_files_and_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "praattypen-a.wav"
    dir_path = tmp_path / "models--mlx-community--whisper-tiny"
    file_path.write_bytes(b"1")
    dir_path.mkdir()
    (dir_path / "weights.npz").write_bytes(b"2")

    removed = remove_paths([file_path, dir_path])

    assert removed == 2
    assert not file_path.exists()
    assert not dir_path.exists()


def test_dir_size_sums_nested_files(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    (tmp_path / "one").write_bytes(b"12")
    (nested / "two").write_bytes(b"345")

    assert dir_size(tmp_path) == 5


def test_auto_clean_cache_removes_unused_models_when_over_limit(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    keep = hub / "models--mlx-community--whisper-large-v3-turbo-q4"
    stale = hub / "models--mlx-community--whisper-tiny"
    keep.mkdir(parents=True)
    stale.mkdir(parents=True)
    (keep / "weights.npz").write_bytes(b"12345")
    (stale / "weights.npz").write_bytes(b"67890")
    config = AppConfig(models=ModelsConfig(whisper="mlx-community/whisper-large-v3-turbo-q4"))

    result = auto_clean_cache(config, max_bytes=6, hub_dir=hub, temp_dir=tmp_path)

    assert result.removed_paths == 1
    assert result.reclaimed_bytes == 5
    assert keep.exists()
    assert not stale.exists()


def test_auto_clean_cache_does_nothing_when_under_limit(tmp_path: Path) -> None:
    hub = tmp_path / "hub"
    stale = hub / "models--mlx-community--whisper-tiny"
    stale.mkdir(parents=True)
    (stale / "weights.npz").write_bytes(b"123")
    config = AppConfig(models=ModelsConfig(whisper="mlx-community/whisper-large-v3-turbo-q4"))

    result = auto_clean_cache(config, max_bytes=999, hub_dir=hub, temp_dir=tmp_path)

    assert result.removed_paths == 0
    assert stale.exists()
