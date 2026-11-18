from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)


class MenuBar(Protocol):
    """Status-bar item that mirrors what the overlay shows, plus a way out."""

    def set_idle(self) -> None:
        ...

    def set_listening(self) -> None:
        ...

    def set_processing(self) -> None:
        ...

    def set_last_transcript(self, text: str) -> None:
        ...


class NullMenuBar:
    """No-op menu bar, used when AppKit isn't available or it is disabled."""

    def set_idle(self) -> None:
        return None

    def set_listening(self) -> None:
        return None

    def set_processing(self) -> None:
        return None

    def set_last_transcript(self, text: str) -> None:
        return None


# Idle deliberately uses the outline symbol and listening the filled one: the
# difference is legible in the menu bar at a glance, tint alone is not.
_IDLE = ("mic", "Ready", None)
_LISTENING = ("mic.fill", "Listening…", (0.95, 0.25, 0.25))
_PROCESSING = ("waveform", "Transcribing…", (0.95, 0.65, 0.15))

# Long enough for the menu to be gone before Cmd+V fires.
_MENU_DISMISS_SECONDS = 0.15

# Long enough for AppKit to have laid the status item out.
_PLACEMENT_LOG_SECONDS = 3.0

# Roughly the size macOS uses for its own menu bar glyphs.
_ICON_POINTS = 18

_TARGET_CLASS = None


def _target_class():
    """Define the Objective-C action target once.

    PyObjC registers classes globally by name, so defining it a second time is
    an error -- which a second RecordingMenuBar in one process would trigger.
    """
    global _TARGET_CLASS
    if _TARGET_CLASS is not None:
        return _TARGET_CLASS

    import AppKit
    import objc

    class _PraatTypenMenuTarget(AppKit.NSObject):
        def init(self):
            self = objc.super(_PraatTypenMenuTarget, self).init()
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
            except Exception as error:  # noqa: BLE001 - a bad menu item must not kill the app
                logger.error("Fout in menu-actie: %s", error)

    _TARGET_CLASS = _PraatTypenMenuTarget
    return _TARGET_CLASS


