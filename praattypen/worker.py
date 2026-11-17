from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from functools import partial
from typing import Generic, TypeVar

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

_STOP = object()

Job = TypeVar("Job")


class TranscriptionWorker(Generic[Job]):
    """Processes recordings on a background thread.

    Keeps the pynput listener callback fast: a blocking transcription inside
    the callback stalls the macOS event tap and lags the whole keyboard.
    """

    def __init__(
        self,
        process_audio: Callable[[Job], object],
        on_error: Callable[[BaseException], None] | None = None,
    ) -> None:
        self.process_audio = process_audio
        self.on_error = on_error or _print_error
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, job: Job) -> None:
        self._queue.put(partial(self.process_audio, job))

    def submit_task(self, task: Callable[[], object]) -> None:
        """Queue an arbitrary callable, in order, on the same thread.

        Used for the model warm-up: running it here instead of on its own
        thread means a dictation started during warm-up waits for the load
        rather than racing it into a second one.
        """
        self._queue.put(task)

    def stop(self, timeout: float = 5.0) -> None:
        self._queue.put(_STOP)
        self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                item()  # type: ignore[operator]
            except BaseException as error:  # noqa: BLE001 - worker must survive
                self.on_error(error)
            finally:
                self._queue.task_done()


def _print_error(error: BaseException) -> None:
    logger.error("Fout bij transcriberen: %s", error)
