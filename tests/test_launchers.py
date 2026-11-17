from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cli_launcher_resolves_its_own_project_directory() -> None:
    launcher = ROOT / "scripts" / "praattypen"

    text = launcher.read_text(encoding="utf-8")

    # An absolute home path here is what broke when the checkout moved out of
    # the TCC-protected Documents folder.
    assert "/Users/" not in text
    assert 'APP_DIR="${PRAATTYPEN_HOME:-${0:A:h:h}}"' in text
    assert 'exec "$PYTHON" -m praattypen.main "$@"' in text


def test_app_bundle_builder_keeps_the_load_bearing_bits() -> None:
    builder = ROOT / "scripts" / "build_app.sh"

    text = builder.read_text(encoding="utf-8")

    assert "/Users/" not in text
    # No Dock icon, so PraatTypen never steals focus from the app being
    # dictated into.
    assert "<key>LSUIElement</key><true/>" in text
    # The interpreter copy is what makes macOS attribute permissions to
    # PraatTypen instead of to org.python.python.
    assert 'cp "$PY_STUB" "$CONTENTS/MacOS/praattypen-python"' in text


def test_double_click_launcher_calls_cli_launcher() -> None:
    launcher = ROOT / "PraatTypen.command"

    text = launcher.read_text(encoding="utf-8")

    assert '"$SCRIPT_DIR/scripts/praattypen" run' in text
    assert "Druk op Enter" in text
