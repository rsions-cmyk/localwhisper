from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from praattypen.audio import Recording
from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

# How often the watchdog checks whether our idea of "Fn is held" still matches
# reality. Two consecutive misses are needed before it acts, so a genuine long
# dictation is never cut off by one bad reading.
WATCHDOG_INTERVAL_SECONDS = 2.0
WATCHDOG_STRIKES = 2
DEFAULT_MAX_RECORDING_SECONDS = 900.0


class Recorder(Protocol):
    def start(self) -> None:
        ...

    def stop_to_recording(self) -> Recording | None:
        ...


class PushToTalkController:
    def __init__(
        self,
        recorder: Recorder,
        process_audio: Callable[[Recording], object],
        debounce_seconds: float = 0.3,
        clock: Callable[[], float] = time.monotonic,
        on_started: Callable[[], None] | None = None,
        on_stopped: Callable[[Recording], None] | None = None,
        on_skipped: Callable[[], None] | None = None,
        on_failed: Callable[[BaseException], None] | None = None,
        max_recording_seconds: float = DEFAULT_MAX_RECORDING_SECONDS,
    ) -> None:
        self.recorder = recorder
        self.process_audio = process_audio
        self.debounce_seconds = debounce_seconds
        self.clock = clock
        self.on_started = on_started or _noop
        self.on_stopped = on_stopped or (lambda _recording: None)
        self.on_skipped = on_skipped or _noop
        self.on_failed = on_failed or _print_failure
        self.max_recording_seconds = max_recording_seconds
        self.recording = False
        self._last_transition = 0.0
        self._recording_started = 0.0
        # press/release also arrive from the watchdog thread, so the state
        # transition and the recorder call have to move as one unit.
        self._lock = threading.RLock()

    def press(self) -> None:
        with self._lock:
            now = self.clock()
            if self.recording or now - self._last_transition < self.debounce_seconds:
                return
            self.recording = True
            self._last_transition = now
            self._recording_started = now
            try:
                self.recorder.start()
            except Exception as error:  # noqa: BLE001 - a failed start must not latch "recording"
                self.recording = False
                self.on_failed(error)
                return
            self.on_started()

    def release(self) -> None:
        with self._lock:
            if not self.recording:
                return
            self.recording = False
            self._last_transition = self.clock()
            try:
                recording = self.recorder.stop_to_recording()
            except Exception as error:  # noqa: BLE001 - otherwise the overlay hangs on "Luistert"
                self.on_failed(error)
                return
            if recording is None:
                self.on_skipped()
                return
            try:
                self.on_stopped(recording)
                self.process_audio(recording)
            except Exception as error:  # noqa: BLE001
                self.on_failed(error)

    def recording_seconds(self) -> float:
        with self._lock:
            if not self.recording:
                return 0.0
            return self.clock() - self._recording_started

    def recording_overdue(self) -> bool:
        if self.max_recording_seconds <= 0:
            return False
        return self.recording_seconds() >= self.max_recording_seconds


def listen_push_to_talk(hotkey: str, controller: PushToTalkController) -> None:
    if _is_fn_hotkey(hotkey):
        listen_fn_push_to_talk(controller)
        return

    try:
        from pynput import keyboard
    except ImportError as error:
        raise RuntimeError(
            "pynput is not installed. Install it with `.venv/bin/python -m pip install pynput`."
        ) from error

    target = _parse_key(hotkey, keyboard)

    def on_press(key) -> None:
        if key == target:
            controller.press()

    def on_release(key) -> None:
        if key == target:
            controller.release()

    with keyboard.Listener(on_press=on_press, on_release=on_release) as listener:
        try:
            import AppKit
        except ImportError:
            listener.join()
            return

        # pynput listens on its own thread, so the main thread is free to run
        # the AppKit loop -- which the overlay and the menu bar need in order to
        # draw at all. Without this they stay invisible on non-Fn hotkeys.
        AppKit.NSApplication.sharedApplication().run()


