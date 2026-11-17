from praattypen.inject import ClipboardInjector


class FakeClipboard:
    def __init__(self, initial: str) -> None:
        self.value = initial
        self.history: list[str] = []

    def get(self) -> str:
        return self.value

    def set(self, text: str) -> None:
        self.value = text
        self.history.append(text)


def build_injector(clipboard: FakeClipboard, pasted: list[str], **kwargs):
    return ClipboardInjector(
        clipboard_get=clipboard.get,
        clipboard_set=clipboard.set,
        paste=lambda: pasted.append("cmd-v"),
        can_paste=lambda: True,
        **kwargs,
    )


def test_clipboard_injector_pastes_text_and_restores_clipboard() -> None:
    clipboard = FakeClipboard("oude clipboard")
    pasted: list[str] = []
    slept: list[float] = []

    injector = build_injector(
        clipboard,
        pasted,
        sleep=slept.append,
        pasteboard_delay=0.01,
        restore_delay=0.02,
    )

    assert injector.inject("Nieuwe tekst") is True
    assert clipboard.history == ["Nieuwe tekst", "oude clipboard"]
    assert pasted == ["cmd-v"]
    assert slept == [0.01, 0.02]


def test_clipboard_injector_skips_empty_text() -> None:
    clipboard = FakeClipboard("oude clipboard")
    pasted: list[str] = []

    injector = build_injector(clipboard, pasted)

    assert injector.inject("   ") is False
    assert clipboard.history == []
    assert pasted == []


def test_clipboard_injector_keeps_text_when_accessibility_is_denied() -> None:
    """Without Accessibility the paste is swallowed, so the transcript must
    survive on the clipboard instead of being overwritten by the old value."""
    clipboard = FakeClipboard("oude clipboard")
    pasted: list[str] = []

    injector = ClipboardInjector(
        clipboard_get=clipboard.get,
        clipboard_set=clipboard.set,
        paste=lambda: pasted.append("cmd-v"),
        can_paste=lambda: False,
    )

    assert injector.inject("Nieuwe tekst") is False
    assert clipboard.history == ["Nieuwe tekst"]
    assert clipboard.value == "Nieuwe tekst"
    assert pasted == []


def test_clipboard_injector_still_tries_when_permission_is_unknown() -> None:
    """``None`` means we could not check -- never a reason to refuse to paste."""
    clipboard = FakeClipboard("oude clipboard")
    pasted: list[str] = []

    injector = ClipboardInjector(
        clipboard_get=clipboard.get,
        clipboard_set=clipboard.set,
        paste=lambda: pasted.append("cmd-v"),
        can_paste=lambda: None,
        sleep=lambda _seconds: None,
    )

    assert injector.inject("Nieuwe tekst") is True
    assert pasted == ["cmd-v"]
