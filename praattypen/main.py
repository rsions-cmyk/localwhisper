from __future__ import annotations

import argparse
import time
from pathlib import Path

from praattypen.appearance import apply_activation_policy
from praattypen.audio import AudioRecorder, Recording
from praattypen.cleanup import CleanupService
from praattypen.config import default_config_path, load_config
from praattypen.engines import create_engine
from praattypen.hangwatch import start_hang_watch
from praattypen.history import HistoryEntry, HistoryStore, format_duration
from praattypen.hotkey import PushToTalkController, QueuedController, listen_push_to_talk
from praattypen.inject import ClipboardInjector
from praattypen.logging_setup import get_logger, setup_logging
from praattypen.menubar import create_menu_bar
from praattypen.overlay import create_overlay
from praattypen.sounds import SoundCues
from praattypen.permissions import log_permissions
from praattypen.pipeline import DictationPipeline
from praattypen.statuswindow import create_status_window
from praattypen.vocabulary import Vocabulary
from praattypen.batch import BatchQueue, MeetingRecorder
from praattypen.models_catalog import set_config_model
from praattypen.modes import ClipboardWatch, ModeState, ModeTransformer
from praattypen.storage import auto_clean_cache, cache_plan, format_bytes, remove_paths
from praattypen.worker import TranscriptionWorker

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="praattypen",
        description="Local push-to-talk dictation for macOS.",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to config.yaml. Defaults to ~/.config/praattypen/config.yaml.",
    )

    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Start push-to-talk dictation.")
    run_parser.add_argument("--config", default=None, help="Path to config.yaml.")

    transcribe_parser = subparsers.add_parser(
        "transcribe",
        help="Transcribe one audio file and print the text without pasting.",
    )
    transcribe_parser.add_argument("audio_path")
    transcribe_parser.add_argument("--config", default=None, help="Path to config.yaml.")
    transcribe_parser.add_argument(
        "--paste",
        action="store_true",
        help="Paste the transcript at the current cursor after transcribing.",
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Toon de laatste opnames en waar PraatTypen op staat.",
    )
    history_parser.add_argument("--config", default=None, help="Path to config.yaml.")
    history_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Hoeveel opnames tonen (standaard 10).",
    )
    history_parser.add_argument(
        "--full",
        action="store_true",
        help="Toon de volledige transcripten in plaats van een samenvatting per regel.",
    )

    subparsers.add_parser("benchmark", help="Speed-test every installed speech model.")
    cache_parser = subparsers.add_parser("cache", help="Show local model and temp-audio cache usage.")
    cache_parser.add_argument("--config", default=None, help="Path to config.yaml.")

    clean_parser = subparsers.add_parser(
        "cache-clean",
        help="Remove unused PraatTypen model caches and temporary audio.",
    )
    clean_parser.add_argument("--config", default=None, help="Path to config.yaml.")
    clean_parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete files. Without this flag, only shows what would be removed.",
    )

    parser.set_defaults(command="run")
    return parser


def build_pipeline(config_path: str | Path, paste: bool = True) -> DictationPipeline:
    config = load_config(config_path)
    engine = create_engine(config)
    cleanup = CleanupService(config.cleanup)
    injector = ClipboardInjector()
    inject = injector.inject if paste else lambda _text: None
    return DictationPipeline(engine=engine, cleanup=cleanup, inject=inject, on_status=_status)


