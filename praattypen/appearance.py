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
    about = app_menu.addItemWithTitle_action_keyEquivalent_("About AirQuote", "showAbout:", "")
    about.setTarget_(_about_target())
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_("Hide AirQuote", "hide:", "h")
    app_menu.addItem_(AppKit.NSMenuItem.separatorItem())
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit AirQuote", "terminate:", "q")
    app_item.setSubmenu_(app_menu)

    app.setMainMenu_(main_menu)


ORIGINAL_URL = "https://github.com/sebastiaanvankemenade-commits/localwhisper"
_ABOUT_TARGET = None


def about_credits():
    """Credits shown in the About window: the original work comes first."""
    import AppKit
    from Foundation import NSURL

    style = AppKit.NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(AppKit.NSTextAlignmentCenter)
    base = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(11),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.labelColor(),
        AppKit.NSParagraphStyleAttributeName: style,
    }
    text = AppKit.NSMutableAttributedString.alloc().init()

    def add(part: str, link: str | None = None, bold: bool = False) -> None:
        attributes = dict(base)
        if bold:
            attributes[AppKit.NSFontAttributeName] = AppKit.NSFont.boldSystemFontOfSize_(11)
        if link:
            attributes[AppKit.NSLinkAttributeName] = NSURL.URLWithString_(link)
        text.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(part, attributes)
        )

    add("Based on ")
    add("PraatTypen", link=ORIGINAL_URL, bold=True)
    add("\nby Sebastiaan van Kemenade,\nthe original app AirQuote is built on.\n\n")
    add("AirQuote changes by Rob Simons.\n\n")
    add("Whisper (OpenAI) on Apple MLX\n")
    add("pyannote speaker model (CC BY 4.0)")
    return text


def _about_target():
    global _ABOUT_TARGET
    if _ABOUT_TARGET is not None:
        return _ABOUT_TARGET

    import AppKit

    class _AirQuoteAbout(AppKit.NSObject):
        def showAbout_(self, _sender) -> None:
            app = AppKit.NSApplication.sharedApplication()
            app.activateIgnoringOtherApps_(True)
            bundle = AppKit.NSBundle.mainBundle()
            version = bundle.objectForInfoDictionaryKey_("CFBundleShortVersionString")
            options = {
                AppKit.NSAboutPanelOptionApplicationName: "AirQuote",
                AppKit.NSAboutPanelOptionApplicationVersion: str(version or "0.1.0"),
                # The build number; empty hides the "(…)" after the version.
                AppKit.NSAboutPanelOptionVersion: "",
                AppKit.NSAboutPanelOptionCredits: about_credits(),
                "Copyright": "© 2026 Sebastiaan van Kemenade (PraatTypen)\n"
                "and Rob Simons (AirQuote changes)",
            }
            icon = app.applicationIconImage()
            if icon is not None:
                options[AppKit.NSAboutPanelOptionApplicationIcon] = icon
            app.orderFrontStandardAboutPanelWithOptions_(options)

    _ABOUT_TARGET = _AirQuoteAbout.alloc().init()
    return _ABOUT_TARGET
