from __future__ import annotations

import faulthandler
import time
from pathlib import Path
from typing import TextIO

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

HANG_LOG_PATH = Path.home() / "Library" / "Logs" / "PraatTypen.hang.log"

# How long the main thread may go without a heartbeat before every thread's
# stack is written down. Well above anything a healthy run loop does (pasting
# holds still for about half a second), well below the point where you give up
# and force-quit the app.
HANG_SECONDS = 8.0
BEAT_SECONDS = 1.0

# Past this size the file is restarted: a hang report is only useful while it
# is recent, and a dump is a few kilobytes.
_MAX_BYTES = 512 * 1024


class HangWatch:
    """Write every thread's Python stack to a file when the main thread hangs.

    The app has frozen with the overlay on "Luistert" while the log simply
    stopped, and macOS took no sample of it. This leaves a record of where each
    thread was at that moment.

    The dump comes from faulthandler's own C thread, which does not need the
    GIL. That matters: when the freeze is a thread holding the GIL forever, no
    Python watchdog thread would get to run at all.
    """

    def __init__(self, file: TextIO, hang_seconds: float = HANG_SECONDS) -> None:
        self._file = file
        self.hang_seconds = hang_seconds

    def beat(self) -> None:
        """Called from the main run loop: re-arms the timer that dumps the stacks."""
        faulthandler.dump_traceback_later(
            self.hang_seconds, repeat=False, file=self._file, exit=False
        )

    def stop(self) -> None:
        faulthandler.cancel_dump_traceback_later()


def open_hang_log(path: Path = HANG_LOG_PATH) -> TextIO:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > _MAX_BYTES:
        path.unlink()
    file = path.open("a", encoding="utf-8")
    # faulthandler writes no timestamp of its own. A line per launch makes it
    # possible to tie a dump to the run in PraatTypen.log.
    file.write(f"\n=== PraatTypen gestart {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    file.flush()
    return file


def start_hang_watch(path: Path = HANG_LOG_PATH) -> HangWatch | None:
    """Arm the hang watch from the main thread, before the run loop starts.

    Optional on purpose: without AppKit or a writable log folder PraatTypen
    still has to dictate, just without this safety net.
    """
    try:
        from Foundation import NSRunLoop, NSRunLoopCommonModes, NSTimer
    except ImportError:
        return None

    try:
        file = open_hang_log(path)
    except OSError as error:
        logger.warning("Hang-log niet beschikbaar (%s): %s", path, error)
        return None

    # A hard crash (segfault in PortAudio or PyObjC) also lands here instead of
    # vanishing with the truncated PraatTypen.out.log of the next launch.
    faulthandler.enable(file=file, all_threads=True)

    watch = HangWatch(file)
    watch.beat()

    def tick(_timer) -> None:
        watch.beat()

    timer = NSTimer.timerWithTimeInterval_repeats_block_(BEAT_SECONDS, True, tick)
    # Common modes, so an open menu (event-tracking mode) still beats and is
    # not mistaken for a hang.
    NSRunLoop.mainRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
    watch._timer = timer  # type: ignore[attr-defined] - keep the timer alive
    logger.info("Hang-detectie actief: %s", path)
    return watch