def listen_fn_push_to_talk(controller: PushToTalkController) -> None:
    try:
        import Quartz
    except ImportError as error:
        raise RuntimeError(
            "Quartz/PyObjC is not installed. Install pynput or pyobjc-framework-Quartz."
        ) from error

    fn_mask = Quartz.kCGEventFlagMaskSecondaryFn
    event_mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
    disabled_types = {
        Quartz.kCGEventTapDisabledByTimeout,
        Quartz.kCGEventTapDisabledByUserInput,
    }
    state = _FnKeyState()
    tap_holder: dict[str, object] = {}

    def callback(_proxy, event_type, event, _refcon):
        # Nothing may escape this callback: an exception here is swallowed by
        # the run loop and would leave press/release permanently out of sync.
        try:
            if event_type in disabled_types:
                # macOS switches the tap off after a slow callback or after
                # secure input (the lock screen). Nothing re-enables it on our
                # behalf, so without this the hotkey silently dies until
                # PraatTypen is restarted -- and any held Fn never releases.
                reason = (
                    "te trage callback"
                    if event_type == Quartz.kCGEventTapDisabledByTimeout
                    else "invoer van de gebruiker (secure input)"
                )
                logger.warning("macOS schakelde de Fn-tap uit: %s. Opnieuw inschakelen.", reason)
                _enable_tap(Quartz, tap_holder.get("tap"))
                state.force_release(controller)
                return event
            is_pressed = _fn_pressed_from_flags(Quartz.CGEventGetFlags(event), fn_mask)
            state.update(is_pressed, controller)
        except Exception as error:  # noqa: BLE001 - the listener must survive
            logger.error("Fout in Fn-listener: %s", error)
        return event

    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap,
        Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly,
        event_mask,
        callback,
        None,
    )
    if tap is None:
        raise RuntimeError(
            "Fn/Globe hotkey kon niet worden gestart. Geef Terminal toestemming bij "
            "Privacy en beveiliging -> Toegankelijkheid en Invoermonitoring."
        )

    tap_holder["tap"] = tap

    run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(
        Quartz.CFRunLoopGetCurrent(),
        run_loop_source,
        Quartz.kCFRunLoopCommonModes,
    )
    Quartz.CGEventTapEnable(tap, True)

    # Without accessibility rights macOS hands back a tap that simply never
    # switches on, instead of refusing to create one. Saying "PraatTypen
    # luistert" at that point is a lie, and it is the exact situation where the
    # user is staring at a held-down Fn key wondering what is broken. The
    # watchdog keeps retrying, so this recovers by itself once rights are given.
    if not Quartz.CGEventTapIsEnabled(tap):
        logger.error(
            "Fn-listener kon niet worden ingeschakeld: PraatTypen mist Invoermonitoring. "
            "Systeeminstellingen -> Privacy en beveiliging -> Invoermonitoring -> PraatTypen aanzetten "
            "(een andere lijst dan Toegankelijkheid). Zodra dat gebeurt pikt PraatTypen het vanzelf op."
        )

    def fn_really_pressed() -> bool:
        flags = Quartz.CGEventSourceFlagsState(Quartz.kCGEventSourceStateCombinedSessionState)
        return _fn_pressed_from_flags(flags, fn_mask)

    def revive_tap() -> None:
        if Quartz.CGEventTapIsEnabled(tap):
            return
        # A tap that cannot be enabled at all stays off, and retrying every two
        # seconds would otherwise bury the log under identical warnings.
        _warn_occasionally(
            "Fn-tap staat uit en laat zich niet inschakelen. Meestal betekent dit dat "
            "Invoermonitoring pas is toegekend nadat PraatTypen al draaide: macOS geeft "
            "een draaiend proces de oude status. Start PraatTypen opnieuw."
        )
        _enable_tap(Quartz, tap)
        if Quartz.CGEventTapIsEnabled(tap):
            # Worth a line: this is the moment the hotkey starts working, and
            # it happens without any visible change on screen.
            logger.info("Fn-listener is nu actief.")
            _reset_warning_clock()

    start_watchdog(controller, state, fn_really_pressed, revive_tap)

    try:
        import AppKit
    except ImportError:
        Quartz.CFRunLoopRun()
        return

    # Running the AppKit event loop (instead of a bare CFRunLoopRun) keeps the
    # Quartz tap working (its source was added in kCFRunLoopCommonModes,
    # so it still fires) while also letting the floating overlay window
    # actually draw and update.
    AppKit.NSApplication.sharedApplication().run()


# Once a minute is often enough to notice, rare enough to read around.
WARNING_INTERVAL_SECONDS = 60.0
_last_warning = {"at": 0.0}


def _warn_occasionally(message: str) -> None:
    now = time.monotonic()
    if _last_warning["at"] and now - _last_warning["at"] < WARNING_INTERVAL_SECONDS:
        return
    _last_warning["at"] = now
    logger.warning(message)


def _reset_warning_clock() -> None:
    _last_warning["at"] = 0.0


