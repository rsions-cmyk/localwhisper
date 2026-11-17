from __future__ import annotations

import threading
import time

from praattypen.hangwatch import HangWatch, open_hang_log


def test_hang_watch_dumps_all_threads_when_main_stops_beating(tmp_path) -> None:
    path = tmp_path / "hang.log"
    file = open_hang_log(path)
    watch = HangWatch(file, hang_seconds=0.2)

    stuck = threading.Event()

    def holder() -> None:
        stuck.wait(2.0)

    thread = threading.Thread(target=holder, name="audio-callback-standin")
    thread.start()
    try:
        watch.beat()
        time.sleep(0.6)  # no beats: the "main thread" is hung
    finally:
        stuck.set()
        thread.join()
        watch.stop()
        file.close()

    report = path.read_text(encoding="utf-8")
    assert "PraatTypen gestart" in report
    assert "Timeout" in report
    assert "holder" in report  # the other thread's stack is in there too


def test_hang_watch_stays_quiet_while_beating(tmp_path) -> None:
    path = tmp_path / "hang.log"
    file = open_hang_log(path)
    watch = HangWatch(file, hang_seconds=0.3)
    try:
        for _ in range(6):
            watch.beat()
            time.sleep(0.1)
    finally:
        watch.stop()
        file.close()

    assert "Timeout" not in path.read_text(encoding="utf-8")


def test_hang_log_restarts_when_too_big(tmp_path) -> None:
    path = tmp_path / "hang.log"
    path.write_text("x" * (600 * 1024), encoding="utf-8")
    open_hang_log(path).close()
    assert path.stat().st_size < 1024
