"""Shared look for the overlay and the status window.

One palette and one "orb" so the floating pill, the window header and the
menu bar read as the same product. Everything AppKit is imported lazily, like
the rest of the UI code, so the tests run without a window server.
"""

from __future__ import annotations

import math
from pathlib import Path

# Siri-like spectrum: blue -> violet -> pink -> orange, and back to blue.
SPECTRUM = [
    (0.27, 0.55, 1.00),
    (0.62, 0.40, 1.00),
    (1.00, 0.36, 0.66),
    (1.00, 0.62, 0.32),
    (0.27, 0.80, 0.98),
    (0.27, 0.55, 1.00),
]

READY = (0.20, 0.78, 0.45)
LISTENING = (1.00, 0.30, 0.42)
PROCESSING = (0.62, 0.40, 1.00)

_LANGUAGES = {
    "auto": "Auto-detect",
    "nl": "Dutch",
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
}

_HOTKEYS = {
    "fn": "Fn / Globe",
    "globe": "Fn / Globe",
    "function": "Fn / Globe",
    "right_option": "Right Option ⌥",
    "right_alt": "Right Option ⌥",
    "left_option": "Left Option ⌥",
    "left_alt": "Left Option ⌥",
    "right_command": "Right Command ⌘",
    "right_cmd": "Right Command ⌘",
    "left_command": "Left Command ⌘",
    "left_cmd": "Left Command ⌘",
    "right_control": "Right Control ⌃",
    "right_ctrl": "Right Control ⌃",
    "caps_lock": "Caps Lock ⇪",
    "capslock": "Caps Lock ⇪",
}

_ENGINES = {
    "local_whisper": "Whisper · on this Mac",
    "local_parakeet": "Parakeet · on this Mac",
    "cloud_groq": "Groq · cloud",
}


def language_name(code: str) -> str:
    parts = [part.strip() for part in code.split(",") if part.strip()]
    return " + ".join(_LANGUAGES.get(part, part.upper()) for part in parts) or code


def hotkey_name(hotkey: str) -> str:
    key = hotkey.strip().lower()
    return _HOTKEYS.get(key, key.replace("_", " ").title())


def engine_name(engine: str) -> str:
    return _ENGINES.get(engine, engine)


def model_name(model: str) -> str:
    """'…/whisper-large-v3-turbo-q4' -> 'Whisper Large v3 Turbo (4-bit)'."""
    if not model:
        return "Unknown"
    base = Path(model).name if "/" in model else model
    words, suffix = [], ""
    for part in base.replace("_", "-").split("-"):
        low = part.lower()
        if low in {"mlx", "community"}:
            continue
        if low in {"q4", "4bit"}:
            suffix = " (4-bit)"
        elif low in {"q8", "8bit"}:
            suffix = " (8-bit)"
        elif low.startswith("v") and low[1:].replace(".", "").isdigit():
            words.append(low)
        elif low.replace(".", "").isdigit() or any(ch.isdigit() for ch in low):
            words.append(part.upper() if len(part) <= 4 else part)
        else:
            words.append(part.capitalize())
    return " ".join(words) + suffix


def level_to_unit(rms: float) -> float:
    """Microphone RMS -> 0..1 on a loudness (dB) scale, for meters."""
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return max(0.0, min(1.0, (db + 58.0) / 42.0))


# --- AppKit / Core Animation helpers (main thread only) --------------------


def rgb(color: tuple[float, float, float], alpha: float = 1.0):
    import AppKit

    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(*color, alpha)


def cg(color: tuple[float, float, float], alpha: float = 1.0):
    return rgb(color, alpha).CGColor()


# The logo's orb: one diagonal sweep, blue -> violet -> pink -> orange.
LOGO_SWEEP = [
    (0.27, 0.55, 1.00),
    (0.62, 0.40, 1.00),
    (1.00, 0.36, 0.66),
    (1.00, 0.62, 0.32),
]


def _symbol_cgimage(name: str, point_size: float, scale: float = 2.0):
    """An SF Symbol at the given size, for use as a layer mask."""
    import AppKit

    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size * scale, AppKit.NSFontWeightBold
    )
    # On macOS a layer takes an NSImage as its contents directly.
    return image.imageWithSymbolConfiguration_(config) or image


