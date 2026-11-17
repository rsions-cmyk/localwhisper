import time
from pathlib import Path

from praattypen.worker import TranscriptionWorker


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for worker")


def test_worker_processes_submitted_paths_in_background() -> None:
    processed: list[Path] = []
    worker = TranscriptionWorker(processed.append)

    worker.submit(Path("een.wav"))
    worker.submit(Path("twee.wav"))
    wait_until(lambda: len(processed) == 2)
    worker.stop()

    assert processed == [Path("een.wav"), Path("twee.wav")]


def test_worker_runs_submitted_tasks_in_order_with_the_jobs() -> None:
    done: list[str] = []
    worker = TranscriptionWorker(lambda path: done.append(path.name))

    worker.submit_task(lambda: done.append("warm-up"))
    worker.submit(Path("een.wav"))
    wait_until(lambda: len(done) == 2)
    worker.stop()

    assert done == ["warm-up", "een.wav"]


def test_worker_survives_processing_errors() -> None:
    errors: list[BaseException] = []
    processed: list[Path] = []

    def flaky(path: Path) -> None:
        if path.name == "kapot.wav":
            raise RuntimeError("engine faalt")
        processed.append(path)

    worker = TranscriptionWorker(flaky, on_error=errors.append)
    worker.submit(Path("kapot.wav"))
    worker.submit(Path("goed.wav"))
    wait_until(lambda: len(processed) == 1 and len(errors) == 1)
    worker.stop()

    assert str(errors[0]) == "engine faalt"
    assert processed == [Path("goed.wav")]
