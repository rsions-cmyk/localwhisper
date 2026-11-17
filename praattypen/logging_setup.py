from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path.home() / "Library" / "Logs" / "PraatTypen.log"

_ROOT = "praattypen"

# Enough history to explain a bad dictation from this morning, small enough to
# ignore forever.
_MAX_BYTES = 1024 * 1024
_BACKUP_COUNT = 2


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger inside the praattypen tree.

    Callers pass ``__name__``, which is ``"__main__"`` for the module started
    by ``python -m praattypen.main`` -- a name outside our tree, so its records
    would propagate to the root logger and be dropped instead of reaching the
    log file. Anchoring every name here keeps that from happening silently.
    """
    if not name or name == "__main__":
        return logging.getLogger(_ROOT)
    if name == _ROOT or name.startswith(f"{_ROOT}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT}.{name}")


def setup_logging(log_path: Path | None = LOG_PATH) -> Path | None:
    """Send status and errors to stderr and, when possible, to a log file.

    Launched from an app bundle there is no terminal to print to, so the file
    is the only record of what PraatTypen did. A file that cannot be opened
    must never stop the app from running: stderr alone is then good enough.
    """
    logger = logging.getLogger(_ROOT)
    if logger.handlers:
        return getattr(logger, "_praattypen_log_path", None)

    logger.setLevel(logging.INFO)
    logger.propagate = False

    # From an app bundle stderr is a redirect file, not a terminal, and the
    # rotating log below already has everything: echoing there too would just
    # write every line twice into two different files.
    if sys.stderr is not None and sys.stderr.isatty():
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(stream)

    written_to: Path | None = None
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
            written_to = log_path
        except OSError as error:
            logger.warning("Kon logbestand niet openen (%s): %s", log_path, error)

    if not logger.handlers:
        # No terminal and no writable log file: stderr is still better than
        # swallowing every message.
        logger.addHandler(logging.StreamHandler(sys.stderr))

    logger._praattypen_log_path = written_to  # type: ignore[attr-defined]
    return written_to
