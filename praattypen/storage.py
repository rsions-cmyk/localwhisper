from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from praattypen.config import AppConfig


MODEL_PREFIXES = (
    "models--mlx-community--whisper",
    "models--mlx-community--parakeet",
)


@dataclass(frozen=True)
class CachePlan:
    keep: list[Path]
    remove: list[Path]
    reclaimable_bytes: int
    temp_audio: list[Path]
    temp_audio_bytes: int


@dataclass(frozen=True)
class AutoCleanResult:
    removed_paths: int
    reclaimed_bytes: int
    skipped: bool


def huggingface_hub_dir() -> Path:
    return Path.home() / ".cache" / "huggingface" / "hub"


def current_local_model_repos(config: AppConfig) -> set[str]:
    # Every model the Models tab offers is one you may switch back to, so the
    # automatic clean-up must never take it away -- only leftovers it does
    # not know. (It once removed a model mid-download for exactly this.)
    from praattypen.models_catalog import CATALOG

    return {config.models.whisper, config.models.parakeet} | {model.repo for model in CATALOG}


def model_cache_dir_name(repo_id: str) -> str:
    return "models--" + repo_id.replace("/", "--")


def cache_plan(
    config: AppConfig,
    hub_dir: Path | None = None,
    temp_dir: Path | None = None,
) -> CachePlan:
    hub = hub_dir or huggingface_hub_dir()
    keep_names = {model_cache_dir_name(repo) for repo in current_local_model_repos(config)}

    keep: list[Path] = []
    remove: list[Path] = []
    if hub.exists():
        for path in sorted(hub.iterdir()):
            if not path.is_dir() or not path.name.startswith(MODEL_PREFIXES):
                continue
            if path.name in keep_names:
                keep.append(path)
            else:
                remove.append(path)

    temp_audio = find_praattypen_temp_audio(temp_dir or Path(tempfile.gettempdir()))
    return CachePlan(
        keep=keep,
        remove=remove,
        reclaimable_bytes=sum(dir_size(path) for path in remove),
        temp_audio=temp_audio,
        temp_audio_bytes=sum(path.stat().st_size for path in temp_audio if path.exists()),
    )


def auto_clean_cache(
    config: AppConfig,
    max_bytes: int,
    hub_dir: Path | None = None,
    temp_dir: Path | None = None,
) -> AutoCleanResult:
    plan = cache_plan(config, hub_dir=hub_dir, temp_dir=temp_dir)
    total_bytes = sum(dir_size(path) for path in plan.keep) + plan.reclaimable_bytes
    if total_bytes <= max_bytes and not plan.temp_audio:
        return AutoCleanResult(removed_paths=0, reclaimed_bytes=0, skipped=True)

    targets = plan.remove + plan.temp_audio
    reclaimed = plan.reclaimable_bytes + plan.temp_audio_bytes
    removed = remove_paths(targets)
    return AutoCleanResult(removed_paths=removed, reclaimed_bytes=reclaimed, skipped=False)


def find_praattypen_temp_audio(temp_dir: Path) -> list[Path]:
    if not temp_dir.exists():
        return []
    return sorted(path for path in temp_dir.glob("praattypen-*.wav") if path.is_file())


def remove_paths(paths: list[Path]) -> int:
    removed = 0
    for path in paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed += 1
    return removed


def dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def format_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