def run(config_path: str | Path) -> None:
    log_path = setup_logging()
    config = load_config(config_path)
    log_permissions()
    maybe_auto_clean_cache(config)

    vocabulary = Vocabulary()
    modes = ModeState()
    transformer = ModeTransformer(modes, model=config.assistant_model)
    engine = create_engine(
        config, prompt_source=vocabulary.prompt, task_source=modes.whisper_task
    )
    injector = ClipboardInjector()
    clipboard = ClipboardWatch()
    transformer.clipboard_fresh = clipboard.fresh
    # A voice command was heard: the bar says what it is doing now.
    transformer.on_command = lambda working: overlay.show_processing(working)

    def paste_text(text: str):
        # Our own clipboard writes must not look like "you just copied this".
        clipboard.begin_own()
        try:
            return injector.inject(text)
        finally:
            clipboard.end_own()

    history = HistoryStore(enabled=config.history_enabled, max_entries=config.history_size)
    # Before any window or status item exists, so nothing else has already
    # pinned the app to "no Dock icon".
    apply_activation_policy(config.dock_icon)
    recorder = AudioRecorder(
        sample_rate=config.sample_rate,
        min_duration_seconds=config.min_speech_seconds,
        min_rms=config.min_speech_rms,
    )
    sounds = SoundCues(enabled=config.sounds_enabled, volume=config.sound_volume)
    overlay = create_overlay(
        enabled=config.overlay_enabled, level_source=lambda: recorder.level
    )
    status_window = create_status_window(
        enabled=config.status_window_enabled,
        config=config,
        engine_name=engine.name,
        model=getattr(engine, "model", ""),
        history=history,
        config_path=Path(config_path),
        log_path=log_path,
        repaste=paste_text,
        vocabulary=vocabulary,
        modes=modes,
        switch_model=lambda value: switch_model(value),
        toggle_dictation=lambda: toggle_dictation(),
    )
    menu_bar = create_menu_bar(
        enabled=config.menu_bar_enabled,
        engine_name=engine.name,
        config_path=Path(config_path),
        log_path=log_path,
        repaste=paste_text,
        show_window=status_window.show,
        modes=modes,
    )
    pipeline = DictationPipeline(
        engine=engine,
        cleanup=CleanupService(config.cleanup),
        inject=paste_text,
        on_status=_status,
        fix_casing=vocabulary.apply_casing,
        transform=transformer.transform,
    )

    def idle() -> None:
        overlay.hide()
        menu_bar.set_idle()
        status_window.set_idle()

    def process_recording(recording: Recording) -> None:
        started = time.monotonic()
        try:
            text = pipeline.process_recording(recording)
            if text:
                overlay.show_done(
                    pipeline.last_pasted,
                    note="Plain text · AI off" if transformer.last_failed else "",
                    label=transformer.last_command,
                )
                sounds.done()
                menu_bar.set_last_transcript(text)
                history.add(
                    HistoryEntry(
                        text=text,
                        duration_seconds=recording.duration_seconds,
                        transcribe_seconds=time.monotonic() - started,
                        engine=engine.name,
                        model=getattr(engine, "model", ""),
                        pasted=pipeline.last_pasted,
                    )
                )
                status_window.refresh()
        finally:
            idle()

    def on_stopped(recording: Recording) -> None:
        _status(f"Opname gestopt ({recording.duration_seconds:.1f}s), in de wachtrij.")
        sounds.stop()
        overlay.show_processing(modes.current.working)
        menu_bar.set_processing()
        status_window.set_processing()

    def on_started() -> None:
        _status("Opname gestart.")
        sounds.start()
        transformer.capture()
        overlay.show_listening()
        menu_bar.set_listening()
        status_window.set_listening()

    def on_hands_free() -> None:
        _status("Handsfree-opname: tik de sneltoets opnieuw om te stoppen.")
        sounds.hands_free()
        overlay.show_hands_free()

    def on_skipped() -> None:
        _status("Opname overgeslagen: te kort of te stil. Houd Fn langer ingedrukt en praat duidelijk.")
        idle()

    def on_failed(error: BaseException) -> None:
        _status(f"Opname mislukt: {error}")
        sounds.error()
        idle()

    batch = BatchQueue(
        model=getattr(engine, "model", config.models.whisper),
        allowed_languages=(
            [] if config.language == "auto"
            else [part.strip() for part in config.language.split(",") if part.strip()]
        ),
        prompt_source=vocabulary.prompt,
        fix_casing=vocabulary.apply_casing,
        on_update=status_window.update_job,
    )
    status_window.attach_batch(batch, MeetingRecorder(), on_finished=sounds.done)

    def switch_model(value: str) -> None:
        """Models tab: use another speech model from now on."""
        set_config_model(Path(config_path), value)
        engine.model = value
        batch.model = value
        _status(f"Spraakmodel gewisseld naar {value}.")
        worker.submit_task(engine.warm_up)

    worker = TranscriptionWorker(process_recording, on_error=lambda error: _status(f"Fout: {error}"))
    # Load the model now, on the worker thread, so the first dictation of the
    # session does not have to wait for it.
    worker.submit_task(pipeline.engine.warm_up)
    controller = PushToTalkController(
        recorder,
        worker.submit,
        on_started=on_started,
        on_stopped=on_stopped,
        on_skipped=on_skipped,
        on_failed=on_failed,
        max_recording_seconds=config.max_recording_seconds,
        on_hands_free=on_hands_free,
    )

    queued = QueuedController(controller)
    overlay.set_click_handler(queued.stop)
    overlay.set_mode_source(lambda: modes.current)

    def toggle_dictation() -> None:
        """Window button (main thread): start or stop a hands-free dictation.

        AirQuote steps aside first, so the text lands in the app you were
        using, not in the AirQuote window you just clicked.
        """
        from PyObjCTools import AppHelper
        import AppKit

        AppKit.NSApplication.sharedApplication().hide_(None)
        if controller.recording:
            AppHelper.callLater(0.35, queued.stop)
        else:
            AppHelper.callLater(0.35, queued.start_hands_free)

    hang_watch = start_hang_watch()

    _status(
        f"PraatTypen luistert. Houd {config.hotkey} ingedrukt om te praten; "
        "laat los om te transcriberen en plakken. Stoppen kan via de menubalk of Ctrl+C."
    )
    try:
        listen_push_to_talk(config.hotkey, queued)
    except KeyboardInterrupt:
        _status("Gestopt.")
    finally:
        if hang_watch is not None:
            hang_watch.stop()
        worker.stop()


