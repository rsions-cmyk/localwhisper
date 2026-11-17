from praattypen.overlay import NullOverlay, create_overlay


def test_null_overlay_methods_are_no_ops() -> None:
    overlay = NullOverlay()

    overlay.show_listening()
    overlay.show_processing()
    overlay.hide()


def test_create_overlay_returns_null_overlay_when_disabled() -> None:
    overlay = create_overlay(enabled=False)

    assert isinstance(overlay, NullOverlay)
