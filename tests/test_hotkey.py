from praattypen.hotkey import (
    PushToTalkController,
    _FnKeyState,
    _fn_pressed_from_flags,
    _is_fn_hotkey,
    _parse_key,
    watchdog_tick,
)


class BrokenRecorder:
    """Recorder whose stream died with the audio device (sleep/lock)."""

    def __init__(self, fail_on: str) -> None:
        self.fail_on = fail_on

    def start(self) -> None:
        if self.fail_on == "start":
            raise OSError("device unavailable")

    def stop_to_recording(self) -> str:
        if self.fail_on == "stop":
            raise OSError("device unavailable")
        return "audio.wav"


class FakeRecorder:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1

    def stop_to_recording(self) -> str:
        self.stopped += 1
        return "audio.wav"


def test_push_to_talk_ignores_repeated_press_until_release() -> None:
    recorder = FakeRecorder()
    processed: list[str] = []
    controller = PushToTalkController(recorder, processed.append)

    controller.press()
    controller.press()
    controller.release()

    assert recorder.started == 1
    assert recorder.stopped == 1
    assert processed == ["audio.wav"]


def test_push_to_talk_ignores_release_when_not_recording() -> None:
    recorder = FakeRecorder()
    processed: list[str] = []
    controller = PushToTalkController(recorder, processed.append)

    controller.release()

    assert recorder.started == 0
    assert recorder.stopped == 0
    assert processed == []


def test_failing_stop_reports_instead_of_hanging_on_listening() -> None:
    failures: list[BaseException] = []
    stopped: list[str] = []
    controller = PushToTalkController(
        BrokenRecorder(fail_on="stop"),
        lambda _path: None,
        on_stopped=stopped.append,
        on_failed=failures.append,
    )

    controller.press()
    controller.release()

    assert len(failures) == 1
    assert stopped == []
    assert not controller.recording


def test_recording_restarts_after_a_failing_stop() -> None:
    recorder = BrokenRecorder(fail_on="stop")
    started: list[int] = []
    controller = PushToTalkController(
        recorder,
        lambda _path: None,
        debounce_seconds=0.0,
        on_started=lambda: started.append(1),
        on_failed=lambda _error: None,
    )

    controller.press()
    controller.release()
    recorder.fail_on = "never"
    controller.press()

    assert len(started) == 2


def test_failing_start_does_not_latch_the_recording_flag() -> None:
    failures: list[BaseException] = []
    controller = PushToTalkController(
        BrokenRecorder(fail_on="start"),
        lambda _path: None,
        on_failed=failures.append,
    )

    controller.press()

    assert len(failures) == 1
    assert not controller.recording


def test_watchdog_ends_a_recording_whose_release_was_missed() -> None:
    recorder = FakeRecorder()
    processed: list[str] = []
    controller = PushToTalkController(recorder, processed.append)
    state = _FnKeyState()
    state.update(True, controller)

    misses = watchdog_tick(controller, state, lambda: False, misses=0, strikes=2)
    assert misses == 1
    assert controller.recording

    misses = watchdog_tick(controller, state, lambda: False, misses=misses, strikes=2)

    assert misses == 0
    assert not controller.recording
    assert processed == ["audio.wav"]


def test_watchdog_leaves_a_long_dictation_alone_while_fn_is_held() -> None:
    controller = PushToTalkController(FakeRecorder(), lambda _path: None)
    state = _FnKeyState()
    state.update(True, controller)

    misses = 0
    for _ in range(50):
        misses = watchdog_tick(controller, state, lambda: True, misses=misses, strikes=2)

    assert controller.recording
    assert misses == 0


def test_watchdog_stops_a_recording_past_the_time_limit() -> None:
    now = [10.0]
    processed: list[str] = []
    controller = PushToTalkController(
        FakeRecorder(),
        processed.append,
        debounce_seconds=0.0,
        clock=lambda: now[0],
        max_recording_seconds=120.0,
    )
    state = _FnKeyState()
    state.update(True, controller)
    assert controller.recording

    now[0] = 100.0
    assert watchdog_tick(controller, state, lambda: True, misses=0, strikes=2) == 0
    assert controller.recording, "een dictaat binnen de limiet mag niet worden afgekapt"

    now[0] = 200.0
    watchdog_tick(controller, state, lambda: True, misses=0, strikes=2)

    assert not controller.recording
    assert processed == ["audio.wav"], "de opname moet alsnog getranscribeerd worden"


def test_watchdog_is_inert_when_nothing_is_recording() -> None:
    recorder = FakeRecorder()
    controller = PushToTalkController(recorder, lambda _path: None)
    state = _FnKeyState()

    assert watchdog_tick(controller, state, lambda: False, misses=1, strikes=2) == 0
    assert recorder.stopped == 0


class FakeKey:
    f20 = object()
    cmd_r = object()
    caps_lock = object()


class FakeKeyCode:
    @staticmethod
    def from_char(char: str) -> tuple[str, str]:
        return ("char", char)


class FakeKeyboard:
    Key = FakeKey
    KeyCode = FakeKeyCode


def test_parse_key_supports_pynput_named_keys_and_aliases() -> None:
    assert _parse_key("f20", FakeKeyboard) is FakeKey.f20
    assert _parse_key("right_command", FakeKeyboard) is FakeKey.cmd_r
    assert _parse_key("caps_lock", FakeKeyboard) is FakeKey.caps_lock
    assert _parse_key("x", FakeKeyboard) == ("char", "x")


def test_parse_key_rejects_globe_when_pynput_has_no_fn_key() -> None:
    try:
        _parse_key("globe", FakeKeyboard)
    except ValueError as error:
        assert "Unsupported hotkey" in str(error)
    else:
        raise AssertionError("Expected ValueError")


def test_fn_hotkey_names_use_quartz_listener_path() -> None:
    assert _is_fn_hotkey("fn")
    assert _is_fn_hotkey("globe")
    assert _is_fn_hotkey(" function ")
    assert not _is_fn_hotkey("f5")


def test_fn_pressed_from_quartz_flags() -> None:
    fn_mask = 0b1000

    assert _fn_pressed_from_flags(0b1000, fn_mask)
    assert _fn_pressed_from_flags(0b1010, fn_mask)
    assert not _fn_pressed_from_flags(0b0010, fn_mask)
