from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

HISTORY_PATH = (
    Path.home() / "Library" / "Application Support" / "PraatTypen" / "history.jsonl"
)

# Enough to look back over a few working days in the status window, small
# enough that rereading the whole file on every refresh stays instant.
MAX_ENTRIES = 200


@dataclass(frozen=True)
class HistoryEntry:
    """One finished dictation, as it should read back in the status window."""

    text: str
    timestamp: float = field(default_factory=time.time)
    duration_seconds: float = 0.0
    transcribe_seconds: float = 0.0
    engine: str = ""
    model: str = ""
    pasted: bool = True

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def local_time(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%H:%M")

    @property
    def local_day(self) -> str:
        return datetime.fromtimestamp(self.timestamp).strftime("%d-%m")

    def is_today(self, today: date | None = None) -> bool:
        return datetime.fromtimestamp(self.timestamp).date() == (today or date.today())

    def preview(self, limit: int = 60) -> str:
        collapsed = " ".join(self.text.split())
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[: limit - 1].rstrip() + "…"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "text": self.text,
            "duration_seconds": self.duration_seconds,
            "transcribe_seconds": self.transcribe_seconds,
            "engine": self.engine,
            "model": self.model,
            "pasted": self.pasted,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "HistoryEntry":
        return cls(
            text=str(raw.get("text", "")),
            timestamp=float(raw.get("timestamp", 0.0)),
            duration_seconds=float(raw.get("duration_seconds", 0.0)),
            transcribe_seconds=float(raw.get("transcribe_seconds", 0.0)),
            engine=str(raw.get("engine", "")),
            model=str(raw.get("model", "")),
            pasted=bool(raw.get("pasted", True)),
        )


@dataclass(frozen=True)
class HistoryStats:
    total: int = 0
    today: int = 0
    words_today: int = 0
    words_total: int = 0
    speech_seconds_today: float = 0.0
    average_transcribe_seconds: float = 0.0


class HistoryStore:
    """Append-only JSONL log of finished dictations, trimmed to the last N.

    Writes happen on the worker thread right after a dictation and reads on the
    main thread when the status window refreshes, so both go through one lock.
    A history that cannot be written must never break dictation: every failure
    here is logged and swallowed.
    """

    def __init__(
        self,
        path: Path | None = None,
        enabled: bool = True,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self.path = Path(path) if path is not None else HISTORY_PATH
        self.enabled = enabled
        self.max_entries = max_entries
        self._lock = threading.Lock()

    def add(self, entry: HistoryEntry) -> None:
        if not self.enabled or not entry.text.strip():
            return
        with self._lock:
            entries = self._read()
            entries.append(entry)
            self._write(entries[-self.max_entries :])

    def recent(self, limit: int = 10) -> list[HistoryEntry]:
        """Newest first, which is the order both the window and the CLI show."""
        with self._lock:
            entries = self._read()
        newest_first = list(reversed(entries))
        return newest_first[:limit] if limit else newest_first

    def stats(self) -> HistoryStats:
        with self._lock:
            entries = self._read()
        return summarize(entries)

    def clear(self) -> None:
        with self._lock:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as error:  # noqa: PERF203 - one path, one message
                logger.warning("Kon geschiedenis niet wissen (%s): %s", self.path, error)

    def _read(self) -> list[HistoryEntry]:
        if not self.path.exists():
            return []
        entries: list[HistoryEntry] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as error:
            logger.warning("Kon geschiedenis niet lezen (%s): %s", self.path, error)
            return []

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                if not isinstance(raw, dict):
                    continue
                entries.append(HistoryEntry.from_dict(raw))
            except (ValueError, TypeError):
                # A half-written line from a crash mid-append is not worth
                # losing the rest of the history over.
                continue
        return entries

    def _write(self, entries: list[HistoryEntry]) -> None:
        payload = "".join(
            json.dumps(entry.to_dict(), ensure_ascii=False) + "\n" for entry in entries
        )
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path.write_text(payload, encoding="utf-8")
            # Replace instead of truncate-and-write: a status window reading
            # while we rewrite then sees the old file, never a half-empty one.
            os.replace(temp_path, self.path)
        except OSError as error:
            logger.warning("Kon geschiedenis niet opslaan (%s): %s", self.path, error)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def summarize(entries: Iterable[HistoryEntry], today: date | None = None) -> HistoryStats:
    entries = list(entries)
    if not entries:
        return HistoryStats()

    day = today or date.today()
    of_today = [entry for entry in entries if entry.is_today(day)]
    timed = [entry for entry in entries if entry.transcribe_seconds > 0]
    return HistoryStats(
        total=len(entries),
        today=len(of_today),
        words_today=sum(entry.word_count for entry in of_today),
        words_total=sum(entry.word_count for entry in entries),
        speech_seconds_today=sum(entry.duration_seconds for entry in of_today),
        average_transcribe_seconds=(
            sum(entry.transcribe_seconds for entry in timed) / len(timed) if timed else 0.0
        ),
    )


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {rest:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"
