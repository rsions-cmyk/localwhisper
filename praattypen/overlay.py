from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Protocol

from praattypen import ui
from praattypen.logging_setup import get_logger

logger = get_logger(__name__)


class Overlay(Protocol):
    """Floating pill that shows PraatTypen is listening, thinking or done."""

    def show_listening(self) -> None:
        ...

    def show_hands_free(self) -> None:
        ...

    def show_processing(self, label: str = "Transcribing…") -> None:
        ...

    def show_done(self, pasted: bool = True, note: str = "", label: str = "") -> None:
        ...

    def hide(self) -> None:
        ...

    def set_click_handler(self, handler) -> None:
        ...

    def set_mode_source(self, source) -> None:
        ...


class NullOverlay:
    """No-op overlay used when AppKit isn't available or overlay is disabled."""

    def show_listening(self) -> None:
        return None

    def show_hands_free(self) -> None:
        return None

    def show_processing(self, label: str = "Transcribing…") -> None:
        return None

    def show_done(self, pasted: bool = True, note: str = "", label: str = "") -> None:
        return None

    def hide(self) -> None:
        return None

    def set_click_handler(self, handler) -> None:
        return None

    def set_mode_source(self, source) -> None:
        return None


_FPS = 60.0
# How long "Pasted" stays up before the pill fades away.
_DONE_SECONDS = 0.9


