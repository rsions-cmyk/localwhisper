"""Dictation modes: what happens to your words between Whisper and the paste.

- dictate     plain transcript, as before
- to_english  Whisper's own speech translation (task="translate"), on-device
- to_dutch    transcript, then translated by a local Ollama model
- feedback    transcript, then rewritten by a local Ollama model into
              structured, constructive feedback for a student
- rewrite     your words are an instruction ("make this more academic"),
              applied by the local model to the selected or copied text

The Ollama modes keep everything on this Mac too. When Ollama is unreachable
the plain transcript is pasted instead, so a dictation is never lost.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

STATE_PATH = Path.home() / "Library" / "Application Support" / "PraatTypen" / "mode.txt"


@dataclass(frozen=True)
class Mode:
    key: str
    title: str
    short: str
    working: str  # overlay text while this mode is busy
    symbol: str  # SF Symbol


MODES = [
    Mode("dictate", "Dictate", "Dictate", "Transcribing…", "waveform"),
    Mode("to_english", "Translate to English", "→ English", "Translating…", "globe"),
    Mode("to_dutch", "Translate to Dutch", "→ Dutch", "Translating…", "globe.europe.africa"),
    Mode("feedback", "Student Feedback", "Feedback", "Writing feedback…", "text.bubble"),
    Mode("rewrite", "Rewrite Selection", "Rewrite", "Rewriting…", "wand.and.stars"),
]
_BY_KEY = {mode.key: mode for mode in MODES}


def get_mode(key: str) -> Mode:
    return _BY_KEY.get(key, MODES[0])


class ModeState:
    """The current mode, remembered across restarts, shared by all threads."""

    def __init__(self, path: Path = STATE_PATH, default: str = "dictate") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._listeners: list[Callable[[Mode], None]] = []
        try:
            saved = path.read_text(encoding="utf-8").strip()
        except OSError:
            saved = ""
        self._key = saved if saved in _BY_KEY else (default if default in _BY_KEY else "dictate")

    @property
    def current(self) -> Mode:
        with self._lock:
            return _BY_KEY[self._key]

    def set(self, key: str) -> None:
        if key not in _BY_KEY:
            return
        with self._lock:
            self._key = key
            listeners = list(self._listeners)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(key, encoding="utf-8")
        except OSError as error:
            logger.error("Modus niet bewaard: %s", error)
        mode = _BY_KEY[key]
        logger.info("Modus: %s", mode.title)
        for listener in listeners:
            try:
                listener(mode)
            except Exception as error:  # noqa: BLE001
                logger.error("Fout bij modus-wissel: %s", error)

    def subscribe(self, listener: Callable[[Mode], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def whisper_task(self) -> str:
        """'translate' makes Whisper itself output English."""
        return "translate" if self.current.key == "to_english" else "transcribe"


Generate = Callable[[str, str, float], str]


class ModeTransformer:
    """Applies the current mode's text step (the Ollama ones)."""

    def __init__(
        self,
        state: ModeState,
        model: str = "gemma4:12b",
        generate: Generate | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.state = state
        self.model = model
        self.timeout = timeout
        if generate is None:
            from praattypen.cleanup import ollama_generate

            def generate(prompt: str, model: str, timeout: float) -> str:
                # Kept loaded for half an hour, so only the first request
                # after a break pays the model's load time.
                return ollama_generate(prompt, model, timeout, think=False, keep_alive="30m")
        self.generate = generate
        # Selection/clipboard captured when the recording started (Rewrite).
        self.context = ""
        # Whether the clipboard holds something you copied just now.
        self.clipboard_fresh: Callable[[], bool] = lambda: True

    def capture(self) -> None:
        """Grab the text to rewrite, before our own paste touches anything.

        Runs on its own thread: an app that is slow to answer Accessibility
        queries must not stall the hotkey handler that calls this.
        """
        self.context = ""
        self._capture_thread = None
        if self.state.current.key != "rewrite":
            return

        allow_clipboard = self.clipboard_fresh()

        def grab() -> None:
            self.context = capture_context(allow_clipboard=allow_clipboard)

        self._capture_thread = threading.Thread(target=grab, daemon=True, name="airquote-context")
        self._capture_thread.start()

    def transform(self, text: str) -> str:
        mode = self.state.current
        if mode.key == "to_dutch":
            prompt = translation_prompt(text, "Dutch")
        elif mode.key == "feedback":
            prompt = feedback_prompt(text)
        elif mode.key == "rewrite":
            thread = getattr(self, "_capture_thread", None)
            if thread is not None:
                thread.join(timeout=2.0)
            if not self.context.strip():
                # Nothing selected and nothing freshly copied: rewriting would
                # mean inventing a target, so paste what was said instead.
                logger.info("Rewrite: geen selectie of vers gekopieerde tekst; gewoon gedicteerd.")
                return text
            prompt = rewrite_prompt(text, self.context)
        else:
            return text
        try:
            result = strip_thinking(self.generate(prompt, self.model, self.timeout)).strip()
        except Exception as error:  # noqa: BLE001 - never lose the dictation
            logger.error("%s mislukt (%s); ruwe tekst geplakt.", mode.title, error)
            return text
        return result or text


def strip_thinking(text: str) -> str:
    """Drop <think>…</think> blocks that reasoning models put before answers."""
    while "<think>" in text and "</think>" in text:
        start = text.index("<think>")
        end = text.index("</think>", start) + len("</think>")
        text = text[:start] + text[end:]
    return text


def translation_prompt(text: str, language: str) -> str:
    return (
        f"Translate the following dictated text into natural, fluent {language}. "
        "Keep the meaning, tone and register; keep names, course codes and "
        "technical terms as they are. If it is already in "
        f"{language}, only fix obvious dictation errors. "
        "Reply with the translation only: no quotes, notes or explanations.\n\n"
        f"Text:\n{text}"
    )


def feedback_prompt(text: str) -> str:
    return (
        "You are helping a university lecturer turn spoken, off-the-cuff remarks "
        "about a student's work into written feedback for that student.\n\n"
        "Rules:\n"
        "- Write in the same language the lecturer spoke (Dutch stays Dutch, "
        "English stays English).\n"
        "- Address the student directly, in a warm, professional and honest tone.\n"
        "- Use ONLY points the lecturer made. Do not invent strengths, problems, "
        "grades or examples.\n"
        "- Turn blunt remarks into constructive, specific, actionable wording "
        "without hiding the problem.\n"
        "- Structure it with these three short headings (translated into the "
        "feedback language): Strengths, Points to improve, Next steps. Leave out a "
        "heading if the lecturer said nothing for it.\n"
        "- Under each heading use short bullet points starting with '• '. Plain "
        "text only: no Markdown, no bold, no asterisks.\n"
        "- Reply with the feedback only.\n\n"
        f"Lecturer's spoken remarks:\n{text}"
    )


class ClipboardWatch:
    """Knows when *you* last copied something, ignoring AirQuote's own pastes.

    Rewrite may only fall back to the clipboard when you copied text just
    before speaking; old clipboard contents (or the text AirQuote itself put
    back after pasting) must never be rewritten by surprise. Polls the
    pasteboard's change counter twice a second, which costs next to nothing.
    """

    def __init__(self, interval: float = 0.5) -> None:
        self._lock = threading.Lock()
        self._count: int | None = None
        self._changed_at = 0.0
        self._own = 0
        self._interval = interval
        threading.Thread(target=self._poll, daemon=True, name="airquote-clipboard").start()

    def _current(self) -> int | None:
        try:
            import AppKit

            return int(AppKit.NSPasteboard.generalPasteboard().changeCount())
        except Exception:  # noqa: BLE001
            return None

    def _poll(self) -> None:
        import time as _time

        while True:
            count = self._current()
            with self._lock:
                if count is not None and count != self._count:
                    if self._count is not None and not self._own:
                        self._changed_at = _time.monotonic()
                    self._count = count
            _time.sleep(self._interval)

    def begin_own(self) -> None:
        with self._lock:
            self._own += 1

    def end_own(self) -> None:
        """After AirQuote pasted and restored: take the new counter as baseline."""
        count = self._current()
        with self._lock:
            self._own = max(0, self._own - 1)
            if count is not None:
                self._count = count

    def fresh(self, seconds: float = 120.0) -> bool:
        import time as _time

        with self._lock:
            return bool(self._changed_at) and _time.monotonic() - self._changed_at <= seconds


def capture_context(allow_clipboard: bool = True) -> str:
    """The text you are working on: the current selection, else the clipboard.

    The selection is read through the Accessibility API, which AirQuote already
    has for pasting. Not every app exposes it (some browsers and Electron apps
    don't); copying the text first (Cmd+C) always works.
    """
    selected = _selected_text()
    if selected:
        return selected
    if not allow_clipboard:
        return ""
    try:
        import AppKit

        board = AppKit.NSPasteboard.generalPasteboard()
        return str(board.stringForType_(AppKit.NSPasteboardTypeString) or "")
    except Exception:  # noqa: BLE001
        return ""


def _selected_text() -> str:
    try:
        from ApplicationServices import (
            AXUIElementCopyAttributeValue,
            AXUIElementCreateSystemWide,
            kAXFocusedUIElementAttribute,
            kAXSelectedTextAttribute,
        )

        system = AXUIElementCreateSystemWide()
        error, focused = AXUIElementCopyAttributeValue(system, kAXFocusedUIElementAttribute, None)
        if error or focused is None:
            return ""
        error, text = AXUIElementCopyAttributeValue(focused, kAXSelectedTextAttribute, None)
        if error or not text:
            return ""
        return str(text)
    except Exception:  # noqa: BLE001 - context is a bonus, never a failure
        return ""


def rewrite_prompt(instruction: str, context: str) -> str:
    if not context.strip():
        return (
            "Carry out this spoken request and reply with the resulting text only, "
            "ready to paste: no preamble, no Markdown.\n\n"
            f"Request:\n{instruction}"
        )
    return (
        "You edit text for a university lecturer. Apply the spoken instruction to "
        "the text below. Keep the text's language unless the instruction asks "
        "for another, keep facts, names and references intact, and do not add "
        "content that was not asked for. Reply with the resulting text only, "
        "ready to paste over the original: no preamble, no quotes, no Markdown.\n\n"
        f"Instruction:\n{instruction}\n\n"
        f"Text:\n{context}"
    )
