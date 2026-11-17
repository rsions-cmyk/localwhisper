from __future__ import annotations

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)


def apply_activation_policy(dock_icon: bool) -> bool:
    """Decide whether PraatTypen shows up in the Dock. Returns what was applied.

    The bundle is marked ``LSUIElement`` so macOS launches it without giving it
    focus -- a dictation tool that steals the front app on login would paste
    into the wrong window. Promoting to ``Regular`` here adds the Dock icon
    afterwards, without the activation that launching as a regular app brings.
    """
    try:
        import AppKit
    except ImportError as error:
        logger.warning("Kan Dock-icoon niet instellen: %s", error)
        return False

    app = AppKit.NSApplication.sharedApplication()
    policy = (
        AppKit.NSApplicationActivationPolicyRegular
        if dock_icon
        else AppKit.NSApplicationActivationPolicyAccessory
    )
    applied = bool(app.setActivationPolicy_(policy))
    if dock_icon and applied:
        build_app_menu()
    logger.info("Dock-icoon: %s (toegepast=%s)", "aan" if dock_icon else "uit", applied)
    return applied


def build_app_menu() -> None:
    """Give the Dock icon a working Cmd+Q and a non-empty menu bar.

    A regular app with no main menu shows an empty bar when it is frontmost,
    and Cmd+Q does nothing -- both read as a broken app.
    """
    import AppKit

    app = AppKit.NSApplication.sharedApplication()
    if app.mainMenu() is not None:
        return

    main_menu = AppKit.NSMenu.alloc().init()
    app_item = AppKit.NSMenuItem.alloc().init()
    main_menu.addItem_(app_item)

    app_menu = AppKit.NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("About AirQuote", "orderFrontStandardAboutPanel:", "")
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_("Hide AirQuote", "hide:", "h")
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit AirQuote", "terminate:", "q")
    app_item.setSubmenu_(app_menu)

    app.setMainMenu_(main_menu)