class Orb:
    """The AirQuote logo, alive: a glowing gradient sphere with a quote mark.

    The colours flow by slowly turning an oversized gradient inside the
    sphere; the quote mark can bob, tilt and swell (see ``set_quote``).
    """

    def __init__(self, view, size: float) -> None:
        from Quartz import (
            CAGradientLayer,
            CALayer,
            CGPathCreateWithEllipseInRect,
            CGPointMake,
            CGRectMake,
            kCAGradientLayerRadial,
        )

        view.setWantsLayer_(True)
        root = view.layer()
        bounds = CGRectMake(0, 0, size, size)

        holder = CALayer.layer()
        holder.setFrame_(bounds)
        # The glow is a shadow cast by the sphere's outline.
        holder.setShadowPath_(CGPathCreateWithEllipseInRect(bounds, None))
        holder.setShadowColor_(cg(LOGO_SWEEP[1]))
        holder.setShadowOpacity_(0.85)
        holder.setShadowRadius_(size * 0.32)
        holder.setShadowOffset_((0, 0))

        body = CALayer.layer()
        body.setFrame_(bounds)
        body.setCornerRadius_(size / 2)
        body.setMasksToBounds_(True)

        # Larger than the sphere so turning it never shows a corner.
        flow = CAGradientLayer.layer()
        pad = size * 0.3
        flow.setFrame_(CGRectMake(-pad, -pad, size + 2 * pad, size + 2 * pad))
        # Same diagonal as the icon: blue top-left, orange bottom-right.
        # The visible sphere is the middle of this oversized layer, so the
        # sweep is squeezed into that middle to show all four colours.
        flow.setStartPoint_(CGPointMake(0.24, 0.76))
        flow.setEndPoint_(CGPointMake(0.76, 0.24))
        flow.setColors_([cg(color) for color in LOGO_SWEEP])

        shine = CAGradientLayer.layer()
        shine.setFrame_(bounds)
        shine.setType_(kCAGradientLayerRadial)
        shine.setStartPoint_(CGPointMake(0.32, 0.74))
        shine.setEndPoint_(CGPointMake(1.0, 0.0))
        shine.setColors_([cg((1, 1, 1), 0.55), cg((1, 1, 1), 0.0)])

        body.addSublayer_(flow)
        body.addSublayer_(shine)
        holder.addSublayer_(body)

        # White quote mark: a white layer seen through the symbol's shape.
        quote = CALayer.layer()
        glyph = size * 0.44
        quote.setFrame_(CGRectMake((size - glyph) / 2, (size - glyph) / 2, glyph, glyph))
        quote.setBackgroundColor_(cg((1, 1, 1)))
        image = _symbol_cgimage("quote.opening", glyph * 0.62)
        if image is not None:
            mask = CALayer.layer()
            mask.setFrame_(CGRectMake(0, 0, glyph, glyph))
            mask.setContents_(image)
            mask.setContentsGravity_("resizeAspect")
            mask.setContentsScale_(2.0)
            quote.setMask_(mask)
            quote.setShadowColor_(cg((0.25, 0.05, 0.35)))
            quote.setShadowOpacity_(0.35)
            quote.setShadowRadius_(size * 0.04)
            quote.setShadowOffset_((0, -size * 0.015))
            holder.addSublayer_(quote)
        root.addSublayer_(holder)

        self._holder = holder
        self._flow = flow
        self._quote = quote
        self._speed = 0.0

    def set_speed(self, seconds_per_turn: float) -> None:
        """How fast the colours flow; 0 holds them still."""
        from Quartz import CABasicAnimation

        if seconds_per_turn == self._speed:
            return
        self._speed = seconds_per_turn
        self._flow.removeAnimationForKey_("spin")
        if seconds_per_turn <= 0:
            return
        animation = CABasicAnimation.animationWithKeyPath_("transform.rotation.z")
        animation.setFromValue_(0.0)
        animation.setToValue_(-2 * math.pi)
        animation.setDuration_(seconds_per_turn)
        animation.setRepeatCount_(float("inf"))
        self._flow.addAnimation_forKey_(animation, "spin")

    def set_scale(self, scale: float) -> None:
        from Quartz import CATransform3DMakeScale

        self._holder.setTransform_(CATransform3DMakeScale(scale, scale, 1.0))

    def set_quote(self, scale: float = 1.0, lift: float = 0.0, tilt: float = 0.0) -> None:
        """Move the quote mark: grow by `scale`, rise `lift` points, turn `tilt` radians."""
        from Quartz import CATransform3DMakeScale, CATransform3DRotate, CATransform3DTranslate

        transform = CATransform3DMakeScale(scale, scale, 1.0)
        transform = CATransform3DTranslate(transform, 0, lift, 0)
        transform = CATransform3DRotate(transform, tilt, 0, 0, 1)
        self._quote.setTransform_(transform)

    def set_glow(self, color: tuple[float, float, float], opacity: float = 0.85) -> None:
        self._holder.setShadowColor_(cg(color))
        self._holder.setShadowOpacity_(opacity)
