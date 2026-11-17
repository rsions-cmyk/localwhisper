from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from praattypen.config import AppConfig
from praattypen.history import HistoryEntry, HistoryStore, format_duration
from praattypen.logging_setup import get_logger
from praattypen.permissions import accessibility_trusted, hid_access, microphone_status

logger = get_logger(__name__)


class StatusWindow(Protocol):
    """Window that answers "draait hij, op welk model, en wat deed hij net?"."""

    def show(self) -> None:
        ...

    def set_idle(self) -> None:
        ...

    def set_listening(self) -> None:
        ...

    def set_processing(self) -> None:
        ...

    def refresh(self) -> None:
        ...


class NullStatusWindow:
    """No-op window, used when AppKit isn't available or it is disabled."""

    def show(self) -> None:
        return None

    def set_idle(self) -> None:
        return None

    def set_listening(self) -> None:
        return None

    def set_processing(self) -> None:
        return None

    def refresh(self) -> None:
        return None


# Same colours as the overlay and the menu bar, so one glance teaches all three.
_IDLE = ("Actief - klaar om te luisteren", (0.30, 0.78, 0.35))
_LISTENING = ("Luistert...", (0.95, 0.25, 0.25))
_PROCESSING = ("Transcriberen...", (0.95, 0.65, 0.15))

_RECENT_LIMIT = 10

# Long enough for our window to be gone and the previous app to be front again
# before Cmd+V fires -- otherwise the paste lands in PraatTypen itself.
_HIDE_BEFORE_PASTE_SECONDS = 0.35

# IOHIDRequestType: the listen-only tap the Fn hotkey needs.
_LISTEN_EVENT = 1

_WINDOW_WIDTH = 620
_WINDOW_HEIGHT = 560
_MARGIN = 20

_TARGET_CLASS = None


def _target_class():
    """Define the Objective-C button target once.

    PyObjC registers classes globally by name, so defining it a second time is
    an error -- which a second status window in one process would trigger.
    """
    global _TARGET_CLASS
    if _TARGET_CLASS is not None:
        return _TARGET_CLASS

    import AppKit
    import objc

    class _PraatTypenWindowTarget(AppKit.NSObject):
        def init(self):
            self = objc.super(_PraatTypenWindowTarget, self).init()
            if self is None:
                return None
            self.handlers: list[Callable[[], None]] = []
            return self

        def addHandler_(self, handler) -> int:
            self.handlers.append(handler)
            return len(self.handlers) - 1

        def invoke_(self, sender) -> None:
            index = int(sender.tag())
            try:
                self.handlers[index]()
            except Exception as error:  # noqa: BLE001 - a bad button must not kill the app
                logger.error("Fout in vensteractie: %s", error)

    _TARGET_CLASS = _PraatTypenWindowTarget
    return _TARGET_CLASS


_DELEGATE_CLASS = None


def _delegate_class():
    """Define the app delegate that reopens the window from the Dock icon.

    Registered globally by name, like the button target, so it is defined once.
    """
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS

    import AppKit
    import objc

    class _PraatTypenAppDelegate(AppKit.NSObject):
        def initWithShow_(self, show):
            self = objc.super(_PraatTypenAppDelegate, self).init()
            if self is None:
                return None
            self.show = show
            return self

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_windows) -> bool:
            # Clicking the Dock icon of a running background app does nothing
            # by default, which reads as "hij doet niets meer".
            try:
                self.show()
            except Exception as error:  # noqa: BLE001
                logger.error("Kon statusvenster niet openen: %s", error)
            return True

    _DELEGATE_CLASS = _PraatTypenAppDelegate
    return _DELEGATE_CLASS


