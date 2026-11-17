from praattypen.main import build_parser


def test_build_parser_accepts_transcribe_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["transcribe", "audio.wav", "--config", "config.yaml"])

    assert args.command == "transcribe"
    assert args.audio_path == "audio.wav"
    assert args.config == "config.yaml"


def test_build_parser_defaults_to_run_command() -> None:
    parser = build_parser()

    args = parser.parse_args([])

    assert args.command == "run"


def test_build_parser_accepts_cache_commands() -> None:
    parser = build_parser()

    cache_args = parser.parse_args(["cache", "--config", "config.yaml"])
    clean_args = parser.parse_args(["cache-clean", "--yes"])

    assert cache_args.command == "cache"
    assert cache_args.config == "config.yaml"
    assert clean_args.command == "cache-clean"
    assert clean_args.yes is True


def test_build_parser_accepts_history_command() -> None:
    parser = build_parser()

    args = parser.parse_args(["history", "--limit", "5", "--full"])

    assert args.command == "history"
    assert args.limit == 5
    assert args.full is True


def test_history_command_defaults_to_ten_entries() -> None:
    parser = build_parser()

    args = parser.parse_args(["history"])

    assert args.limit == 10
    assert args.full is False
