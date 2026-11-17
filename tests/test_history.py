import json
import time
from datetime import date, datetime, timedelta

from praattypen.history import (
    HistoryEntry,
    HistoryStore,
    format_duration,
    summarize,
)


def make_entry(text: str = "hallo wereld", **kwargs) -> HistoryEntry:
    defaults = {
        "timestamp": time.time(),
        "duration_seconds": 2.0,
        "transcribe_seconds": 1.0,
        "engine": "local_whisper",
        "model": "whisper-turbo",
    }
    return HistoryEntry(text=text, **{**defaults, **kwargs})


def test_add_and_recent_returns_newest_first(tmp_path) -> None:
    store = HistoryStore(path=tmp_path / "history.jsonl")

    store.add(make_entry("eerste"))
    store.add(make_entry("tweede"))

    assert [entry.text for entry in store.recent()] == ["tweede", "eerste"]


def test_recent_respects_limit(tmp_path) -> None:
    store = HistoryStore(path=tmp_path / "history.jsonl")
    for index in range(15):
        store.add(make_entry(f"opname {index}"))

    assert len(store.recent(10)) == 10


def test_history_is_trimmed_to_max_entries(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path=path, max_entries=3)
    for index in range(6):
        store.add(make_entry(f"opname {index}"))

    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 3
    assert [entry.text for entry in store.recent()] == ["opname 5", "opname 4", "opname 3"]


def test_disabled_store_writes_nothing(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path=path, enabled=False)

    store.add(make_entry())

    assert not path.exists()
    assert store.recent() == []


def test_empty_transcripts_are_not_stored(tmp_path) -> None:
    store = HistoryStore(path=tmp_path / "history.jsonl")

    store.add(make_entry("   "))

    assert store.recent() == []


def test_corrupt_lines_are_skipped(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    good = json.dumps(make_entry("goed").to_dict())
    path.write_text(f"{good}\nniet-json\n{{\"half\": \n", encoding="utf-8")

    store = HistoryStore(path=path)

    assert [entry.text for entry in store.recent()] == ["goed"]


def test_entry_survives_a_write_read_round_trip(tmp_path) -> None:
    store = HistoryStore(path=tmp_path / "history.jsonl")
    original = make_entry("héél mooi", pasted=False)

    store.add(original)

    assert store.recent()[0] == original


def test_clear_removes_the_file(tmp_path) -> None:
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path=path)
    store.add(make_entry())

    store.clear()

    assert not path.exists()
    assert store.recent() == []


def test_missing_file_reads_as_empty(tmp_path) -> None:
    assert HistoryStore(path=tmp_path / "nope.jsonl").recent() == []


def test_summarize_counts_only_today_for_daily_totals() -> None:
    today = date(2026, 8, 19)
    now = datetime(2026, 8, 19, 10, 0).timestamp()
    yesterday = (datetime(2026, 8, 19, 10, 0) - timedelta(days=1)).timestamp()

    stats = summarize(
        [
            make_entry("een twee drie", timestamp=now, duration_seconds=3.0, transcribe_seconds=1.0),
            make_entry("vier vijf", timestamp=now, duration_seconds=2.0, transcribe_seconds=2.0),
            make_entry("gisteren", timestamp=yesterday, duration_seconds=9.0, transcribe_seconds=3.0),
        ],
        today=today,
    )

    assert stats.total == 3
    assert stats.today == 2
    assert stats.words_today == 5
    assert stats.words_total == 6
    assert stats.speech_seconds_today == 5.0
    assert stats.average_transcribe_seconds == 2.0


def test_summarize_of_nothing_is_all_zero() -> None:
    stats = summarize([])

    assert stats.total == 0
    assert stats.average_transcribe_seconds == 0.0


def test_preview_shortens_long_text() -> None:
    entry = make_entry("woord " * 40)

    assert len(entry.preview(30)) == 30
    assert entry.preview(30).endswith("…")


def test_preview_keeps_short_text_intact() -> None:
    assert make_entry("kort  en\nbondig").preview() == "kort en bondig"


def test_format_duration_scales_with_size() -> None:
    assert format_duration(4.25) == "4.2s"
    assert format_duration(95) == "1m 35s"
    assert format_duration(3725) == "1h 02m"