class RecordingMenuBar:
    """The PraatTypen icon in the macOS menu bar.

    Must be constructed on the main thread (AppKit requirement). The public
    methods are safe to call from any thread: like the overlay, they marshal
    onto the main run loop instead of touching AppKit directly.
    """

    def __init__(
        self,
        engine_name: str,
        config_path: Path,
        log_path: Path | None,
        repaste: Callable[[str], None] | None = None,
        quit_app: Callable[[], None] | None = None,
        show_window: Callable[[], None] | None = None,
        modes=None,
    ) -> None:
        import AppKit

        self._modes = modes
        self._mode_items = {}

        self._AppKit = AppKit
        self._engine_name = engine_name
        self._config_path = config_path
        self._log_path = log_path
        self._repaste = repaste
        self._quit_app = quit_app
        self._show_window = show_window
        self._last_transcript = ""
        self._state = _IDLE
        self._item = None

        # Activation policy is set once in appearance.apply_activation_policy;
        # forcing it here would silently undo the Dock icon.
        AppKit.NSApplication.sharedApplication()

        # Claiming a slot in the status bar before the event loop runs gets us
        # an item that reports itself as visible but that the window server
        # never hears about -- no icon, no error. Launched from a terminal it
        # happens to work, which is exactly what makes it easy to miss. Build
        # it from the loop instead.
        from PyObjCTools import AppHelper

        AppHelper.callLater(0.0, self._build)

    def _build(self) -> None:
        AppKit = self._AppKit

        self._item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSVariableStatusItemLength
        )
        self._target = _target_class().alloc().init()

        menu = AppKit.NSMenu.alloc().init()
        # Our items carry an explicit target, but auto-enabling still greys out
        # the plain status line in a way that reads as "broken".
        menu.setAutoenablesItems_(False)

        self._status_item = self._add_item(menu, "Ready", None)
        self._status_item.setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        if self._show_window is not None:
            self._add_item(menu, "Open AirQuote…", self._show_window)
            menu.addItem_(AppKit.NSMenuItem.separatorItem())

        if self._modes is not None:
            from praattypen.modes import MODES

            mode_menu = AppKit.NSMenu.alloc().init()
            mode_menu.setAutoenablesItems_(False)
            for mode in MODES:
                item = self._add_item(
                    mode_menu, mode.title, lambda key=mode.key: self._modes.set(key)
                )
                self._mode_items[mode.key] = item
            mode_menu.addItem_(AppKit.NSMenuItem.separatorItem())
            self._commands_item = self._add_item(
                mode_menu,
                "Voice Commands",
                lambda: self._modes.set_commands(not self._modes.commands_enabled),
            )
            mode_root = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Mode", None, ""
            )
            mode_root.setSubmenu_(mode_menu)
            menu.addItem_(mode_root)
            self._sync_modes()
            self._modes.subscribe(lambda _mode: AppHelper_call(self._sync_modes))
            menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._repaste_item = self._add_item(
            menu, "Paste Last Dictation", self._repaste_last
        )
        self._repaste_item.setEnabled_(False)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._add_item(menu, "Open Settings File", lambda: self._open(self._config_path))
        if self._log_path is not None:
            self._add_item(menu, "Open Log", lambda: self._open(self._log_path))
        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._add_item(menu, "Quit AirQuote", self._quit_app or self._terminate)

        self._item.setMenu_(menu)
        # Whatever happened while the loop was still starting.
        self._apply(self._state)
        self._set_last_transcript(self._last_transcript)

        from PyObjCTools import AppHelper

        AppHelper.callLater(_PLACEMENT_LOG_SECONDS, self._log_placement)

    def _log_placement(self) -> None:
        """Record where the icon ended up.

        "I don't see it" has several causes that look identical from the
        outside -- no item, an item without an image, or an item pushed off
        screen behind the notch -- and none of them are visible in a log
        unless the position is written down.
        """
        if self._item is None:
            logger.warning("Menubalk-item is niet aangemaakt.")
            return
        button = self._item.button()
        window = button.window() if button is not None else None
        logger.info(
            "Menubalk-item aangemaakt: zichtbaar=%s, afbeelding=%s, positie=%s",
            self._item.isVisible(),
            button is not None and button.image() is not None,
            window.frame().origin if window is not None else "onbekend",
        )

    def set_idle(self) -> None:
        self._call_on_main(self._apply, _IDLE)

    def set_listening(self) -> None:
        self._call_on_main(self._apply, _LISTENING)

    def set_processing(self) -> None:
        self._call_on_main(self._apply, _PROCESSING)

    def set_last_transcript(self, text: str) -> None:
        self._call_on_main(self._set_last_transcript, text)

    def _add_item(self, menu, title: str, handler: Callable[[], None] | None):
        AppKit = self._AppKit
        item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, None, "")
        if handler is not None:
            item.setTarget_(self._target)
            item.setAction_("invoke:")
            item.setTag_(self._target.addHandler_(handler))
        menu.addItem_(item)
        return item

    def _sync_modes(self) -> None:
        if self._modes is None:
            return
        current = self._modes.current.key
        for key, item in self._mode_items.items():
            item.setState_(1 if key == current else 0)
        item = getattr(self, "_commands_item", None)
        if item is not None:
            item.setState_(1 if self._modes.commands_enabled else 0)

    def _apply(self, state: tuple[str, str, tuple[float, float, float] | None]) -> None:
        AppKit = self._AppKit
        symbol, label, tint = state

        # Remembered even before the item exists, so a dictation started during
        # startup still shows the right state once it does.
        self._state = state
        if self._item is None:
            return

        button = self._item.button()
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol, f"AirQuote: {label}"
        )
        if image is not None:
            # SF Symbols arrive at their natural point size, which a status
            # item does not scale for you; without this the glyph can end up
            # drawn at a size that shows as an empty gap.
            image.setSize_(AppKit.NSMakeSize(_ICON_POINTS, _ICON_POINTS))
            image.setTemplate_(True)
            button.setImage_(image)
        else:
            # Pre-Big Sur, or a symbol this macOS does not know.
            button.setTitle_("AQ")
        button.setContentTintColor_(
            None
            if tint is None
            else AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*tint, 1.0)
        )
        self._status_item.setTitle_(f"{label} ({self._engine_name})")

    def _set_last_transcript(self, text: str) -> None:
        self._last_transcript = text
        if self._item is None:
            return
        self._repaste_item.setEnabled_(bool(text))
        self._repaste_item.setTitle_(
            f"Paste Again: {_shorten(text)}" if text else "Paste Last Dictation"
        )

    def _repaste_last(self) -> None:
        if self._repaste is None or not self._last_transcript:
            return

        text = self._last_transcript

        def paste_later() -> None:
            # Off the main thread: injecting holds still for about half a
            # second, and stalling this run loop is what makes macOS switch the
            # Fn event tap off. The short wait lets the menu finish closing so
            # the keystroke lands in the app underneath, not in the menu.
            time.sleep(_MENU_DISMISS_SECONDS)
            try:
                self._repaste(text)  # type: ignore[misc]
            except Exception as error:  # noqa: BLE001
                logger.error("Opnieuw plakken mislukt: %s", error)

        threading.Thread(target=paste_later, daemon=True, name="praattypen-repaste").start()

    def _open(self, path: Path) -> None:
        AppKit = self._AppKit
        from Foundation import NSURL

        AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    def _terminate(self) -> None:
        self._AppKit.NSApplication.sharedApplication().terminate_(None)

    def _call_on_main(self, func, *args, **kwargs) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(func, *args, **kwargs)


def AppHelper_call(func) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(func)


def _shorten(text: str, limit: int = 40) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def create_menu_bar(
    enabled: bool = True,
    engine_name: str = "",
    config_path: Path | None = None,
    log_path: Path | None = None,
    repaste: Callable[[str], None] | None = None,
    quit_app: Callable[[], None] | None = None,
    show_window: Callable[[], None] | None = None,
    modes=None,
) -> MenuBar:
    """Build the menu-bar item, falling back to a no-op silently.

    Same bargain as the overlay and the Fn listener: PraatTypen keeps working
    without AppKit, just with one fewer affordance.
    """
    if not enabled:
        return NullMenuBar()

    try:
        return RecordingMenuBar(
            engine_name=engine_name,
            config_path=config_path or Path("config.yaml"),
            log_path=log_path,
            repaste=repaste,
            quit_app=quit_app,
            show_window=show_window,
            modes=modes,
        )
    except ImportError as error:
        logger.warning(
            "Menubalk uitgeschakeld: %s. Installeer pyobjc-framework-Cocoa voor het icoon.",
            error,
        )
        return NullMenuBar()
