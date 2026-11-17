from __future__ import annotations

import time
from collections.abc import Callable

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

# Virtual keycodes are layout-independent: kVK_ANSI_V and kVK_Command.
_VK_V = 0x09
_VK_COMMAND = 0x37


class ClipboardInjector:
    def __init__(
        self,
        clipboard_get: Callable[[], str] | None = None,
        clipboard_set: Callable[[str], None] | None = None,
        paste: Callable[[], None] | None = None,
        can_paste: Callable[[], bool | None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        pasteboard_delay: float = 0.05,
        restore_delay: float = 0.4,
    ) -> None:
        self.clipboard_get = clipboard_get or _pyperclip_get
        self.clipboard_set = clipboard_set or _pyperclip_set
        self.paste = paste or _press_cmd_v
        self.can_paste = can_paste or _accessibility_trusted
        self.sleep = sleep
        self.pasteboard_delay = pasteboard_delay
        self.restore_delay = restore_delay

    def inject(self, text: str) -> bool:
        """Paste ``text`` at the cursor. Returns False if it stayed on the clipboard.

        Without Accessibility macOS drops our Cmd+V without a word, and the old
        clipboard would then be restored over a transcript that never landed
        anywhere -- the dictation is simply gone. So when we know the paste
        cannot work, the text is left on the clipboard for a manual Cmd+V.
        """
        if not text.strip():
            return False

        allowed = self.can_paste()
        old_clipboard = self.clipboard_get()
        self.clipboard_set(text)

        if allowed is False:
            logger.warning(
                "Niet geplakt: PraatTypen heeft geen Toegankelijkheid, dus macOS negeert "
                "Cmd+V. De tekst staat op je klembord -- plak zelf met Cmd+V. "
                "Aanzetten: Systeeminstellingen -> Privacy en beveiliging -> Toegankelijkheid."
            )
            return False

        try:
            self.sleep(self.pasteboard_delay)
            self.paste()
            self.sleep(self.restore_delay)
        finally:
            self.clipboard_set(old_clipboard)
        return True


def _pyperclip_get() -> str:
    import pyperclip

    return str(pyperclip.paste())


def _pyperclip_set(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


def _accessibility_trusted() -> bool | None:
    from praattypen.permissions import accessibility_trusted

    return accessibility_trusted()


def _press_cmd_v() -> None:
    """Post Cmd+V through Quartz rather than pynput.

    pynput translates "v" to a keycode through HIToolbox
    (TSMGetInputSourceProperty), which asserts that it runs on the main dispatch
    queue. Started from a terminal that assertion had no main queue to fail
    against; inside an app bundle it does, and the transcription thread trips it
    mid-paste -- macOS then kills the whole process with SIGTRAP, which is why
    PraatTypen disappeared while pasting. CGEventPost has no such requirement
    and V sits at a fixed virtual keycode on every layout.

    The Command key is pressed for real, not just set as a flag: apps that draw
    their own menus (Electron, Java) watch for the modifier's key events and
    ignore a Cmd+V that only carries the flag.
    """
    import Quartz

    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    command_down = Quartz.CGEventCreateKeyboardEvent(source, _VK_COMMAND, True)
    v_down = Quartz.CGEventCreateKeyboardEvent(source, _VK_V, True)
    v_up = Quartz.CGEventCreateKeyboardEvent(source, _VK_V, False)
    command_up = Quartz.CGEventCreateKeyboardEvent(source, _VK_COMMAND, False)

    # Set explicitly instead of inheriting whatever is physically held down:
    # a stray Fn or Shift still under the user's finger would turn Cmd+V into
    # a different shortcut.
    Quartz.CGEventSetFlags(command_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(v_down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(v_up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(command_up, 0)

    tap = Quartz.kCGHIDEventTap
    try:
        Quartz.CGEventPost(tap, command_down)
        Quartz.CGEventPost(tap, v_down)
        Quartz.CGEventPost(tap, v_up)
    finally:
        # Never leave Command stuck down, whatever happened above: every click
        # and keystroke afterwards would be a shortcut.
        Quartz.CGEventPost(tap, command_up)
