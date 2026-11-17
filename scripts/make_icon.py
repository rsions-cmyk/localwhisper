"""Draw the AirQuote icon: a glowing spectrum orb with a quotation mark.

Run by scripts/build_app.sh. Without an icon file the Dock shows a blank
placeholder, which is exactly as uninformative as having no Dock icon at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import AppKit
from Foundation import NSMakeRect, NSMakeSize

# macOS draws the tile itself at these sizes; iconutil wants every one of them.
_SIZES = [16, 32, 128, 256, 512]
_BACKGROUND = (0.13, 0.42, 0.92)
_GLYPH = "quote.opening"


def _tinted(glyph: AppKit.NSImage, color: AppKit.NSColor) -> AppKit.NSImage:
    """Return a recoloured copy of a symbol image.

    Drawing a template image does not pick up the current fill colour, which is
    how the glyph ends up black on the blue tile. Compositing the colour over
    the glyph's own canvas keeps the tint inside its alpha.
    """
    size = glyph.size()
    tinted = AppKit.NSImage.alloc().initWithSize_(size)
    tinted.lockFocus()
    glyph.drawAtPoint_fromRect_operation_fraction_(
        (0, 0), AppKit.NSZeroRect, AppKit.NSCompositingOperationSourceOver, 1.0
    )
    color.set()
    AppKit.NSRectFillUsingOperation(
        NSMakeRect(0, 0, size.width, size.height),
        AppKit.NSCompositingOperationSourceAtop,
    )
    tinted.unlockFocus()
    return tinted


def draw_icon(size: int) -> AppKit.NSImage:
    """Dark tile, glowing spectrum orb, white quotation mark: AirQuote."""
    image = AppKit.NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    image.lockFocus()

    def rgb(r, g, b, a=1.0):
        return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)

    # macOS icons leave a margin; filling edge to edge looks oversized next to
    # every other Dock tile.
    inset = size * 0.08
    rect = NSMakeRect(inset, inset, size - 2 * inset, size - 2 * inset)
    tile = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        rect, size * 0.22, size * 0.22
    )
    AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        rgb(0.16, 0.10, 0.30), rgb(0.05, 0.05, 0.12)
    ).drawInBezierPath_angle_(tile, -90)

    # Soft glow behind the orb.
    centre = size / 2
    radius = size * 0.27
    glow = AppKit.NSGradient.alloc().initWithColors_(
        [rgb(0.62, 0.40, 1.0, 0.55), rgb(1.0, 0.36, 0.66, 0.18), rgb(0.62, 0.40, 1.0, 0.0)]
    )
    tile.addClip()
    glow.drawFromCenter_radius_toCenter_radius_options_(
        (centre, centre), 0, (centre, centre), radius * 1.9, 0
    )

    orb_rect = NSMakeRect(centre - radius, centre - radius, 2 * radius, 2 * radius)
    orb = AppKit.NSBezierPath.bezierPathWithOvalInRect_(orb_rect)
    AppKit.NSGradient.alloc().initWithColors_(
        [rgb(0.27, 0.55, 1.0), rgb(0.62, 0.40, 1.0), rgb(1.0, 0.36, 0.66), rgb(1.0, 0.62, 0.32)]
    ).drawInBezierPath_angle_(orb, -35)
    AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        rgb(1, 1, 1, 0.55), rgb(1, 1, 1, 0.0)
    ).drawInBezierPath_relativeCenterPosition_(orb, (-0.35, 0.45))

    glyph = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(_GLYPH, "AirQuote")
    if glyph is not None:
        config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            size * 0.2, AppKit.NSFontWeightBold
        )
        glyph = _tinted(glyph.imageWithSymbolConfiguration_(config), AppKit.NSColor.whiteColor())

        glyph_size = glyph.size()
        origin = ((size - glyph_size.width) / 2, (size - glyph_size.height) / 2)
        glyph.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            NSMakeRect(origin[0], origin[1], glyph_size.width, glyph_size.height),
            AppKit.NSZeroRect,
            AppKit.NSCompositingOperationSourceOver,
            1.0,
            True,
            {AppKit.NSImageHintInterpolation: AppKit.NSImageInterpolationHigh},
        )

    image.unlockFocus()
    return image


def write_png(image: AppKit.NSImage, pixels: int, path: Path) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, pixels, pixels, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(context)
    image.drawInRect_fromRect_operation_fraction_(
        NSMakeRect(0, 0, pixels, pixels),
        AppKit.NSZeroRect,
        AppKit.NSCompositingOperationSourceOver,
        1.0,
    )
    AppKit.NSGraphicsContext.restoreGraphicsState()

    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    path.write_bytes(bytes(data))


def build(output: Path) -> Path:
    iconset = output.with_suffix(".iconset")
    iconset.mkdir(parents=True, exist_ok=True)

    for size in _SIZES:
        for scale in (1, 2):
            pixels = size * scale
            suffix = "" if scale == 1 else "@2x"
            write_png(draw_icon(pixels), pixels, iconset / f"icon_{size}x{size}{suffix}.png")

    subprocess.run(
        ["iconutil", "--convert", "icns", str(iconset), "--output", str(output)],
        check=True,
        capture_output=True,
    )
    for leftover in iconset.iterdir():
        leftover.unlink()
    iconset.rmdir()
    return output


if __name__ == "__main__":
    target = Path(sys.argv[1])
    print(f"Icoon geschreven: {build(target)}")