class RecordingOverlay:
    """Frosted-glass pill near the bottom of the screen.

    [orb]  ▁▃▆█▆▃▁  0:04

    The orb turns and swells with your voice, the bars follow the microphone
    level, and the label shows the elapsed time, "Transcribing" or "Pasted".

    Must be constructed on the main thread (AppKit requirement). The public
    methods are safe to call from any thread: they marshal onto the main run
    loop via ``AppHelper.callAfter`` instead of touching AppKit directly.
    """

    _HEIGHT = 54
    _ORB = 28
    _BARS = 19
    _BAR_WIDTH = 3.0
    _BAR_GAP = 2.6
    _BAR_AREA_HEIGHT = 30
    _LABEL_WIDTH = 132
    _PAD = 15

    def __init__(self, level_source: Callable[[], float] | None = None) -> None:
        import AppKit
        import Quartz  # noqa: F401 - registers CGColorRef bridging used below
        from Foundation import NSMakeRect
        from Quartz import CAGradientLayer, CALayer, CGPointMake, CGRectMake

        self._AppKit = AppKit
        self._level_source = level_source
        self._mode = "hidden"
        self._generation = 0
        self._started = 0.0
        self._done_at = 0.0
        self._level = 0.0
        self._phase = 0.0
        self._last_tick = time.monotonic()
        self._timer = None
        self._processing_label = "Transcribing…"

        # Activation policy is set once in appearance.apply_activation_policy;
        # forcing it here would silently undo the Dock icon.
        AppKit.NSApplication.sharedApplication()

        bars_width = self._BARS * (self._BAR_WIDTH + self._BAR_GAP) - self._BAR_GAP
        self._width = (
            self._PAD + self._ORB + 14 + bars_width + 14 + self._LABEL_WIDTH + self._PAD - 6
        )
        width, height = self._width, self._HEIGHT

        window = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, width, height),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setOpaque_(False)
        window.setBackgroundColor_(AppKit.NSColor.clearColor())
        window.setLevel_(AppKit.NSStatusWindowLevel)
        window.setIgnoresMouseEvents_(True)
        window.setHasShadow_(True)
        window.setAlphaValue_(0.0)
        window.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )

        glass = AppKit.NSVisualEffectView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        glass.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        glass.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        glass.setState_(AppKit.NSVisualEffectStateActive)
        glass.setAppearance_(AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua))
        # The window server draws the blur itself and ignores the layer's
        # corner radius, so without a mask image the rectangular glass shows
        # in the corners (white on a light background). A pill-shaped mask
        # shapes the blur and the window shadow alike.
        glass.setMaskImage_(_pill_mask(width, height))
        glass.setWantsLayer_(True)
        glass.layer().setCornerRadius_(height / 2)
        glass.layer().setMasksToBounds_(True)
        glass.layer().setBorderWidth_(0.5)
        glass.layer().setBorderColor_(ui.cg((1, 1, 1), 0.16))
        window.setContentView_(glass)

        # Faint spectrum wash over the glass, so the pill is not just grey.
        tint = CAGradientLayer.layer()
        tint.setFrame_(CGRectMake(0, 0, width, height))
        tint.setStartPoint_(CGPointMake(0, 0.5))
        tint.setEndPoint_(CGPointMake(1, 0.5))
        tint.setColors_([ui.cg(color, 0.10) for color in ui.SPECTRUM[:4]])
        glass.layer().addSublayer_(tint)

        x = self._PAD
        orb_view = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(x, (height - self._ORB) / 2, self._ORB, self._ORB)
        )
        glass.addSubview_(orb_view)
        self._orb = ui.Orb(orb_view, self._ORB)
        x += self._ORB + 14

        bars_view = AppKit.NSView.alloc().initWithFrame_(
            NSMakeRect(x, (height - self._BAR_AREA_HEIGHT) / 2, bars_width, self._BAR_AREA_HEIGHT)
        )
        bars_view.setWantsLayer_(True)
        glass.addSubview_(bars_view)
        # Bars are white shapes inside a mask; the gradient behind them gives
        # each bar its colour, like a spectrum seen through slits.
        gradient = CAGradientLayer.layer()
        gradient.setFrame_(CGRectMake(0, 0, bars_width, self._BAR_AREA_HEIGHT))
        gradient.setStartPoint_(CGPointMake(0, 0.5))
        gradient.setEndPoint_(CGPointMake(1, 0.5))
        gradient.setColors_([ui.cg(color) for color in ui.SPECTRUM[:4]])
        mask = CALayer.layer()
        mask.setFrame_(CGRectMake(0, 0, bars_width, self._BAR_AREA_HEIGHT))
        self._bars = []
        for index in range(self._BARS):
            bar = CALayer.layer()
            bar.setBackgroundColor_(ui.cg((1, 1, 1)))
            bar.setCornerRadius_(self._BAR_WIDTH / 2)
            bar.setFrame_(
                CGRectMake(
                    index * (self._BAR_WIDTH + self._BAR_GAP),
                    (self._BAR_AREA_HEIGHT - self._BAR_WIDTH) / 2,
                    self._BAR_WIDTH,
                    self._BAR_WIDTH,
                )
            )
            mask.addSublayer_(bar)
            self._bars.append(bar)
        gradient.setMask_(mask)
        bars_view.layer().addSublayer_(gradient)
        x += bars_width + 14

        label = AppKit.NSTextField.labelWithString_("")
        label.setFrame_(NSMakeRect(x, (height - 18) / 2, self._LABEL_WIDTH, 18))
        label.setTextColor_(AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.92))
        label.setFont_(
            AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(13, AppKit.NSFontWeightMedium)
        )
        label.setWantsLayer_(True)
        glass.addSubview_(label)

        # Clicking the bar stops the recording. It is a non-activating panel,
        # so the click never steals focus from the app the text goes into.
        from praattypen.statuswindow import _target_class

        self._click_handler = None
        self._mode_source = None
        self._done_note = ""
        self._done_label = ""
        self._click_target = _target_class().alloc().init()
        catcher = AppKit.NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        catcher.setTitle_("")
        catcher.setBordered_(False)
        catcher.setTransparent_(True)
        catcher.setTarget_(self._click_target)
        catcher.setAction_("invoke:")
        catcher.setTag_(self._click_target.addHandler_(self._clicked))
        catcher.setToolTip_("Click to stop and paste")
        glass.addSubview_(catcher)

        self._window = window
        self._glass = glass
        self._label = label

    # --- public API (any thread) ----------------------------------------

    def show_listening(self) -> None:
        self._call_on_main(self._enter, "listening")

    def show_hands_free(self) -> None:
        self._call_on_main(self._enter, "hands_free")

    def show_processing(self, label: str = "Transcribing…") -> None:
        self._processing_label = label
        self._call_on_main(self._enter, "processing")

    def show_done(self, pasted: bool = True, note: str = "", label: str = "") -> None:
        """note: a warning, held longer (orange); label: what was done ("→ English")."""
        self._done_note = note
        self._done_label = label
        self._call_on_main(self._enter, "done" if pasted else "copied")

    def hide(self) -> None:
        self._call_on_main(self._hide)

    def set_click_handler(self, handler) -> None:
        self._click_handler = handler

    def set_mode_source(self, source) -> None:
        """Callable returning the current Mode, so the bar can name it."""
        self._mode_source = source

    def _clicked(self) -> None:
        if self._mode in ("listening", "hands_free") and self._click_handler is not None:
            self._click_handler()

    def _call_on_main(self, func, *args) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(func, *args)

    # --- main thread ----------------------------------------------------

    def _enter(self, mode: str) -> None:
        AppKit = self._AppKit
        previous = self._mode
        self._mode = mode
        self._generation += 1
        self._label.layer().removeAllAnimations()
        self._label.layer().setOpacity_(1.0)
        white = AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.92)
        self._label.setTextColor_(white)

        if mode in ("listening", "hands_free"):
            if previous not in ("listening", "hands_free"):
                self._started = time.monotonic()
                self._level = 0.0
            self._orb.set_speed(5.0)
            self._update_timer_label()
        elif mode == "processing":
            self._orb.set_glow(ui.PROCESSING)
            self._orb.set_speed(0.9)
            self._label.setStringValue_(self._processing_label)
            self._label.setTextColor_(AppKit.NSColor.colorWithWhite_alpha_(1.0, 0.75))
            self._pulse_label()
        else:
            self._done_at = time.monotonic()
            self._orb.set_glow(ui.READY, 0.9)
            self._orb.set_speed(6.0)
            if mode == "done":
                pasted_text = f"✓  Pasted · {self._done_label}" if self._done_label else "✓  Pasted"
                self._label.setStringValue_(self._done_note or pasted_text)
                self._label.setTextColor_(
                    ui.rgb((1.0, 0.78, 0.45)) if self._done_note else ui.rgb((0.55, 0.95, 0.65))
                )
            else:
                self._label.setStringValue_("Copied")
                self._label.setTextColor_(white)

        # Clickable only while there is something to stop.
        self._window.setIgnoresMouseEvents_(
            mode not in ("listening", "hands_free") or self._click_handler is None
        )
        if previous == "hidden":
            self._appear()
        self._ensure_timer()

    def _update_timer_label(self) -> None:
        elapsed = int(time.monotonic() - self._started)
        clock = f"{elapsed // 60}:{elapsed % 60:02d}"
        mode = self._mode_source() if self._mode_source is not None else None
        if mode is not None and mode.key != "dictate":
            # Anything but plain dictation changes what gets pasted: say so.
            self._label.setStringValue_(f"{clock}  · {mode.short}")
            self._label.setTextColor_(ui.rgb((1.0, 0.78, 0.45)))
        else:
            self._label.setStringValue_(
                f"{clock}  · click to stop" if self._mode == "hands_free" else clock
            )

    def _pulse_label(self) -> None:
        from Quartz import CABasicAnimation

        animation = CABasicAnimation.animationWithKeyPath_("opacity")
        animation.setFromValue_(1.0)
        animation.setToValue_(0.45)
        animation.setDuration_(0.8)
        animation.setAutoreverses_(True)
        animation.setRepeatCount_(float("inf"))
        self._label.layer().addAnimation_forKey_(animation, "pulse")

    def _position(self, rise: float = 0.0):
        from Foundation import NSMakeRect

        AppKit = self._AppKit
        screen = AppKit.NSScreen.mainScreen() or AppKit.NSScreen.screens()[0]
        visible = screen.visibleFrame()
        x = visible.origin.x + (visible.size.width - self._width) / 2
        y = visible.origin.y + 28 + rise
        return NSMakeRect(x, y, self._width, self._HEIGHT)

    def _appear(self) -> None:
        AppKit = self._AppKit
        self._window.setFrame_display_(self._position(rise=-10), False)
        self._window.setAlphaValue_(0.0)
        self._window.orderFrontRegardless()
        self._window.invalidateShadow()
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.22)
        self._window.animator().setAlphaValue_(1.0)
        self._window.animator().setFrame_display_(self._position(), True)
        AppKit.NSAnimationContext.endGrouping()

    def _hide(self) -> None:
        from PyObjCTools import AppHelper

        if self._mode == "hidden":
            return
        if self._mode in ("done", "copied"):
            # Let "Pasted" be read before the pill goes.
            hold = _DONE_SECONDS * 3 if self._done_note else _DONE_SECONDS
            wait = hold - (time.monotonic() - self._done_at)
            if wait > 0:
                generation = self._generation
                AppHelper.callLater(wait, self._hide_if, generation)
                return
        self._fade_out()

    def _hide_if(self, generation: int) -> None:
        if generation == self._generation:
            self._fade_out()

    def _fade_out(self) -> None:
        from PyObjCTools import AppHelper

        AppKit = self._AppKit
        self._mode = "hidden"
        self._generation += 1
        generation = self._generation
        AppKit.NSAnimationContext.beginGrouping()
        AppKit.NSAnimationContext.currentContext().setDuration_(0.25)
        self._window.animator().setAlphaValue_(0.0)
        self._window.animator().setFrame_display_(self._position(rise=-8), True)
        AppKit.NSAnimationContext.endGrouping()
        AppHelper.callLater(0.3, self._order_out_if, generation)

    def _order_out_if(self, generation: int) -> None:
        if generation == self._generation and self._mode == "hidden":
            self._window.orderOut_(None)
            self._stop_timer()
            self._orb.set_speed(0)
            self._label.layer().removeAllAnimations()

    # --- animation loop -------------------------------------------------

    def _ensure_timer(self) -> None:
        if self._timer is not None:
            return
        Foundation = __import__("Foundation")
        self._last_tick = time.monotonic()
        self._timer = Foundation.NSTimer.timerWithTimeInterval_repeats_block_(
            1.0 / _FPS, True, self._tick
        )
        Foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(
            self._timer, Foundation.NSRunLoopCommonModes
        )

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    def _tick(self, _timer) -> None:
        try:
            self._draw_frame()
        except Exception as error:  # noqa: BLE001 - a drawing bug must not kill dictation
            logger.error("Overlay-animatie gestopt: %s", error)
            self._stop_timer()

    def _draw_frame(self) -> None:
        from Quartz import CATransaction, CGRectMake

        now = time.monotonic()
        dt = min(0.1, now - self._last_tick)
        self._last_tick = now
        mode = self._mode

        if mode in ("listening", "hands_free"):
            raw = ui.level_to_unit(self._level_source()) if self._level_source else 0.0
            # Fast attack, slow release: the bars jump with a syllable and
            # settle gently, the way a VU meter does.
            rate = 0.55 if raw > self._level else 0.12
            self._level += (raw - self._level) * rate
            amplitude = 0.10 + 0.90 * self._level
            self._phase += dt * (5.0 + 6.0 * self._level)
            self._orb.set_scale(1.0 + 0.10 * self._level)
            # The quote marks breathe with your voice: they swell on loud
            # syllables, bob gently and tilt a few degrees back and forth.
            self._orb.set_quote(
                scale=1.0 + 0.22 * self._level,
                lift=0.9 * math.sin(self._phase * 0.45),
                tilt=0.09 * math.sin(self._phase * 0.3),
            )
            glow = 0.5 + 0.5 * math.sin(self._phase * 0.25)
            self._orb.set_glow(_mix(ui.LOGO_SWEEP[1], ui.LOGO_SWEEP[2], glow), 0.7 + 0.3 * self._level)
            if int(now * 4) != int((now - dt) * 4):
                self._update_timer_label()
        elif mode == "processing":
            amplitude = 0.30
            self._phase += dt * 7.0
            self._orb.set_scale(1.0 + 0.05 * math.sin(self._phase * 0.8))
            self._orb.set_quote(scale=1.0 + 0.06 * math.sin(self._phase * 0.8))
        else:
            amplitude = max(0.0, 0.25 - (now - self._done_at) * 0.6)
            self._phase += dt * 3.0
            self._orb.set_scale(1.0)
            self._orb.set_quote()

        count = len(self._bars)
        area = self._BAR_AREA_HEIGHT
        floor = self._BAR_WIDTH
        CATransaction.begin()
        CATransaction.setDisableActions_(True)
        for index, bar in enumerate(self._bars):
            position = index / (count - 1) * 2 - 1  # -1 .. 1
            if mode == "processing":
                # A ripple travelling left to right: "working on it".
                shape = 0.55 + 0.45 * math.sin(self._phase - index * 0.55)
                envelope = 0.75 + 0.25 * math.exp(-position * position * 2.0)
            else:
                shape = 0.55 + 0.45 * math.sin(self._phase + index * 0.7) * math.cos(
                    self._phase * 0.6 - index * 0.35
                )
                envelope = math.exp(-position * position * 2.4)
            bar_height = floor + (area - floor) * amplitude * envelope * shape
            frame = bar.frame()
            bar.setFrame_(
                CGRectMake(frame.origin.x, (area - bar_height) / 2, self._BAR_WIDTH, bar_height)
            )
        CATransaction.commit()


def _pill_mask(width: float, height: float):
    """A black pill on transparent, as an NSVisualEffectView mask image."""
    import AppKit
    from Foundation import NSMakeRect, NSMakeSize

    radius = height / 2
    image = AppKit.NSImage.alloc().initWithSize_(NSMakeSize(width, height))
    image.lockFocus()
    AppKit.NSColor.blackColor().set()
    AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(0, 0, width, height), radius, radius
    ).fill()
    image.unlockFocus()
    return image


def _mix(a, b, t: float):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def create_overlay(
    enabled: bool = True, level_source: Callable[[], float] | None = None
) -> Overlay:
    """Build the floating status overlay, falling back to a no-op silently.

    Keeps parity with how the Fn hotkey listener already degrades: PraatTypen
    should keep working without the overlay if AppKit isn't available.
    """
    if not enabled:
        return NullOverlay()

    try:
        return RecordingOverlay(level_source=level_source)
    except ImportError as error:
        logger.warning(
            "Overlay uitgeschakeld: %s. Installeer pyobjc-framework-Cocoa "
            "voor het zwevende statusbalkje.",
            error,
        )
        return NullOverlay()
