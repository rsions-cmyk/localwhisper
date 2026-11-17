"""Recordings: transcribe long audio, optionally with speakers told apart.

For an interview or meeting, dropped into the AirQuote window or recorded
there. Everything runs on this Mac:

1. ffmpeg decodes the file (any audio or video format) to 16 kHz mono.
2. pyannote (speaker-diarization-community-1, from the local Hugging Face
   cache) works out who speaks when.
3. Whisper transcribes each speaker turn, in chunks of at most a minute so a
   dictation can slip in between (they share MLX_LOCK).
4. The result is written next to the recording as a script:

       [00:05] Speaker B: Ja hoor. Ik werk al tien jaar ...

   which ATLAS.ti, NVivo and MAXQDA all import as plain text.
"""

from __future__ import annotations

import os
import queue
import shutil
import threading
import time
import uuid
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

SAMPLE_RATE = 16000
RECORDINGS_DIR = Path.home() / "Documents" / "AirQuote"
DIARIZATION_MODEL = "pyannote/speaker-diarization-community-1"
AUDIO_EXTENSIONS = {
    "m4a", "mp3", "wav", "aif", "aiff", "caf", "flac", "ogg", "opus", "webm",
    "mp4", "mov", "m4v", "mkv", "aac", "wma",
}

# Chunks of at most this long go to Whisper in one call (see module docstring).
_MAX_CHUNK_SECONDS = 60.0
# Turns shorter than this are merged into a neighbour: too short to transcribe
# well on their own, and usually a cough or an "mm".
_MIN_TURN_SECONDS = 0.6
# Same speaker again after a pause shorter than this: one turn, not two.
_MERGE_GAP_SECONDS = 1.0


def ensure_ffmpeg_on_path() -> None:
    """Apps started from Finder get a bare PATH without Homebrew's ffmpeg."""
    if shutil.which("ffmpeg"):
        return
    for folder in ("/opt/homebrew/bin", "/usr/local/bin"):
        if Path(folder, "ffmpeg").exists():
            os.environ["PATH"] = f"{folder}:{os.environ.get('PATH', '')}"
            return


@dataclass
class Turn:
    start: float
    end: float
    speaker: str = ""
    text: str = ""
    language: str = ""


@dataclass
class Job:
    source: Path
    speakers: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "Waiting…"
    progress: float = 0.0  # 0..1, or -1 while indeterminate
    state: str = "waiting"  # waiting | running | done | failed
    output: Path | None = None
    speaker_count: int = 0
    duration: float = 0.0
    started: float = 0.0
    finished: float = 0.0

    @property
    def name(self) -> str:
        return self.source.name


