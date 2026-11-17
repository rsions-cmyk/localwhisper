from __future__ import annotations

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class Recording:
    """Captured audio, still in memory.

    Mono float32 at ``sample_rate`` is exactly what the local Whisper engine
    wants, so the common path can hand these samples straight to the model
    instead of writing a WAV and having ffmpeg decode it back.
    """

    samples: np.ndarray
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        return len(self.samples) / self.sample_rate

    def write_wav(self, path: Path) -> None:
        write_wav(path, self.samples, self.sample_rate)


class AudioRecorder:
    def __init__(
        self,
        sample_rate: int = 16000,
        min_duration_seconds: float = 0.3,
        min_rms: float = 0.003,
    ) -> None:
        self.sample_rate = sample_rate
        self.min_duration_seconds = min_duration_seconds
        self.min_rms = min_rms
        self._stream = None
        self._frames: list[np.ndarray] = []

    def start(self) -> None:
        try:
            import sounddevice as sd
        except ImportError as error:
            raise RuntimeError(
                "sounddevice is not installed. Install it with `.venv/bin/python -m pip install sounddevice`."
            ) from error

        # A leftover stream means a previous stop failed, which happens when
        # CoreAudio resets the input device across sleep/lock. That handle is
        # dead: keeping it would make every later recording a silent no-op.
        self._discard_stream()

        self._frames = []

        def callback(indata, _frames, _time, status) -> None:
            if status:
                logger.warning("Audiostatus: %s", status)
            self._frames.append(indata.copy())

        def open_stream():
            stream = sd.InputStream(
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                callback=callback,
            )
            stream.start()
            return stream

        try:
            self._stream = open_stream()
        except Exception as error:  # noqa: BLE001 - retried once below
            # PortAudio snapshots the device list once, at import. When the
            # input device changes afterwards (AirPods connecting or dropping)
            # it keeps pointing at the old one and every open fails with
            # PaErrorCode -9986 until the app restarts. Re-reading the device
            # list and trying once more fixes that without a restart.
            logger.warning("Microfoon openen mislukte (%s); audioapparaten opnieuw inlezen.", error)
            reinitialize_portaudio(sd)
            self._stream = open_stream()

    def stop_to_recording(self) -> Recording | None:
        if self._stream is None:
            return None

        self._discard_stream()

        frames, self._frames = self._frames, []
        if not frames:
            return None

        audio = np.concatenate(frames, axis=0).reshape(-1)

        if not has_speech(audio, self.sample_rate, self.min_duration_seconds, self.min_rms):
            # Which of the two thresholds it failed matters: a long clip at
            # exactly 0.0 RMS is a muted or unauthorised microphone, not a user
            # who spoke too softly.
            seconds = len(audio) / self.sample_rate
            level = speech_level(audio, self.sample_rate)
            logger.info(
                "Opname genegeerd: %.2fs (drempel %.2fs), luidste %.1fs %.6f (drempel %.6f)%s",
                seconds,
                self.min_duration_seconds,
                SPEECH_WINDOW_SECONDS,
                level,
                self.min_rms,
                " -- volledig stil, controleer de microfoontoestemming"
                if level == 0.0 and seconds >= self.min_duration_seconds
                else "",
            )
            return None

        return Recording(samples=audio, sample_rate=self.sample_rate)

    def _discard_stream(self) -> None:
        """Drop the current stream, surviving a failing stop/close.

        Audio already captured stays in ``_frames``: a device reset while
        stopping should cost the user nothing, so we keep what was recorded
        instead of letting the error escape.
        """
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        for step in (stream.stop, stream.close):
            try:
                step()
            except Exception as error:  # noqa: BLE001 - a dead stream must not wedge the recorder
                logger.error("Audiostream sluiten mislukte: %s", error)


def reinitialize_portaudio(sd) -> None:
    """Make PortAudio pick up the current audio devices.

    Only safe while no stream is open; ``start`` discards its stream first.
    """
    sd._terminate()
    sd._initialize()


# Roughly one syllable: long enough not to trigger on a click, short enough
# that a single word still fills it.
SPEECH_WINDOW_SECONDS = 0.3


def rms(samples: np.ndarray) -> float:
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    if mono.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(mono))))


def speech_level(samples: np.ndarray, sample_rate: int) -> float:
    """Loudness of the loudest short window, not of the whole clip.

    Most of a push-to-talk recording is the silence around the words, so a
    whole-clip average drops the longer you hold the key: the same voice that
    passes in a one-second clip fails in a three-second one. Scoring the
    loudest window instead makes the gate independent of how long you held it.
    """
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    window = int(SPEECH_WINDOW_SECONDS * sample_rate)
    if mono.size == 0:
        return 0.0
    if window <= 0 or mono.size <= window:
        return rms(mono)

    # Sliding-window mean of squares via a cumulative sum: one pass, no copies
    # of the signal per window.
    squares = np.square(mono, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(squares)))
    sums = cumulative[window:] - cumulative[:-window]
    return float(np.sqrt(sums.max() / window))


def has_speech(
    samples: np.ndarray,
    sample_rate: int,
    min_duration_seconds: float,
    min_rms: float,
) -> bool:
    """Gate against too-short or silent clips: Whisper hallucinates on near-silence."""
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    if mono.size < int(min_duration_seconds * sample_rate):
        return False
    return speech_level(mono, sample_rate) >= min_rms


def write_wav(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    mono = np.asarray(samples, dtype=np.float32).reshape(-1)
    clipped = np.clip(mono, -1.0, 1.0)
    pcm = (clipped * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
