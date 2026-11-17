from pathlib import Path
from wave import open as open_wave

import numpy as np

from praattypen.audio import AudioRecorder, has_speech, rms, speech_level, write_wav


class FakeStream:
    """Mimics a PortAudio stream whose device vanished across sleep/lock."""

    def __init__(self, fail_on_stop: bool = False) -> None:
        self.fail_on_stop = fail_on_stop
        self.stopped = False
        self.closed = False

    def start(self) -> None:
        return None

    def stop(self) -> None:
        self.stopped = True
        if self.fail_on_stop:
            raise OSError("device disappeared")

    def close(self) -> None:
        self.closed = True


def test_write_wav_writes_16_bit_mono_file(tmp_path: Path) -> None:
    output = tmp_path / "audio.wav"
    samples = np.array([[0.0], [0.5], [-0.5]], dtype=np.float32)

    write_wav(output, samples, sample_rate=16000)

    with open_wave(str(output), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 16000
        assert wav.getsampwidth() == 2
        assert wav.getnframes() == 3


def test_has_speech_rejects_too_short_clip() -> None:
    samples = np.full(int(0.1 * 16000), 0.5, dtype=np.float32)

    assert not has_speech(samples, 16000, min_duration_seconds=0.3, min_rms=0.003)


def test_has_speech_rejects_silence() -> None:
    samples = np.zeros(16000, dtype=np.float32)

    assert not has_speech(samples, 16000, min_duration_seconds=0.3, min_rms=0.003)


def test_speech_level_ignores_the_silence_around_the_words() -> None:
    """The bug this replaced: holding the key longer failed the same speech."""
    speech = np.full(int(0.5 * 16000), 0.1, dtype=np.float32)
    silence = np.zeros(int(3.0 * 16000), dtype=np.float32)
    clip = np.concatenate([silence, speech, silence])

    # Averaged over the whole clip this speech looks far too quiet.
    assert rms(clip) < 0.03
    assert speech_level(clip, 16000) > 0.09


def test_speech_level_of_a_muted_microphone_is_exactly_zero() -> None:
    assert speech_level(np.zeros(16000, dtype=np.float32), 16000) == 0.0


def test_has_speech_accepts_quiet_speech_in_a_long_recording() -> None:
    """A 2.5s clip with a moment of quiet speech, like the one that was rejected."""
    speech = np.full(int(0.6 * 16000), 0.006, dtype=np.float32)
    clip = np.concatenate([np.zeros(int(1.9 * 16000), dtype=np.float32), speech])

    assert has_speech(clip, 16000, min_duration_seconds=0.3, min_rms=0.0015)


def test_has_speech_accepts_audible_clip() -> None:
    samples = np.full(16000, 0.1, dtype=np.float32)

    assert has_speech(samples, 16000, min_duration_seconds=0.3, min_rms=0.003)


def test_stop_to_recording_keeps_audio_when_closing_the_stream_fails() -> None:
    recorder = AudioRecorder()
    recorder._stream = FakeStream(fail_on_stop=True)
    recorder._frames = [np.full((16000, 1), 0.1, dtype=np.float32)]

    recording = recorder.stop_to_recording()

    assert recording is not None
    assert recording.sample_rate == 16000
    assert recording.samples.shape == (16000,)
    assert recording.duration_seconds == 1.0


def test_stop_keeps_the_stream_warm_and_idle_close_survives_a_failing_stop() -> None:
    recorder = AudioRecorder()
    stream = FakeStream(fail_on_stop=True)
    recorder._stream = stream

    recorder.stop_to_recording()
    assert recorder._stream is stream  # warm: no stop/start churn on a double-tap

    recorder._close_if_idle()
    assert recorder._stream is None
    recorder._cancel_idle_close()


def test_start_reuses_a_warm_running_stream(monkeypatch) -> None:
    recorder = AudioRecorder()
    warm = FakeStream()
    warm.active = True
    recorder._stream = warm
    monkeypatch.setattr(
        "sounddevice.InputStream",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("must not reopen")),
        raising=False,
    )

    recorder.start()

    assert recorder._stream is warm
    assert recorder._collecting


def test_start_replaces_a_stream_left_behind_by_a_failed_stop(monkeypatch) -> None:
    recorder = AudioRecorder()
    stale = FakeStream()
    recorder._stream = stale
    fresh = FakeStream()
    monkeypatch.setattr(
        "sounddevice.InputStream",
        lambda **_kwargs: fresh,
        raising=False,
    )

    recorder.start()

    assert stale.closed
    assert recorder._stream is fresh


def test_start_rereads_audio_devices_when_the_device_changed(monkeypatch) -> None:
    recorder = AudioRecorder()
    fresh = FakeStream()
    calls: list[str] = []

    def input_stream(**_kwargs):
        if "initialize" not in calls:
            raise OSError("Internal PortAudio error [PaErrorCode -9986]")
        return fresh

    monkeypatch.setattr("sounddevice.InputStream", input_stream, raising=False)
    monkeypatch.setattr("sounddevice._terminate", lambda: calls.append("terminate"), raising=False)
    monkeypatch.setattr("sounddevice._initialize", lambda: calls.append("initialize"), raising=False)

    recorder.start()

    assert calls == ["terminate", "initialize"]
    assert recorder._stream is fresh