class RecordingStatusWindow:
    """The PraatTypen status window: state, engine, model, permissies, geschiedenis.

    Must be constructed on the main thread (AppKit requirement). The public
    methods are safe to call from any thread: like the overlay and the menu
    bar, they marshal onto the main run loop instead of touching AppKit
    directly.
    """

    def __init__(
        self,
        config: AppConfig,
        engine_name: str,
        model: str,
        history: HistoryStore,
        config_path: Path,
        log_path: Path | None = None,
        repaste: Callable[[str], object] | None = None,
        started_at: float | None = None,
    ) -> None:
        import AppKit
        import Quartz  # noqa: F401 - registers CGColorRef bridging used below
        from Foundation import NSMakeRect

        self._AppKit = AppKit
        self._config = config
        self._engine_name = engine_name
        self._model = model
        self._history = history
        self._config_path = config_path
        self._log_path = log_path
        self._repaste = repaste
        self._started_at = started_at or time.time()
        self._state = _IDLE

        # Activation policy is set once in appearance.apply_activation_policy;
        # forcing it here would silently undo the Dock icon.
        AppKit.NSApplication.sharedApplication()

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WINDOW_WIDTH, _WINDOW_HEIGHT),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("PraatTypen")
        # Closing must not deallocate the window: the menu bar reopens this
        # same instance, and a released one crashes the app on the second show.
        window.setReleasedWhenClosed_(False)
        window.center()
        self._window = window

        self._target = _target_class().alloc().init()
        self._row_target = _target_class().alloc().init()
        self._build(window.contentView())
        self._apply(self._state)
        self._reload_rows()
        self._install_dock_reopen()

    def _install_dock_reopen(self) -> None:
        app = self._AppKit.NSApplication.sharedApplication()
        if app.delegate() is not None:
            # Something else already owns the delegate slot; taking it would
            # break whatever that is, and the menu bar can still open us.
            return
        self._delegate = _delegate_class().alloc().initWithShow_(self.show)
        app.setDelegate_(self._delegate)

    # --- building -------------------------------------------------------

    def _build(self, content) -> None:
        AppKit = self._AppKit

        self._dot = self._circle(12)
        self._state_label = self._label("", size=16, bold=True)
        header = self._hstack([self._dot, self._state_label], spacing=10)

        self._uptime_label = self._label("", size=11, secondary=True)

        self._info_rows = {}
        info = self._grid(
            [
                ("Engine", "engine"),
                ("Model", "model"),
                ("Taal", "language"),
                ("Sneltoets", "hotkey"),
                ("Opschoning", "cleanup"),
                ("Permissies", "permissions"),
            ]
        )

        self._stats_label = self._label("", size=12, secondary=True)

        recent_title = self._label(f"Laatste {_RECENT_LIMIT} opnames", size=13, bold=True)
        self._rows = self._vstack([], spacing=2)
        self._empty_label = self._label(
            "Nog geen opnames. Houd de sneltoets ingedrukt en praat.",
            size=12,
            secondary=True,
        )

        buttons = self._hstack(
            [
                self._button("Vernieuwen", self.refresh, self._target),
                self._button("Config openen", lambda: self._open(self._config_path), self._target),
            ]
            + (
                [self._button("Log openen", lambda: self._open(self._log_path), self._target)]
                if self._log_path is not None
                else []
            )
            + [self._button("Geschiedenis wissen", self._clear_history, self._target)],
            spacing=8,
        )

        root = self._vstack(
            [
                header,
                self._uptime_label,
                self._separator(),
                info,
                self._separator(),
                self._stats_label,
                recent_title,
                self._empty_label,
                self._rows,
                self._separator(),
                buttons,
            ],
            spacing=12,
        )
        self._root = root
        root.setTranslatesAutoresizingMaskIntoConstraints_(False)
        content.addSubview_(root)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                root.leadingAnchor().constraintEqualToAnchor_constant_(
                    content.leadingAnchor(), _MARGIN
                ),
                root.trailingAnchor().constraintEqualToAnchor_constant_(
                    content.trailingAnchor(), -_MARGIN
                ),
                root.topAnchor().constraintEqualToAnchor_constant_(content.topAnchor(), _MARGIN),
                root.bottomAnchor().constraintLessThanOrEqualToAnchor_constant_(
                    content.bottomAnchor(), -_MARGIN
                ),
                self._rows.widthAnchor().constraintEqualToAnchor_(root.widthAnchor()),
            ]
        )

    def _grid(self, fields: list[tuple[str, str]]):
        AppKit = self._AppKit
        rows = []
        for title, key in fields:
            value = self._label("", size=12)
            self._info_rows[key] = value
            rows.append([self._label(f"{title}:", size=12, secondary=True), value])
        grid = AppKit.NSGridView.gridViewWithViews_(rows)
        grid.setRowSpacing_(4)
        grid.setColumnSpacing_(12)
        grid.columnAtIndex_(0).setWidth_(96)
        return grid

    # --- public API -----------------------------------------------------

    def show(self) -> None:
        self._call_on_main(self._show)

    def set_idle(self) -> None:
        self._call_on_main(self._apply, _IDLE)

    def set_listening(self) -> None:
        self._call_on_main(self._apply, _LISTENING)

    def set_processing(self) -> None:
        self._call_on_main(self._apply, _PROCESSING)

    def refresh(self) -> None:
        self._call_on_main(self._refresh)

    # --- main-thread work ------------------------------------------------

    def _show(self) -> None:
        self._refresh()
        AppKit = self._AppKit
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def _refresh(self) -> None:
        self._apply(self._state)
        self._reload_rows()

    def _apply(self, state: tuple[str, tuple[float, float, float]]) -> None:
        AppKit = self._AppKit
        label, color = state
        self._state = state

        self._state_label.setStringValue_(label)
        self._dot.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*color, 1.0).CGColor()
        )
        started = datetime.fromtimestamp(self._started_at).strftime("%H:%M")
        self._uptime_label.setStringValue_(
            f"Actief sinds {started} - {format_duration(time.time() - self._started_at)} draaiend"
        )

        config = self._config
        cleanup = (
            f"aan ({config.cleanup.provider}: {config.cleanup.model})"
            if config.cleanup.enabled
            else "uit"
        )
        values = {
            "engine": f"{self._engine_name}{' (cloud-boost)' if config.cloud_boost else ''}",
            "model": self._model or "onbekend",
            "language": config.language,
            "hotkey": config.hotkey,
            "cleanup": cleanup,
            "permissions": _permission_summary(),
        }
        for key, value in values.items():
            self._info_rows[key].setStringValue_(value)

    def _reload_rows(self) -> None:
        AppKit = self._AppKit
        entries = self._history.recent(_RECENT_LIMIT)
        stats = self._history.stats()

        self._stats_label.setStringValue_(
            f"Vandaag {stats.today} opnames - {stats.words_today} woorden - "
            f"{format_duration(stats.speech_seconds_today)} spraak - "
            f"gemiddeld {stats.average_transcribe_seconds:.1f}s transcriberen - "
            f"totaal {stats.total} opnames bewaard"
        )

        for view in list(self._rows.arrangedSubviews()):
            self._rows.removeArrangedSubview_(view)
            view.removeFromSuperview()

        # A fresh target per reload drops the closures of the rows we just
        # threw away, instead of growing a handler list forever.
        self._row_target = _target_class().alloc().init()
        self._empty_label.setHidden_(bool(entries))
        # One call instead of a loop of addArrangedSubview_, so the rows can
        # never end up half-replaced if building one of them raises.
        self._rows.setViews_inGravity_(
            [self._row(entry) for entry in entries], AppKit.NSStackViewGravityTop
        )
        self._rows.setHidden_(not entries)
        self._fit_height()
        self._window.contentView().setNeedsDisplay_(True)

    def _fit_height(self) -> None:
        """Grow the window to whatever the rows need, and no further.

        The number of rows only changes while the history fills up, so this
        settles after ten dictations instead of resizing under your hands.
        """
        from Foundation import NSMakeSize

        self._root.layoutSubtreeIfNeeded()
        height = self._root.fittingSize().height + 2 * _MARGIN
        self._window.setContentSize_(NSMakeSize(_WINDOW_WIDTH, height))

    def _row(self, entry: HistoryEntry):
        AppKit = self._AppKit
        when = self._label(f"{entry.local_day} {entry.local_time}", size=11, secondary=True)
        _fix_width(when, 74)
        duration = self._label(
            f"{entry.duration_seconds:.1f}s", size=11, secondary=True
        )
        _fix_width(duration, 44)
        words = self._label(f"{entry.word_count}w", size=11, secondary=True)
        _fix_width(words, 36)
        preview = self._label(entry.preview(), size=12)
        preview.setToolTip_(
            f"{entry.text}\n\n{entry.engine} - {entry.model}\n"
            f"transcriberen: {entry.transcribe_seconds:.1f}s"
            + ("" if entry.pasted else "\nniet geplakt: stond alleen op het klembord")
        )
        preview.setContentHuggingPriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        # Without this a long transcript pushes the whole window wider instead
        # of truncating into the space the row already has.
        preview.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        views = [when, duration, words, preview]
        if not entry.pasted:
            # This transcript never reached an app: without a visible mark the
            # row looks exactly like one that did land.
            views.append(self._label("klembord", size=10, secondary=True))
        if self._repaste is not None:
            paste = self._button("Plak", lambda text=entry.text: self._paste(text), self._row_target)
            paste.setControlSize_(AppKit.NSControlSizeSmall)
            paste.setFont_(AppKit.NSFont.systemFontOfSize_(11))
            views.append(paste)
        row = self._hstack(views, spacing=8)
        row.setDistribution_(AppKit.NSStackViewDistributionFill)
        return row

    def _paste(self, text: str) -> None:
        if self._repaste is None or not text:
            return
        AppKit = self._AppKit
        # Our window is frontmost while you click, so pasting now would put the
        # text in PraatTypen. Step aside first, then type into whatever was
        # underneath.
        AppKit.NSApplication.sharedApplication().hide_(None)

        def paste_later() -> None:
            # Off the main thread: injecting holds still for about half a
            # second, and stalling this run loop is what makes macOS switch the
            # Fn event tap off.
            time.sleep(_HIDE_BEFORE_PASTE_SECONDS)
            try:
                self._repaste(text)  # type: ignore[misc]
            except Exception as error:  # noqa: BLE001
                logger.error("Opnieuw plakken mislukt: %s", error)

        threading.Thread(target=paste_later, daemon=True, name="praattypen-repaste").start()

    def _clear_history(self) -> None:
        self._history.clear()
        self._reload_rows()

    def _open(self, path: Path | None) -> None:
        if path is None:
            return
        from Foundation import NSURL

        self._AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    # --- small AppKit helpers -------------------------------------------

    def _label(self, text: str, size: float = 12, bold: bool = False, secondary: bool = False):
        AppKit = self._AppKit
        field = AppKit.NSTextField.labelWithString_(text)
        field.setFont_(
            AppKit.NSFont.boldSystemFontOfSize_(size)
            if bold
            else AppKit.NSFont.systemFontOfSize_(size)
        )
        if secondary:
            field.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        field.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        return field

    def _circle(self, size: float):
        AppKit = self._AppKit
        from Foundation import NSMakeRect

        view = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, size, size))
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(size / 2)
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                view.widthAnchor().constraintEqualToConstant_(size),
                view.heightAnchor().constraintEqualToConstant_(size),
            ]
        )
        return view

    def _button(self, title: str, handler: Callable[[], None], target):
        AppKit = self._AppKit
        button = AppKit.NSButton.buttonWithTitle_target_action_(title, target, "invoke:")
        button.setTag_(target.addHandler_(handler))
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        return button

    def _separator(self):
        AppKit = self._AppKit
        box = AppKit.NSBox.alloc().init()
        box.setBoxType_(AppKit.NSBoxSeparator)
        return box

    def _vstack(self, views: list, spacing: float = 8):
        AppKit = self._AppKit
        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        stack.setSpacing_(spacing)
        return stack

    def _hstack(self, views: list, spacing: float = 8):
        AppKit = self._AppKit
        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        stack.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        stack.setSpacing_(spacing)
        return stack

    def _call_on_main(self, func, *args, **kwargs) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(func, *args, **kwargs)


