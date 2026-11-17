"""Custom vocabulary: names and jargon Whisper should spell your way.

Two layers, because neither is enough on its own:

1. Before transcribing, the terms go to Whisper as its "initial prompt". Whisper
   treats that as text it has just heard, so it leans towards those spellings
   ("Kemenade", "PraatTypen", "ILO") instead of the nearest common word.
2. After transcribing, any whole-word match that differs only in case is put
   back in your casing ("buas" -> "BUas"), which the prompt alone does not
   guarantee.

The list lives in a plain text file, one term per line, so it can also be
edited outside the app. Lines starting with # are ignored.
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_PATH = Path.home() / "Library" / "Application Support" / "PraatTypen" / "vocabulary.txt"

# Whisper's prompt window is 224 tokens; stay well inside it so the prompt
# never crowds out the audio context.
_MAX_PROMPT_CHARS = 600


class Vocabulary:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[float, list[str]] | None = None

    def terms(self) -> list[str]:
        """Current terms; rereads the file only when it changed."""
        with self._lock:
            try:
                mtime = self.path.stat().st_mtime
            except FileNotFoundError:
                return []
            if self._cache is None or self._cache[0] != mtime:
                try:
                    text = self.path.read_text(encoding="utf-8")
                except OSError as error:
                    logger.error("Woordenlijst niet gelezen: %s", error)
                    return self._cache[1] if self._cache else []
                self._cache = (mtime, parse_terms(text))
            return list(self._cache[1])

    def text(self) -> str:
        try:
            return self.path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def save(self, text: str) -> list[str]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text.rstrip() + "\n" if text.strip() else "", encoding="utf-8")
        return self.terms()

    def prompt(self) -> str | None:
        return build_prompt(self.terms())

    def apply_casing(self, text: str) -> str:
        return apply_casing(text, self.terms())


def parse_terms(text: str) -> list[str]:
    seen: set[str] = set()
    terms: list[str] = []
    for line in text.splitlines():
        for term in line.split(","):
            term = term.strip()
            if not term or term.startswith("#") or term.lower() in seen:
                continue
            seen.add(term.lower())
            terms.append(term)
    return terms


def build_prompt(terms: list[str]) -> str | None:
    if not terms:
        return None
    prompt = ""
    for term in terms:
        candidate = f"{prompt}, {term}" if prompt else term
        if len(candidate) > _MAX_PROMPT_CHARS:
            break
        prompt = candidate
    # Written like a sentence that has just been said: Whisper copies the
    # style of its prompt, so a bare list would invite list-like output.
    return f"{prompt}."


def apply_casing(text: str, terms: list[str]) -> str:
    for term in terms:
        pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
        text = pattern.sub(term, text)
    return text
