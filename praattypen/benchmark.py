"""On-demand speed test of the installed speech models.

Nothing here runs in the background: it starts when you click "Run Speed
Test" in the Models tab (or run `scripts/praattypen benchmark`), and stops
when every installed model has been measured.

Each model transcribes the same two clips, one Dutch and one English, made
with macOS's own voices so every run hears identical audio. Per model:

- load      seconds to load the model and run a first transcription
- speed     processing time per second of audio (lower is faster), median of
            three runs per clip after the load
- accuracy  word accuracy against the known text (1 - word error rate)
- memory    peak GPU memory MLX used while transcribing

The clips are synthetic voices, which are easier than a real one; the Models
tab therefore also shows the speed measured from your own dictations.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

RESULTS_PATH = Path.home() / "Library" / "Application Support" / "PraatTypen" / "benchmarks.json"

SAMPLES = [
    (
        "nl",
        "Xander",
        "Morgen bespreken we het nieuwe curriculum met alle docenten, en daarna "
        "beoordelen we de verslagen van de studenten over duurzaam toerisme in Zeeland.",
    ),
    (
        "en",
        "Samantha",
        "Tomorrow we will discuss the new curriculum with all lecturers, and afterwards "
        "we will grade the student reports about sustainable tourism in Zeeland.",
    ),
]
_RUNS = 3


@dataclass
class Result:
    repo: str
    load_seconds: float
    speed: float  # processing seconds per second of audio
    accuracy: float  # 0..1
    peak_memory_gb: float
    tested_at: float


def word_accuracy(reference: str, hypothesis: str) -> float:
    """1 - word error rate, on lowercased words without punctuation."""
    ref = _words(reference)
    hyp = _words(hypothesis)
    if not ref:
        return 0.0
    # Levenshtein distance over words.
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i] + [0] * len(hyp)
        for j, hyp_word in enumerate(hyp, start=1):
            current[j] = min(
                previous[j] + 1,
                current[j - 1] + 1,
                previous[j - 1] + (ref_word != hyp_word),
            )
        previous = current
    return max(0.0, 1.0 - previous[-1] / len(ref))


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.lower())


def load_results() -> dict[str, Result]:
    try:
        raw = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        return {item["repo"]: Result(**item) for item in raw}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _save(results: dict[str, Result]) -> None:
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps([asdict(r) for r in results.values()], indent=2), encoding="utf-8"
    )


def _make_clips(folder: Path) -> list[tuple[str, str, object]]:
    from praattypen.batch import ensure_ffmpeg_on_path, load_audio

    ensure_ffmpeg_on_path()
    clips = []
    for language, voice, text in SAMPLES:
        path = folder / f"{language}.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(path), text], check=True, timeout=60)
        clips.append((language, text, load_audio(path)))
    return clips


def run(
    models: list[tuple[str, str]],
    restore: str,
    on_progress: Callable[[str], None] | None = None,
) -> dict[str, Result]:
    """Benchmark (repo, path_or_repo) pairs; reload `restore` afterwards.

    Takes the shared MLX lock per transcription, so dictation keeps working
    between steps.
    """
    import mlx.core as mx
    import mlx_whisper
    from mlx_whisper.transcribe import ModelHolder

    from praattypen.engines.local_whisper import MLX_LOCK

    progress = on_progress or (lambda _message: None)
    results = load_results()
    with tempfile.TemporaryDirectory(prefix="airquote-benchmark-") as tmp:
        progress("Preparing test audio…")
        clips = _make_clips(Path(tmp))
        for index, (repo, source) in enumerate(models, start=1):
            label = f"{index}/{len(models)}"
            progress(f"Testing model {label}: loading…")
            with MLX_LOCK:
                ModelHolder.model = None  # force a real load, not the cached one
                mx.reset_peak_memory()
                started = time.perf_counter()
                mlx_whisper.transcribe(clips[0][2][:16000], path_or_hf_repo=source, language="nl")
                load_seconds = time.perf_counter() - started

            speeds, accuracies = [], []
            for language, text, audio in clips:
                seconds = len(audio) / 16000
                for run_index in range(_RUNS):
                    progress(f"Testing model {label}: {language.upper()} run {run_index + 1}/{_RUNS}")
                    with MLX_LOCK:
                        started = time.perf_counter()
                        out = mlx_whisper.transcribe(audio, path_or_hf_repo=source, language=language)
                        speeds.append((time.perf_counter() - started) / seconds)
                    accuracies.append(word_accuracy(text, str(out.get("text", ""))))
            results[repo] = Result(
                repo=repo,
                load_seconds=round(load_seconds, 2),
                speed=round(statistics.median(speeds), 4),
                accuracy=round(statistics.mean(accuracies), 3),
                peak_memory_gb=round(mx.get_peak_memory() / 1024**3, 2),
                tested_at=time.time(),
            )
            _save(results)
            logger.info("Benchmark %s: %s", repo, results[repo])

    progress("Reloading your current model…")
    with MLX_LOCK:
        mlx_whisper.transcribe(clips[0][2][:8000], path_or_hf_repo=restore, language="nl")
    progress("Done")
    return results


def summary(result: Result) -> str:
    return (
        f"test: {result.speed:.3f} s per s of audio · {result.accuracy * 100:.0f}% words right · "
        f"loads in {result.load_seconds:.1f} s · {result.peak_memory_gb:.1f} GB peak"
    )
