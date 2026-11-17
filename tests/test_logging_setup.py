import logging

from praattypen.logging_setup import get_logger, setup_logging


def test_main_module_logger_stays_inside_the_praattypen_tree() -> None:
    """`python -m praattypen.main` names that module "__main__".

    A "__main__" logger propagates to the root logger instead of to our
    handlers, which silently dropped every status line when PraatTypen ran from
    the app bundle -- the one place the log file is the only output there is.
    """
    assert get_logger("__main__").name == "praattypen"
    assert get_logger("praattypen.hotkey").name == "praattypen.hotkey"
    assert get_logger("menubar").name == "praattypen.menubar"


def test_status_messages_reach_the_log_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging.getLogger("praattypen"), "handlers", [])
    log_path = tmp_path / "PraatTypen.log"

    assert setup_logging(log_path) == log_path
    get_logger("__main__").info("opname gestart")
    for handler in logging.getLogger("praattypen").handlers:
        handler.flush()

    assert "opname gestart" in log_path.read_text(encoding="utf-8")


def test_setup_logging_survives_an_unwritable_log_path(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(logging.getLogger("praattypen"), "handlers", [])
    blocked = tmp_path / "bestaand-bestand" / "PraatTypen.log"
    blocked.parent.write_text("ik ben geen map", encoding="utf-8")

    assert setup_logging(blocked) is None
    assert logging.getLogger("praattypen").handlers, "stderr moet overblijven"