def transcribe(audio_path: str | Path, config_path: str | Path, paste: bool = False) -> str:
    pipeline = build_pipeline(config_path, paste=paste)
    text = pipeline.process_audio_file(Path(audio_path))
    if not paste:
        print(text)
    return text


def show_history(config_path: str | Path, limit: int = 10, full: bool = False) -> None:
    config = load_config(config_path)
    engine_name = "cloud_groq" if config.cloud_boost else config.engine
    models = {
        "local_whisper": config.models.whisper,
        "local_parakeet": config.models.parakeet,
        "cloud_groq": config.models.groq,
    }
    store = HistoryStore(enabled=config.history_enabled, max_entries=config.history_size)
    entries = store.recent(limit)
    stats = store.stats()

    print(f"Engine:      {engine_name}")
    print(f"Model:       {models.get(engine_name, 'onbekend')}")
    print(f"Taal:        {config.language}   Sneltoets: {config.hotkey}")
    print(
        "Opschoning:  "
        + (
            f"aan ({config.cleanup.provider}: {config.cleanup.model})"
            if config.cleanup.enabled
            else "uit"
        )
    )
    print(
        f"Vandaag:     {stats.today} opnames, {stats.words_today} woorden, "
        f"{format_duration(stats.speech_seconds_today)} spraak, "
        f"gemiddeld {stats.average_transcribe_seconds:.1f}s transcriberen"
    )
    print(f"Bewaard:     {stats.total} opnames in {store.path}")
    print()

    if not entries:
        print("Nog geen opnames bewaard.")
        return

    print(f"Laatste {len(entries)} opnames (nieuwste eerst):")
    for entry in entries:
        flag = "" if entry.pasted else "  [niet geplakt]"
        head = (
            f"  {entry.local_day} {entry.local_time}  {entry.duration_seconds:5.1f}s  "
            f"{entry.word_count:3d}w"
        )
        print(f"{head}  {entry.text if full else entry.preview(70)}{flag}")


def show_cache(config_path: str | Path) -> None:
    plan = cache_plan(load_config(config_path))
    _print_cache_plan(plan)


def clean_cache(config_path: str | Path, yes: bool = False) -> None:
    plan = cache_plan(load_config(config_path))
    _print_cache_plan(plan)
    targets = plan.remove + plan.temp_audio
    if not targets:
        print("Niets om op te ruimen.")
        return
    if not yes:
        print("Dry-run: voeg --yes toe om dit echt te verwijderen.")
        return
    removed = remove_paths(targets)
    print(f"Opgeruimd: {removed} pad(en).")


def _print_cache_plan(plan) -> None:
    print("Bewaarde modelcaches:")
    if plan.keep:
        for path in plan.keep:
            print(f"  KEEP   {path.name} ({format_bytes(_path_size(path))})")
    else:
        print("  Geen huidige lokale modelcache gevonden.")

    print("Opruimbare modelcaches:")
    if plan.remove:
        for path in plan.remove:
            print(f"  REMOVE {path.name} ({format_bytes(_path_size(path))})")
    else:
        print("  Geen oude Whisper/Parakeet caches gevonden.")

    print(f"Vrij te maken modelruimte: {format_bytes(plan.reclaimable_bytes)}")
    print(f"PraatTypen tijdelijke audio: {len(plan.temp_audio)} bestand(en), {format_bytes(plan.temp_audio_bytes)}")


def _path_size(path: Path) -> int:
    from praattypen.storage import dir_size

    return dir_size(path)


def run_benchmark(config_path) -> int:
    from praattypen import benchmark
    from praattypen import models_catalog as catalog

    config = load_config(config_path)
    models = [
        (model.repo, str(path))
        for model in catalog.CATALOG
        if (path := catalog.local_path(model.repo)) is not None
    ]
    results = benchmark.run(
        models, restore=catalog.resolve_whisper_model(config.models.whisper), on_progress=print
    )
    for repo, _ in models:
        print(f"{repo}\n  {benchmark.summary(results[repo])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config) if args.config else default_config_path()

    if args.command == "transcribe":
        transcribe(args.audio_path, config_path, paste=args.paste)
        return 0
    if args.command == "history":
        show_history(config_path, limit=args.limit, full=args.full)
        return 0
    if args.command == "benchmark":
        return run_benchmark(config_path)
    if args.command == "cache":
        show_cache(config_path)
        return 0
    if args.command == "cache-clean":
        clean_cache(config_path, yes=args.yes)
        return 0

    run(config_path)
    return 0


def _status(message: str) -> None:
    logger.info(message)


def maybe_auto_clean_cache(config) -> None:
    if not config.auto_cache_clean:
        return
    max_bytes = int(config.max_model_cache_gb * 1024 * 1024 * 1024)
    result = auto_clean_cache(config, max_bytes=max_bytes)
    if result.removed_paths:
        _status(
            f"Cache automatisch opgeruimd: {result.removed_paths} pad(en), "
            f"{format_bytes(result.reclaimed_bytes)} vrijgemaakt."
        )


if __name__ == "__main__":
    raise SystemExit(main())