def _timed(label: str, action: Callable[[], None]) -> None:
    started = time.monotonic()
    try:
        action()
    finally:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.info("%s (%.0f ms in de tap-callback).", label, elapsed_ms)


class _FnKeyState:
    """Whether Fn is held, as far as the event tap has told us.

    Kept separate from the controller so the watchdog can correct it when the
    tap misses a release instead of only being able to stop the recorder.
    """

    def __init__(self) -> None:
        self.pressed = False
        self._lock = threading.Lock()

    def update(self, is_pressed: bool, controller: PushToTalkController) -> None:
        with self._lock:
            was_pressed = self.pressed
            self.pressed = is_pressed
        # Timed because this runs inside the event tap callback: macOS switches
        # a tap off when its callback is too slow, and opening or closing the
        # audio device here is exactly the kind of work that gets slow.
        if is_pressed and not was_pressed:
            _timed("Fn ingedrukt", controller.press)
        elif was_pressed and not is_pressed:
            _timed("Fn losgelaten", controller.release)

    def force_release(self, controller: PushToTalkController) -> None:
        with self._lock:
            self.pressed = False
        controller.release()


def start_watchdog(
    controller: PushToTalkController,
    state: _FnKeyState,
    fn_really_pressed: Callable[[], bool],
    revive_tap: Callable[[], None] | None = None,
    interval: float = WATCHDOG_INTERVAL_SECONDS,
    strikes: int = WATCHDOG_STRIKES,
    sleep: Callable[[float], None] = time.sleep,
) -> threading.Thread:
    """Recover from a recording that never got its release event.

    The tap can stop delivering events (lock screen, secure input) while Fn is
    down, which used to leave the overlay stuck on "Luistert" forever. Querying
    the real modifier state does not go through the tap, so it still tells the
    truth when the tap is dead.
    """

    revive = revive_tap or _noop

    def run() -> None:
        misses = 0
        while True:
            sleep(interval)
            try:
                revive()
                misses = watchdog_tick(controller, state, fn_really_pressed, misses, strikes)
            except Exception as error:  # noqa: BLE001 - the watchdog must survive
                logger.error("Fout in watchdog: %s", error)

    thread = threading.Thread(target=run, daemon=True, name="praattypen-watchdog")
    thread.start()
    return thread


def watchdog_tick(
    controller: PushToTalkController,
    state: _FnKeyState,
    fn_really_pressed: Callable[[], bool],
    misses: int,
    strikes: int = WATCHDOG_STRIKES,
) -> int:
    """One watchdog check; returns the updated consecutive-miss count."""
    if not controller.recording:
        return 0
    if controller.recording_overdue():
        logger.warning(
            "Opname langer dan %.0fs: automatisch gestopt.", controller.max_recording_seconds
        )
        state.force_release(controller)
        return 0
    # One stray reading is not enough: only a sustained mismatch means the
    # release was really missed, so a long dictation is never cut short.
    if fn_really_pressed():
        return 0
    misses += 1
    if misses < strikes:
        return misses
    logger.warning("Fn-loslaten gemist, opname alsnog afgerond.")
    state.force_release(controller)
    return 0


def _enable_tap(quartz_module, tap) -> None:
    if tap is not None:
        quartz_module.CGEventTapEnable(tap, True)


def _print_failure(error: BaseException) -> None:
    logger.error("Fout bij opnemen: %s", error)


def _is_fn_hotkey(name: str) -> bool:
    return name.strip().lower() in {"fn", "globe", "function"}


def _fn_pressed_from_flags(flags: int, fn_mask: int) -> bool:
    return bool(flags & fn_mask)


def _noop() -> None:
    return None


def _parse_key(name: str, keyboard_module):
    normalized = name.strip().lower()
    aliases = {
        "right_option": "alt_r",
        "right_alt": "alt_r",
        "left_option": "alt_l",
        "left_alt": "alt_l",
        "right_command": "cmd_r",
        "right_cmd": "cmd_r",
        "left_command": "cmd_l",
        "left_cmd": "cmd_l",
        "right_control": "ctrl_r",
        "right_ctrl": "ctrl_r",
        "left_control": "ctrl_l",
        "left_ctrl": "ctrl_l",
        "capslock": "caps_lock",
    }
    key_name = aliases.get(normalized, normalized)
    named = getattr(keyboard_module.Key, key_name, None)
    if named is not None:
        return named
    if len(normalized) == 1:
        return keyboard_module.KeyCode.from_char(normalized)
    raise ValueError(f"Unsupported hotkey: {name}")