class BatchQueue:
    """Runs recording jobs one after another on a background thread."""

    def __init__(
        self,
        model: str,
        allowed_languages: list[str],
        prompt_source: Callable[[], str | None] | None = None,
        fix_casing: Callable[[str], str] | None = None,
        on_update: Callable[[Job], None] | None = None,
    ) -> None:
        self.model = model
        # [] means: let Whisper detect the language of every chunk.
        self.allowed_languages = allowed_languages
        self.prompt_source = prompt_source or (lambda: None)
        self.fix_casing = fix_casing or (lambda text: text)
        self.on_update = on_update or (lambda _job: None)
        self.jobs: list[Job] = []
        self._queue: queue.Queue[Job] = queue.Queue()
        self._pipeline = None
        self._thread = threading.Thread(target=self._run, daemon=True, name="airquote-batch")
        self._thread.start()

    def add(self, path: Path, speakers: bool = True) -> Job:
        job = Job(source=Path(path), speakers=speakers)
        self.jobs.append(job)
        self._queue.put(job)
        self._notify(job)
        return job

    def _notify(self, job: Job) -> None:
        try:
            self.on_update(job)
        except Exception as error:  # noqa: BLE001
            logger.error("Fout bij bijwerken opname-weergave: %s", error)

    def _set(self, job: Job, status: str, progress: float | None = None) -> None:
        job.status = status
        if progress is not None:
            job.progress = progress
        self._notify(job)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            job.state = "running"
            job.started = time.time()
            try:
                self._process(job)
                job.state = "done"
            except Exception as error:  # noqa: BLE001 - one bad file must not stop the queue
                logger.exception("Opname %s mislukt", job.name)
                job.state = "failed"
                job.status = f"Failed: {error}"
            job.finished = time.time()
            self._notify(job)

    # --- the work -------------------------------------------------------

    def _process(self, job: Job) -> None:
        ensure_ffmpeg_on_path()
        self._set(job, "Reading audio…", -1)
        audio = load_audio(job.source)
        job.duration = len(audio) / SAMPLE_RATE
        if job.duration < 0.5:
            raise ValueError("no audio found in this file")

        if job.speakers:
            self._set(job, "Telling speakers apart…", -1)
            turns = self._diarize(audio)
        else:
            turns = [Turn(0.0, job.duration, "")]
        turns = split_long_turns(audio, turns)
        labels = speaker_labels(turns)
        job.speaker_count = len(labels)

        total = sum(turn.end - turn.start for turn in turns) or 1.0
        done = 0.0
        language = self.allowed_languages[0] if self.allowed_languages else ""
        for index, turn in enumerate(turns, start=1):
            self._set(job, f"Transcribing… {int(done / total * 100)}%", done / total)
            clip = audio[int(turn.start * SAMPLE_RATE) : int(turn.end * SAMPLE_RATE)]
            turn.language, turn.text = self._transcribe(clip, language)
            language = turn.language or language
            done += turn.end - turn.start

        main_language = _majority([turn.language for turn in turns if turn.text]) or language
        text = format_script(
            turns,
            labels,
            title=job.source.stem,
            duration=job.duration,
            language=main_language,
            model=self.model,
            speakers=job.speakers,
        )
        job.output = write_transcript(job.source, text)
        words = sum(len(turn.text.split()) for turn in turns)
        speakers = f"{job.speaker_count} speaker{'s' if job.speaker_count != 1 else ''} · " if job.speakers else ""
        self._set(job, f"Done · {speakers}{words:,} words · {_clock(job.duration)}", 1.0)

    def _diarize(self, audio: np.ndarray) -> list[Turn]:
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as error:
            raise RuntimeError(
                "speaker separation is not installed. Run ./install.sh --speakers, "
                "or untick \u201cTell speakers apart\u201d."
            ) from error

        if self._pipeline is None:
            from huggingface_hub import snapshot_download

            try:
                local = snapshot_download(DIARIZATION_MODEL, local_files_only=True)
            except Exception:  # noqa: BLE001 - not cached: fetch it once
                local = DIARIZATION_MODEL
            try:
                pipeline = Pipeline.from_pretrained(local)
            except Exception as error:  # noqa: BLE001 - usually the one-time HF login
                raise RuntimeError(
                    "the speaker model needs a one-time Hugging Face login. Accept the "
                    "terms at https://huggingface.co/pyannote/speaker-diarization-community-1, "
                    "then run: .venv/bin/hf auth login"
                ) from error
            if pipeline is None:
                raise RuntimeError(
                    "the speaker model could not be downloaded. Accept its terms at "
                    "https://huggingface.co/pyannote/speaker-diarization-community-1 "
                    "and run: .venv/bin/hf auth login"
                )
            if torch.backends.mps.is_available():
                pipeline.to(torch.device("mps"))
            self._pipeline = pipeline

        result = self._pipeline(
            {"waveform": torch.from_numpy(audio).unsqueeze(0), "sample_rate": SAMPLE_RATE}
        )
        # "Exclusive" gives one speaker at a time, which is what a script needs.
        annotation = getattr(result, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(result, "speaker_diarization", result)
        turns = [Turn(float(seg.start), float(seg.end), str(speaker)) for seg, speaker in annotation]
        return merge_turns(turns)

    def _transcribe(self, clip: np.ndarray, previous_language: str) -> tuple[str, str]:
        import mlx_whisper

        from praattypen.engines.local_whisper import MLX_LOCK, LocalWhisperEngine

        if len(clip) < SAMPLE_RATE * 0.3:
            return previous_language, ""
        with MLX_LOCK:
            language = previous_language
            if len(self.allowed_languages) > 1 and len(clip) >= SAMPLE_RATE * 2.5:
                picker = LocalWhisperEngine(
                    language=",".join(self.allowed_languages), model=self.model
                )
                language = picker._pick_language(clip)
            kwargs = {
                "path_or_hf_repo": self.model,
                # Each turn stands alone; carrying text over invites Whisper's
                # repetition loops on long recordings.
                "condition_on_previous_text": False,
            }
            if language:
                kwargs["language"] = language
            prompt = self.prompt_source()
            if prompt:
                kwargs["initial_prompt"] = prompt
            result = mlx_whisper.transcribe(clip, **kwargs)
        text = " ".join(str(result.get("text", "")).split())
        return str(result.get("language") or language), self.fix_casing(text)


# --- pure helpers (tested without audio) ----------------------------------


def load_audio(path: Path) -> np.ndarray:
    from mlx_whisper.audio import load_audio as whisper_load

    return np.asarray(whisper_load(str(path)), dtype=np.float32)


def merge_turns(turns: list[Turn]) -> list[Turn]:
    turns = sorted((t for t in turns if t.end > t.start), key=lambda t: t.start)
    merged: list[Turn] = []
    for turn in turns:
        if merged and (
            (turn.speaker == merged[-1].speaker and turn.start - merged[-1].end < _MERGE_GAP_SECONDS)
            or turn.end - turn.start < _MIN_TURN_SECONDS
        ):
            merged[-1].end = max(merged[-1].end, turn.end)
            continue
        if merged and merged[-1].end - merged[-1].start < _MIN_TURN_SECONDS:
            # A scrap before this turn: fold it in rather than keep it alone.
            turn.start = merged.pop().start
        merged.append(turn)
    return merged


def split_long_turns(audio: np.ndarray, turns: list[Turn]) -> list[Turn]:
    """Cut turns longer than a minute at their quietest moment."""
    out: list[Turn] = []
    for turn in turns:
        start = turn.start
        while turn.end - start > _MAX_CHUNK_SECONDS:
            cut = _quiet_point(audio, start + _MAX_CHUNK_SECONDS * 0.75, start + _MAX_CHUNK_SECONDS)
            out.append(Turn(start, cut, turn.speaker))
            start = cut
        out.append(Turn(start, turn.end, turn.speaker))
    return out


def _quiet_point(audio: np.ndarray, earliest: float, latest: float) -> float:
    window = int(0.25 * SAMPLE_RATE)
    lo, hi = int(earliest * SAMPLE_RATE), min(int(latest * SAMPLE_RATE), len(audio))
    if hi - lo <= window:
        return latest
    segment = audio[lo:hi]
    frames = len(segment) // window
    energy = np.square(segment[: frames * window]).reshape(frames, window).mean(axis=1)
    return (lo + int(np.argmin(energy)) * window + window // 2) / SAMPLE_RATE


def speaker_labels(turns: list[Turn]) -> dict[str, str]:
    """pyannote's SPEAKER_01 etc. -> A, B, C in order of first appearance."""
    labels: dict[str, str] = {}
    for turn in turns:
        if turn.speaker and turn.speaker not in labels:
            index = len(labels)
            labels[turn.speaker] = chr(ord("A") + index) if index < 26 else str(index + 1)
    return labels


def format_script(
    turns: list[Turn],
    labels: dict[str, str],
    title: str,
    duration: float,
    language: str,
    model: str,
    speakers: bool = True,
) -> str:
    from praattypen import ui

    dutch = language == "nl"
    word = "Spreker" if dutch else "Speaker"
    count = len(labels)
    who = f"{count} {'spreker' if count == 1 else 'sprekers'}" if dutch else f"{count} speaker{'s' if count != 1 else ''}"
    language_label = {"nl": "Nederlands"}.get(language) if dutch else ui.language_name(language)
    credit = "Lokaal getranscribeerd door AirQuote" if dutch else "Transcribed on this Mac by AirQuote"
    separation = (", sprekerherkenning met pyannote" if dutch else ", pyannote speaker separation") if speakers else ""
    header = [
        title,
        f"{datetime.now():%Y-%m-%d} · {_clock(duration)} · "
        + (f"{who} · " if speakers else "")
        + language_label,
        f"{credit} ({ui.model_name(model)}{separation})",
        "",
    ]
    lines = []
    previous = None
    for turn in turns:
        if not turn.text:
            continue
        if speakers:
            label = f"{word} {labels.get(turn.speaker, '?')}"
            if label == previous and lines:
                # A chunk of a long turn: continue the same paragraph.
                lines[-1] += " " + turn.text
                continue
            lines.append(f"[{_clock(turn.start)}] {label}: {turn.text}")
            previous = label
        else:
            lines.append(f"[{_clock(turn.start)}] {turn.text}")
    return "\n".join(header) + "\n" + "\n\n".join(lines) + "\n"


def write_transcript(source: Path, text: str) -> Path:
    name = f"{source.stem} – transcript.txt"
    for folder in (source.parent, RECORDINGS_DIR):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = _unique(folder / name)
            target.write_text(text, encoding="utf-8")
            return target
        except OSError as error:
            logger.warning("Transcript niet geschreven in %s: %s", folder, error)
    raise OSError("could not save the transcript")


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for n in range(2, 1000):
        candidate = path.with_name(f"{path.stem} {n}{path.suffix}")
        if not candidate.exists():
            return candidate
    return path


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def _majority(values: list[str]) -> str:
    counts: dict[str, int] = {}
    for value in values:
        if value:
            counts[value] = counts.get(value, 0) + 1
    return max(counts, key=counts.get) if counts else ""


# --- recording a meeting -----------------------------------------------------


class MeetingRecorder:
    """Records the microphone straight to a WAV file, for as long as needed.

    Writes to disk as it goes instead of holding an hour of audio in memory.
    """

    def __init__(self, folder: Path = RECORDINGS_DIR) -> None:
        self.folder = folder
        self.path: Path | None = None
        self.level = 0.0
        self.started = 0.0
        self._stream = None
        self._wave = None
        self._chunks: queue.Queue = queue.Queue()
        self._writer: threading.Thread | None = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def start(self) -> Path:
        import sounddevice as sd

        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = _unique(self.folder / f"Recording {datetime.now():%Y-%m-%d %H.%M}.wav")
        handle = wave.open(str(self.path), "wb")
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        self._wave = handle

        def callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("Audiostatus (opname): %s", status)
            samples = indata[:, 0]
            self.level = float(np.sqrt(np.mean(np.square(samples))))
            self._chunks.put((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())

        def write() -> None:
            while True:
                chunk = self._chunks.get()
                if chunk is None:
                    return
                handle.writeframes(chunk)

        self._writer = threading.Thread(target=write, daemon=True, name="airquote-meeting-writer")
        self._writer.start()
        stream = sd.InputStream(channels=1, samplerate=SAMPLE_RATE, dtype="float32", callback=callback)
        stream.start()
        self._stream = stream
        self.started = time.time()
        logger.info("Opname gestart: %s", self.path)
        return self.path

    def stop(self) -> Path | None:
        if self._stream is None:
            return None
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
            self._chunks.put(None)
            if self._writer is not None:
                self._writer.join(timeout=5)
            if self._wave is not None:
                self._wave.close()
            self.level = 0.0
        logger.info("Opname gestopt: %s", self.path)
        return self.path

    def elapsed(self) -> float:
        return time.time() - self.started if self.recording else 0.0
