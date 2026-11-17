from __future__ import annotations

from typing import Protocol

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)


class Overlay(Protocol):
    """On-screen indicator that mirrors Wispr Flow's floating status bar."""

    def show_listening(self) -> None:
        ...

    def show_processing(self) -> None:
        ...

    def hide(self) -> None:
        ...


class NullOverlay:
    """No-op overlay used when AppKit isn't available or overlay is disabled."""

    def show_listening(self) -> None:
        return None

    def show_processing(self) -> None:
        return None

    def hide(self) -> None:
        return None


class RecordingOverlay:
    """Small floating pill, shown while PraatTypen luistert or transcribeert.

    Must be constructed on the main thread (AppKit requirement). The public
    methods are safe to call from any thread: they marshal onto the main run
    loop via ``AppHelper.callAfter`` instead of touching AppKit directly.
    """

    _WIDTH = 220
    _HEIGHT = 44

    def __init__(self) -> None:
        import AppKit
        import Quartz  # noqa: F401 - registers CGColorRef bridging used below
        from Foundation import NSMakeRect

        self._AppKit = AppKit

        # Activation policy is set once in appearance.apply_activation_policy;
        # forcing it here would silently undo the Dock icon.
        AppKit.NSApplication.sharedApplication()

        screen_frame = AppKit.NSScreen.mainScreen().frame()
        x = screen_frame.origin.x + (screen_frame.size.width - self._WIDTH) / 2
        y = screen_frame.origin.y + 90

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, self._WIDTH, self._HEIGHT),
            AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(True)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )

        content = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self._WIDTH, self._HEIGHT)
        )
        content.setWantsLayer_(True)
        content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        content.layer().setCornerRadius_(self._HEIGHT / 2)
        window.setContentView_(content)

        dot = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(16, self._HEIGHT / 2 - 6, 12, 12)
        )
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(6)
        content.addSubview_(dot)

        label = AppKit.NSTextField.alloc().initWithFrame_(
            NSMakeRect(38, 0, self._WIDTH - 50, self._HEIGHT)
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(AppKit.NSColor.whiteColor())
        label.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        content.addSubview_(label)

        self._window = window
        self._dot = dot
        self._label = label

    def show_listening(self) -> None:
        self._call_on_main(self._set_state, "Luistert...", (0.95, 0.25, 0.25), pulse=True)

    def show_processing(self) -> None:
        self._call_on_main(self._set_state, "Transcriberen...", (0.95, 0.65, 0.15), pulse=False)

    def hide(self) -> None:
        self._call_on_main(self._hide)

    def _call_on_main(self, func, *args, **kwargs) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(func, *args, **kwargs)

    def _set_state(self, text: str, color: tuple[float, float, float], pulse: bool) -> None:
        AppKit = self._AppKit
        self._label.setStringValue_(text)
        self._dot.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*color, 1.0).CGColor()
        )
        if pulse:
            self._start_pulse()
        else:
            self._stop_pulse()
        self._window.orderFront_(None)

    def _hide(self) -> None:
        self._stop_pulse()
        self._window.orderOut_(None)

    def _start_pulse(self) -> None:
        from Quartz import CABasicAnimation

        animation = CABasicAnimation.animationWithKeyPath_("opacity")
        animation.setFromValue_(1.0)
        animation.setToValue_(0.35)
        animation.setDuration_(0.7)
        animation.setAutoreverses_(True)
        animation.setRepeatCount_(float("inf"))
        self._dot.layer().addAnimation_forKey_(animation, "pulse")

    def _stop_pulse(self) -> None:
        self._dot.layer().removeAnimationForKey_("pulse")
        self._dot.layer().setOpacity_(1.0)


def create_overlay(enabled: bool = True) -> Overlay:
    """Build the floating status overlay, falling back to a no-op silently.

    Keeps parity with how the Fn hotkey listener already degrades: PraatTypen
    should keep working without the overlay if AppKit isn't available.
    """
    if not enabled:
        return NullOverlay()

    try:
        return RecordingOverlay()
    except ImportError as error:
        logger.warning(
            "Overlay uitgeschakeld: %s. Installeer pyobjc-framework-Cocoa "
            "voor het zwevende statusbalkje.",
            error,
        )
        return NullOverlay()
