"""The Recordings and Models tabs of the AirQuote window.

Kept apart from statuswindow.py, which owns the window and the small AppKit
helpers these tabs borrow (``w._label``, ``w._button``, ``w._card`` ...).
All methods run on the main thread unless they say otherwise.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from praattypen import models_catalog as catalog
from praattypen import ui
from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

_DROP_CLASS = None


def _drop_zone_class():
    """An NSView that accepts dropped files and draws a dashed outline."""
    global _DROP_CLASS
    if _DROP_CLASS is not None:
        return _DROP_CLASS

    import AppKit
    import objc
    from Quartz import CAShapeLayer, CGPathCreateWithRoundedRect

    class _AirQuoteDropZone(AppKit.NSView):
        def initWithHandler_(self, handler):
            self = objc.super(_AirQuoteDropZone, self).init()
            if self is None:
                return None
            self.handler = handler
            self.setWantsLayer_(True)
            outline = CAShapeLayer.layer()
            outline.setFillColor_(None)
            outline.setLineWidth_(1.5)
            outline.setLineDashPattern_([6, 5])
            self.layer().addSublayer_(outline)
            self.outline = outline
            self.hovering = False
            self._paint()
            self.registerForDraggedTypes_([AppKit.NSPasteboardTypeFileURL])
            return self

        def _paint(self):
            color = ui.LOGO_SWEEP[1] if self.hovering else (0.55, 0.55, 0.6)
            self.outline.setStrokeColor_(ui.cg(color, 0.95 if self.hovering else 0.55))
            self.layer().setBackgroundColor_(ui.cg(ui.LOGO_SWEEP[1], 0.08 if self.hovering else 0.0))
            self.layer().setCornerRadius_(14)

        def layout(self):
            objc.super(_AirQuoteDropZone, self).layout()
            bounds = self.bounds()
            inset = bounds.origin.x + 1, bounds.origin.y + 1
            rect = ((inset[0], inset[1]), (bounds.size.width - 2, bounds.size.height - 2))
            self.outline.setFrame_(bounds)
            self.outline.setPath_(CGPathCreateWithRoundedRect(rect, 14, 14, None))

        def draggingEntered_(self, sender):
            self.hovering = True
            self._paint()
            return AppKit.NSDragOperationCopy

        def draggingExited_(self, sender):
            self.hovering = False
            self._paint()

        def performDragOperation_(self, sender):
            self.hovering = False
            self._paint()
            urls = sender.draggingPasteboard().readObjectsForClasses_options_(
                [AppKit.NSURL], {AppKit.NSPasteboardURLReadingFileURLsOnlyKey: True}
            )
            paths = [Path(str(url.path())) for url in (urls or [])]
            try:
                self.handler(paths)
            except Exception as error:  # noqa: BLE001
                logger.error("Bestand neerzetten mislukt: %s", error)
            return True

    _DROP_CLASS = _AirQuoteDropZone
    return _DROP_CLASS


class RecordingsTab:
    """Drop, choose or record audio; watch the queue; open the transcripts."""

    def __init__(self, w) -> None:
        self.w = w
        self.batch = None
        self.meeting = None
        self.on_finished: Callable[[], None] = lambda: None
        self._job_views: dict[str, dict] = {}
        self._timer = None
        self.view = self._build()

    def attach(self, batch, meeting, on_finished: Callable[[], None] | None = None) -> None:
        self.batch = batch
        self.meeting = meeting
        if on_finished is not None:
            self.on_finished = on_finished

    # --- building -------------------------------------------------------

    def _build(self):
        w = self.w
        AppKit = w._AppKit

        zone = _drop_zone_class().alloc().initWithHandler_(self._add_paths)
        icon = w_image("waveform.badge.plus", 30, AppKit.NSColor.secondaryLabelColor())
        self._zone_title = w._label(
            "Drop an interview, meeting or lecture here", size=15, weight=AppKit.NSFontWeightSemibold
        )
        self._zone_hint = w._label(
            "Any audio or video file, any length. Transcribed on this Mac.", size=12, secondary=True
        )
        self._choose = w._button("Choose File…", self._choose_file, symbol="folder")
        self._record = w._button("Record", self._toggle_record, symbol="record.circle")
        self._record.setContentTintColor_(AppKit.NSColor.systemRedColor())
        self._speakers = AppKit.NSButton.checkboxWithTitle_target_action_(
            "Tell speakers apart", None, None
        )
        self._speakers.setState_(1)
        self._level = AppKit.NSLevelIndicator.alloc().init()
        self._level.setLevelIndicatorStyle_(AppKit.NSLevelIndicatorStyleContinuousCapacity)
        self._level.setMinValue_(0)
        self._level.setMaxValue_(1)
        self._level.setWarningValue_(0.85)
        self._level.setCriticalValue_(0.95)
        self._level.setHidden_(True)
        _fix_width(self._level, 160)
        buttons = w._hstack([self._choose, self._record, self._speakers], spacing=10)
        stack = w._vstack([icon, self._zone_title, self._zone_hint, self._level, buttons], spacing=8)
        stack.setAlignment_(AppKit.NSLayoutAttributeCenterX)
        stack.setCustomSpacing_afterView_(14, self._zone_hint)
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        zone.addSubview_(stack)

        self._jobs = w._vstack([], spacing=8)
        self._jobs_title = w._section_title("Transcripts")
        self._jobs_title.setHidden_(True)

        container = AppKit.NSView.alloc().init()
        for view in (zone, self._jobs_title, self._jobs):
            view.setTranslatesAutoresizingMaskIntoConstraints_(False)
            container.addSubview_(view)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                zone.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                zone.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                zone.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
                zone.heightAnchor().constraintEqualToConstant_(196),
                stack.centerXAnchor().constraintEqualToAnchor_(zone.centerXAnchor()),
                stack.centerYAnchor().constraintEqualToAnchor_(zone.centerYAnchor()),
                self._jobs_title.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                self._jobs_title.topAnchor().constraintEqualToAnchor_constant_(zone.bottomAnchor(), 18),
                self._jobs.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                self._jobs.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                self._jobs.topAnchor().constraintEqualToAnchor_constant_(
                    self._jobs_title.bottomAnchor(), 8
                ),
            ]
        )
        return container

    # --- actions --------------------------------------------------------

    def _add_paths(self, paths: list[Path]) -> None:
        if self.batch is None:
            return
        accepted = [p for p in paths if p.suffix.lower().lstrip(".") in catalog_audio_extensions()]
        for path in accepted:
            self.batch.add(path, speakers=bool(self._speakers.state()))
        if paths and not accepted:
            self._zone_hint.setStringValue_("That doesn't look like an audio or video file.")

    def _choose_file(self) -> None:
        AppKit = self.w._AppKit
        panel = AppKit.NSOpenPanel.openPanel()
        panel.setAllowsMultipleSelection_(True)
        panel.setCanChooseDirectories_(False)
        panel.setPrompt_("Transcribe")
        panel.setMessage_("Choose one or more recordings to transcribe")
        try:
            panel.setAllowedFileTypes_(sorted(catalog_audio_extensions()))
        except Exception:  # noqa: BLE001 - deprecated API; no filter is fine too
            pass
        if panel.runModal() == AppKit.NSModalResponseOK:
            self._add_paths([Path(str(url.path())) for url in panel.URLs()])

    def _toggle_record(self) -> None:
        if self.meeting is None:
            return
        AppKit = self.w._AppKit
        if not self.meeting.recording:
            try:
                self.meeting.start()
            except Exception as error:  # noqa: BLE001
                self._zone_hint.setStringValue_(f"Could not start recording: {error}")
                return
            self._record.setTitle_("Stop & Transcribe")
            self._record.setImage_(_symbol("stop.circle.fill", 12))
            self._choose.setEnabled_(False)
            self._level.setHidden_(False)
            self._zone_hint.setStringValue_(f"Saving to {self.meeting.path}")
            self._start_timer()
        else:
            path = self.meeting.stop()
            self._stop_timer()
            self._record.setTitle_("Record")
            self._record.setImage_(_symbol("record.circle", 12))
            self._record.setContentTintColor_(AppKit.NSColor.systemRedColor())
            self._choose.setEnabled_(True)
            self._level.setHidden_(True)
            self._zone_title.setStringValue_("Drop an interview, meeting or lecture here")
            self._zone_hint.setStringValue_("Any audio or video file, any length. Transcribed on this Mac.")
            if path is not None:
                self.batch.add(path, speakers=bool(self._speakers.state()))

    def _start_timer(self) -> None:
        import Foundation

        def tick(_timer) -> None:
            if self.meeting is None or not self.meeting.recording:
                return
            seconds = int(self.meeting.elapsed())
            self._zone_title.setStringValue_(
                f"● Recording  {seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
            )
            self._level.setDoubleValue_(ui.level_to_unit(self.meeting.level))

        self._timer = Foundation.NSTimer.timerWithTimeInterval_repeats_block_(0.1, True, tick)
        Foundation.NSRunLoop.mainRunLoop().addTimer_forMode_(self._timer, Foundation.NSRunLoopCommonModes)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.invalidate()
            self._timer = None

    # --- job list -------------------------------------------------------

    def update_job(self, job) -> None:
        w = self.w
        AppKit = w._AppKit
        views = self._job_views.get(job.id)
        if views is None:
            views = self._job_row(job)
            self._job_views[job.id] = views
            self._jobs.insertArrangedSubview_atIndex_(views["row"], 0)
            AppKit.NSLayoutConstraint.activateConstraints_(
                [views["row"].widthAnchor().constraintEqualToAnchor_(self._jobs.widthAnchor())]
            )
            self._jobs_title.setHidden_(False)
        views["status"].setStringValue_(job.status)
        bar = views["bar"]
        if job.state in ("done", "failed"):
            bar.stopAnimation_(None)
            bar.setHidden_(True)
        elif job.progress < 0:
            bar.setHidden_(False)
            bar.setIndeterminate_(True)
            bar.startAnimation_(None)
        else:
            bar.setHidden_(False)
            bar.stopAnimation_(None)
            bar.setIndeterminate_(False)
            bar.setDoubleValue_(job.progress * 100)
        done = job.state == "done" and job.output is not None
        views["open"].setHidden_(not done)
        views["reveal"].setHidden_(not done)
        icon = {"done": "checkmark.circle.fill", "failed": "exclamationmark.triangle.fill"}.get(
            job.state, "waveform"
        )
        tint = {
            "done": AppKit.NSColor.systemGreenColor(),
            "failed": AppKit.NSColor.systemOrangeColor(),
        }.get(job.state, AppKit.NSColor.secondaryLabelColor())
        views["icon"].setImage_(_symbol(icon, 15))
        views["icon"].setContentTintColor_(tint)
        if job.state == "done" and not views.get("announced"):
            views["announced"] = True
            self.on_finished()

    def _job_row(self, job) -> dict:
        w = self.w
        AppKit = w._AppKit
        icon = w_image("waveform", 15, AppKit.NSColor.secondaryLabelColor())
        _fix_width(icon, 20)
        name = w._label(job.name, size=13, weight=AppKit.NSFontWeightMedium)
        name.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        status = w._label(job.status, size=11, secondary=True)
        text = w._vstack([name, status], spacing=2)
        bar = AppKit.NSProgressIndicator.alloc().init()
        bar.setStyle_(AppKit.NSProgressIndicatorStyleBar)
        bar.setMinValue_(0)
        bar.setMaxValue_(100)
        bar.setControlSize_(AppKit.NSControlSizeSmall)
        _fix_width(bar, 120)
        target = w._target
        open_button = w._button(
            "Open", lambda: job.output and w._open(job.output), symbol="doc.text", small=True, target=target
        )
        reveal = w._button("", lambda: job.output and _reveal(job.output), symbol="folder", small=True, target=target)
        reveal.setToolTip_("Show in Finder")
        row = w._hstack([icon, text, w._spacer(), bar, open_button, reveal], spacing=10)
        return {
            "row": w._card(row, padding=12, radius=10, alpha=0.045),
            "status": status,
            "bar": bar,
            "open": open_button,
            "reveal": reveal,
            "icon": icon,
        }


class ModelsTab:
    """This Mac, the models that fit it, and switching between them."""

    def __init__(self, w, switch_model: Callable[[str], None] | None = None) -> None:
        self.w = w
        self.switch_model = switch_model
        self.info = catalog.system_info()
        self.recommended = catalog.recommend(self.info)
        self._rows: dict[str, dict] = {}
        self._downloading: set[str] = set()
        from praattypen.benchmark import load_results

        self._bench_results = load_results()
        self._benchmarking = False
        self.current = ""
        self.view = self._build()

    def _build(self):
        w = self.w
        AppKit = w._AppKit
        chip = w_image("cpu", 14, AppKit.NSColor.secondaryLabelColor())
        mac = w._label(self.info.summary(), size=13, weight=AppKit.NSFontWeightMedium)
        mac_row = w._hstack([chip, mac], spacing=8)
        explain = w._label(
            "Pick the speech model AirQuote uses for dictation and recordings. "
            "The recommendation is based on your chip and memory; speed is measured "
            "from your own dictations once you have used a model a few times.",
            size=12,
            secondary=True,
            wrap=True,
        )
        self._bench_button = w._button("Run Speed Test", self._run_benchmark, symbol="gauge.with.dots.needle.67percent")
        self._bench_status = w._label(
            "Tests every installed model on the same Dutch and English clip. "
            "Runs only when you click; dictation keeps working meanwhile.",
            size=11,
            tertiary=True,
            wrap=True,
        )
        self._bench_status.setPreferredMaxLayoutWidth_(420)
        bench_row = w._hstack([self._bench_status, w._spacer(), self._bench_button], spacing=10)
        rows = []
        for model in catalog.CATALOG:
            views = self._row(model)
            self._rows[model.repo] = views
            rows.append(views["card"])
        stack = w._vstack([mac_row, explain] + rows + [bench_row], spacing=10)
        stack.setCustomSpacing_afterView_(16, explain)
        rows = rows + [bench_row]
        container = AppKit.NSView.alloc().init()
        stack.setTranslatesAutoresizingMaskIntoConstraints_(False)
        container.addSubview_(stack)
        constraints = [
            stack.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
            stack.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
            stack.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
            explain.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()),
        ]
        constraints += [card.widthAnchor().constraintEqualToAnchor_(stack.widthAnchor()) for card in rows]
        AppKit.NSLayoutConstraint.activateConstraints_(constraints)
        return container

    def _row(self, model: catalog.SpeechModel) -> dict:
        w = self.w
        AppKit = w._AppKit
        title = w._label(model.title, size=14, weight=AppKit.NSFontWeightSemibold)
        badge = w._label("", size=11, weight=AppKit.NSFontWeightSemibold)
        title_row = w._hstack([title, badge], spacing=8)
        blurb = w._label(model.blurb, size=12, secondary=True, wrap=True)
        blurb.setPreferredMaxLayoutWidth_(400)
        facts = w._label("", size=11, tertiary=True)
        text = w._vstack([title_row, blurb, facts], spacing=4)
        text.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        action = w._button("Use", lambda repo=model.repo: self._use(repo), small=False)
        spinner = AppKit.NSProgressIndicator.alloc().init()
        spinner.setStyle_(AppKit.NSProgressIndicatorStyleSpinning)
        spinner.setControlSize_(AppKit.NSControlSizeSmall)
        spinner.setDisplayedWhenStopped_(False)
        row = w._hstack([text, w._spacer(), spinner, action], spacing=10)
        return {
            "card": w._card(row, padding=14, radius=12, alpha=0.05),
            "badge": badge,
            "facts": facts,
            "action": action,
            "spinner": spinner,
            "model": model,
        }

    def refresh(self, current: str, history_entries) -> None:
        AppKit = self.w._AppKit
        self.current = current
        for repo, views in self._rows.items():
            model = views["model"]
            in_use = catalog.same_model(current, repo)
            installed = catalog.local_path(repo) is not None
            badges = []
            if repo == self.recommended:
                badges.append("RECOMMENDED FOR YOUR MAC")
            views["badge"].setStringValue_("  ·  ".join(badges))
            views["badge"].setTextColor_(ui.rgb(ui.LOGO_SWEEP[1]))
            speed = catalog.measured_speed(history_entries, repo)
            bench = self._bench_results.get(repo)
            parts = [
                "●" * model.quality + "○" * (5 - model.quality) + " accuracy",
                f"{model.download_gb:.1f} GB download" if not installed else "Installed",
                f"~{model.memory_gb:.0f} GB memory",
                f"{speed:.2f} s per second of speech (measured)" if speed else "speed not measured yet",
            ]
            note = catalog.fit_note(model, self.info)
            if note:
                parts.append(note)
            text = "  ·  ".join(parts)
            if bench is not None:
                from praattypen.benchmark import summary

                text += "\n" + summary(bench)
            views["facts"].setStringValue_(text)
            action = views["action"]
            if repo in self._downloading:
                action.setTitle_("Downloading…")
                action.setEnabled_(False)
                views["spinner"].startAnimation_(None)
                continue
            views["spinner"].stopAnimation_(None)
            if in_use:
                action.setTitle_("In Use")
                action.setImage_(_symbol("checkmark", 11))
                action.setImagePosition_(AppKit.NSImageLeading)
                action.setEnabled_(False)
            else:
                action.setImage_(None)
                action.setTitle_("Use" if installed else f"Download {model.download_gb:.1f} GB")
                action.setEnabled_(self.switch_model is not None)

    def _run_benchmark(self) -> None:
        import threading

        from PyObjCTools import AppHelper

        if self._benchmarking:
            return
        models = [
            (model.repo, str(path))
            for model in catalog.CATALOG
            if (path := catalog.local_path(model.repo)) is not None
        ]
        if not models:
            return
        self._benchmarking = True
        self._bench_button.setEnabled_(False)
        current = self.current or models[0][1]

        def progress(message: str) -> None:
            AppHelper.callAfter(self._bench_status.setStringValue_, message)

        def work() -> None:
            from praattypen import benchmark

            try:
                results = benchmark.run(models, restore=current, on_progress=progress)
                message = time.strftime("Last test %H:%M · synthetic voices, so expect real speech to be a bit slower")
            except Exception as error:  # noqa: BLE001
                logger.exception("Snelheidstest mislukt")
                results, message = benchmark.load_results(), f"Speed test failed: {error}"

            def finish() -> None:
                self._bench_results = results
                self._benchmarking = False
                self._bench_button.setEnabled_(True)
                self._bench_status.setStringValue_(message)
                self.w._refresh()

            AppHelper.callAfter(finish)

        threading.Thread(target=work, daemon=True, name="airquote-benchmark").start()

    def _use(self, repo: str) -> None:
        path = catalog.local_path(repo)
        if path is not None:
            self._switch(repo, path)
            return
        self._downloading.add(repo)
        self.w._refresh()

        def finished(path, error) -> None:
            from PyObjCTools import AppHelper

            def on_main() -> None:
                self._downloading.discard(repo)
                if path is not None:
                    self._switch(repo, path)
                else:
                    self.w._refresh()
                    _alert("Download failed", error)

            AppHelper.callAfter(on_main)

        catalog.download(repo, finished)

    def _switch(self, repo: str, path: Path) -> None:
        # The repo id is what the config file should say when the model lives
        # in the Hugging Face cache; a folder elsewhere is used by its path.
        value = repo if str(path).startswith(str(catalog.HF_HUB)) else str(path)
        try:
            if self.switch_model is not None:
                self.switch_model(value)
        except Exception as error:  # noqa: BLE001
            _alert("Could not switch model", str(error))
        self.w._refresh()


def catalog_audio_extensions() -> set[str]:
    from praattypen.batch import AUDIO_EXTENSIONS

    return AUDIO_EXTENSIONS


def _symbol(name: str, size: float):
    from praattypen.statuswindow import _symbol as symbol

    return symbol(name, size)


def w_image(name: str, size: float, tint):
    from praattypen.statuswindow import _image_view

    return _image_view(name, size, tint)


def _fix_width(view, width: float) -> None:
    import AppKit

    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    AppKit.NSLayoutConstraint.activateConstraints_([view.widthAnchor().constraintEqualToConstant_(width)])


def _reveal(path: Path) -> None:
    import AppKit
    from Foundation import NSURL

    AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([NSURL.fileURLWithPath_(str(path))])


def _alert(title: str, message: str) -> None:
    import AppKit

    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.runModal()