def _fix_width(view, width: float) -> None:
    import AppKit

    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    AppKit.NSLayoutConstraint.activateConstraints_(
        [view.widthAnchor().constraintEqualToConstant_(width)]
    )


def _permission_summary() -> str:
    """The three switches that decide whether dictation can work at all."""
    trusted = {True: "toegestaan", False: "GEWEIGERD", None: "onbekend"}[
        accessibility_trusted()
    ]
    return (
        f"microfoon {microphone_status()} - invoermonitoring {hid_access(_LISTEN_EVENT)} - "
        f"toegankelijkheid {trusted}"
    )


def create_status_window(
    enabled: bool = True,
    config: AppConfig | None = None,
    engine_name: str = "",
    model: str = "",
    history: HistoryStore | None = None,
    config_path: Path | None = None,
    log_path: Path | None = None,
    repaste: Callable[[str], object] | None = None,
) -> StatusWindow:
    """Build the status window, falling back to a no-op silently.

    Same bargain as the overlay and the menu bar: PraatTypen keeps dictating
    without AppKit, just with one fewer affordance.
    """
    if not enabled:
        return NullStatusWindow()

    try:
        return RecordingStatusWindow(
            config=config or AppConfig(),
            engine_name=engine_name,
            model=model,
            history=history or HistoryStore(),
            config_path=config_path or Path("config.yaml"),
            log_path=log_path,
            repaste=repaste,
        )
    except ImportError as error:
        logger.warning(
            "Statusvenster uitgeschakeld: %s. Installeer pyobjc-framework-Cocoa "
            "voor het venster.",
            error,
        )
        return NullStatusWindow()
