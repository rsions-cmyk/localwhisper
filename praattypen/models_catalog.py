"""Which speech models exist, which suit this Mac, and which are installed.

Shown in the window's Models tab. Three kinds of evidence, kept apart so the
tab never claims more than it knows:

- this Mac: chip and memory, read from the system;
- each model: download size and memory needed (published figures);
- speed: *measured* from your own dictation history, or "not measured yet".
"""

from __future__ import annotations

import platform
import re
import subprocess
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

HF_HUB = Path.home() / ".cache" / "huggingface" / "hub"
# Other apps on this Mac that keep MLX models in the same layout; reusing
# their copy saves a multi-GB download.
EXTRA_MODEL_DIRS = [Path.home() / ".omlx" / "models", Path.home() / ".lmstudio" / "models"]


@dataclass(frozen=True)
class SpeechModel:
    repo: str
    title: str
    blurb: str
    download_gb: float
    memory_gb: float
    quality: int  # 1..5, relative, for the dots in the UI


CATALOG = [
    SpeechModel(
        "mlx-community/whisper-large-v3-turbo-q4",
        "Whisper Large v3 Turbo · 4-bit",
        "Fastest. Very good for everyday dictation on any Apple Silicon Mac.",
        0.45, 1.0, 4,
    ),
    SpeechModel(
        "mlx-community/whisper-large-v3-turbo",
        "Whisper Large v3 Turbo",
        "Full precision Turbo: a little more accurate, a little slower to load.",
        1.6, 2.0, 4,
    ),
    SpeechModel(
        "mlx-community/whisper-large-v3-mlx",
        "Whisper Large v3",
        "Most accurate, best punctuation. About twice as slow as Turbo; "
        "comfortable on a Pro or Max chip with 16 GB or more.",
        3.1, 4.0, 5,
    ),
]


@dataclass(frozen=True)
class SystemInfo:
    chip: str
    memory_gb: int
    macos: str
    apple_silicon: bool

    @property
    def tier(self) -> str:
        """'max', 'pro' or 'base', from the chip name."""
        name = self.chip.lower()
        if "ultra" in name or "max" in name:
            return "max"
        if "pro" in name:
            return "pro"
        return "base"

    def summary(self) -> str:
        return f"{self.chip} · {self.memory_gb} GB memory · macOS {self.macos}"


def system_info() -> SystemInfo:
    def sysctl(name: str) -> str:
        try:
            return subprocess.run(
                ["sysctl", "-n", name], capture_output=True, text=True, timeout=2
            ).stdout.strip()
        except Exception:  # noqa: BLE001
            return ""

    chip = sysctl("machdep.cpu.brand_string") or platform.processor() or "Unknown chip"
    try:
        memory = round(int(sysctl("hw.memsize")) / 1024**3)
    except ValueError:
        memory = 0
    return SystemInfo(
        chip=chip,
        memory_gb=memory,
        macos=platform.mac_ver()[0] or "?",
        apple_silicon=platform.machine() == "arm64",
    )


def recommend(info: SystemInfo) -> str:
    """The repo that best balances accuracy and speed on this Mac."""
    if info.memory_gb >= 16 and info.tier in ("pro", "max"):
        return "mlx-community/whisper-large-v3-mlx"
    if info.memory_gb >= 16:
        return "mlx-community/whisper-large-v3-turbo"
    return "mlx-community/whisper-large-v3-turbo-q4"


def fit_note(model: SpeechModel, info: SystemInfo) -> str:
    if not info.apple_silicon:
        return "Needs an Apple Silicon Mac"
    if model.memory_gb > info.memory_gb * 0.4:
        return "May be slow with this much memory"
    return ""


def local_path(repo: str) -> Path | None:
    """Where this model's weights already are on disk, if anywhere."""
    snapshots = HF_HUB / f"models--{repo.replace('/', '--')}" / "snapshots"
    if snapshots.is_dir():
        for snapshot in sorted(snapshots.iterdir(), reverse=True):
            if _has_weights(snapshot):
                return snapshot
    for root in EXTRA_MODEL_DIRS:
        candidate = root / repo
        if _has_weights(candidate):
            return candidate
    return None


def _has_weights(folder: Path) -> bool:
    return any((folder / name).exists() for name in ("weights.npz", "weights.safetensors", "model.safetensors"))


def same_model(configured: str, repo: str) -> bool:
    """Is the configured value (a repo id or a local folder) this catalog model?"""
    if configured == repo:
        return True
    return configured.rstrip("/").endswith(repo) or Path(configured).name == repo.split("/")[-1]


def measured_speed(entries: Iterable, repo: str) -> float | None:
    """Seconds of processing per second of speech, from dictation history."""
    ratios = [
        entry.transcribe_seconds / entry.duration_seconds
        for entry in entries
        if entry.duration_seconds >= 2 and entry.transcribe_seconds > 0 and same_model(entry.model, repo)
    ]
    if len(ratios) < 3:
        return None
    ratios.sort()
    return ratios[len(ratios) // 2]


def set_config_model(config_path: Path, value: str) -> None:
    """Rewrite only the `whisper:` line under `models:`, keeping comments."""
    text = config_path.read_text(encoding="utf-8")
    new, count = re.subn(
        r'(?m)^(\s+whisper:\s*)"[^"]*"', lambda m: f'{m.group(1)}"{value}"', text, count=1
    )
    if count == 0:
        raise ValueError("no `whisper:` entry under `models:` in the config file")
    config_path.write_text(new, encoding="utf-8")


def download(repo: str, on_done: Callable[[Path | None, str], None]) -> threading.Thread:
    """Fetch a model in the background; on_done(path, error_message)."""

    def run() -> None:
        try:
            from huggingface_hub import snapshot_download

            path = Path(snapshot_download(repo))
            on_done(path, "")
        except Exception as error:  # noqa: BLE001
            logger.error("Model %s downloaden mislukt: %s", repo, error)
            on_done(None, str(error))

    thread = threading.Thread(target=run, daemon=True, name="airquote-model-download")
    thread.start()
    return thread


def downloaded_bytes(repo: str) -> int:
    folder = HF_HUB / f"models--{repo.replace('/', '--')}" / "blobs"
    if not folder.is_dir():
        return 0
    return sum(path.stat().st_size for path in folder.iterdir() if path.is_file())
