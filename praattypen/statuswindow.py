from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from praattypen import ui
from praattypen.config import AppConfig
from praattypen.history import HistoryEntry, HistoryStore, format_duration
from praattypen.logging_setup import get_logger
from praattypen.modes import MODES, ModeState
from praattypen.permissions import accessibility_trusted, hid_access, microphone_status
from praattypen.vocabulary import Vocabulary

logger = get_logger(__name__)

APP_NAME = "AirQuote"


class StatusWindow(Protocol):
    """The app's main window: state, mode, recent dictations, vocabulary, setup."""

    def show(self) -> None:
        ...

    def set_idle(self) -> None:
        ...

    def set_listening(self) -> None:
        ...

    def set_processing(self) -> None:
        ...

    def refresh(self) -> None:
        ...


class NullStatusWindow:
    """No-op window, used when AppKit isn't available or it is disabled."""

    def show(self) -> None:
        return None

    def set_idle(self) -> None:
        return None

    def set_listening(self) -> None:
        return None

    def set_processing(self) -> None:
        return None

    def refresh(self) -> None:
        return None

    def attach_batch(self, batch, meeting, on_finished=None) -> None:
        return None

    def update_job(self, job) -> None:
        return None


_IDLE = "idle"
_LISTENING = "listening"
_PROCESSING = "processing"

_RECENT_LIMIT = 30

# Long enough for our window to be gone and the previous app to be front again
# before Cmd+V fires -- otherwise the paste lands in AirQuote itself.
_HIDE_BEFORE_PASTE_SECONDS = 0.35

# IOHIDRequestType: the listen-only tap the hotkey needs.
_LISTEN_EVENT = 1

_WIDTH = 660
_HEIGHT = 820
_SIDE = 28

_MODE_HINTS = {
    "dictate": "Speak, and AirQuote types exactly what you said.",
    "to_english": "Speak Dutch (or any language) and English text is pasted. Runs in Whisper.",
    "to_dutch": "Speak English (or any language) and Dutch text is pasted. Uses your local AI model.",
    "feedback": "Talk through your comments; AirQuote writes structured, constructive "
    "student feedback. Uses your local AI model.",
    "rewrite": "Select or copy some text, then say what to do with it (\u201cmake this "
    "more academic\u201d). The result replaces your selection.",
}

_TARGET_CLASS = None
_DELEGATE_CLASS = None
_FLIPPED_CLASS = None


def _target_class():
    """Objective-C target for buttons and controls, defined once per process."""
    global _TARGET_CLASS
    if _TARGET_CLASS is not None:
        return _TARGET_CLASS

    import AppKit
    import objc

    class _AirQuoteWindowTarget(AppKit.NSObject):
        def init(self):
            self = objc.super(_AirQuoteWindowTarget, self).init()
            if self is None:
                return None
            self.handlers: list[Callable[[], None]] = []
            return self

        def addHandler_(self, handler) -> int:
            self.handlers.append(handler)
            return len(self.handlers) - 1

        def invoke_(self, sender) -> None:
            index = int(sender.tag())
            try:
                self.handlers[index]()
            except Exception as error:  # noqa: BLE001 - a bad button must not kill the app
                logger.error("Fout in vensteractie: %s", error)

    _TARGET_CLASS = _AirQuoteWindowTarget
    return _TARGET_CLASS


def _delegate_class():
    """App delegate that reopens the window when the Dock icon is clicked."""
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is not None:
        return _DELEGATE_CLASS

    import AppKit
    import objc

    class _AirQuoteAppDelegate(AppKit.NSObject):
        def initWithShow_(self, show):
            self = objc.super(_AirQuoteAppDelegate, self).init()
            if self is None:
                return None
            self.show = show
            return self

        def applicationShouldHandleReopen_hasVisibleWindows_(self, app, has_windows) -> bool:
            try:
                self.show()
            except Exception as error:  # noqa: BLE001
                logger.error("Kon statusvenster niet openen: %s", error)
            return True

    _DELEGATE_CLASS = _AirQuoteAppDelegate
    return _DELEGATE_CLASS


def _flipped_class():
    """A view whose origin is top-left, so scrolled lists start at the top."""
    global _FLIPPED_CLASS
    if _FLIPPED_CLASS is not None:
        return _FLIPPED_CLASS

    import AppKit

    class _AirQuoteFlippedView(AppKit.NSView):
        def isFlipped(self) -> bool:
            return True

    _FLIPPED_CLASS = _AirQuoteFlippedView
    return _FLIPPED_CLASS


class RecordingStatusWindow:
    """Must be constructed on the main thread (AppKit requirement).

    The public methods are safe to call from any thread: like the overlay and
    the menu bar, they marshal onto the main run loop.
    """

    def __init__(
        self,
        config: AppConfig,
        engine_name: str,
        model: str,
        history: HistoryStore,
        config_path: Path,
        log_path: Path | None = None,
        repaste: Callable[[str], object] | None = None,
        started_at: float | None = None,
        vocabulary: Vocabulary | None = None,
        modes: ModeState | None = None,
        switch_model: Callable[[str], None] | None = None,
        toggle_dictation: Callable[[], None] | None = None,
    ) -> None:
        import AppKit

        self._toggle_dictation = toggle_dictation

        self._switch_model_callback = (
            (lambda value: (switch_model(value), setattr(self, "_model", value)))
            if switch_model is not None
            else None
        )
        import Quartz  # noqa: F401 - registers CGColorRef bridging used below
        from Foundation import NSMakeRect, NSMakeSize

        self._AppKit = AppKit
        self._config = config
        self._engine_name = engine_name
        self._model = model
        self._history = history
        self._config_path = config_path
        self._log_path = log_path
        self._repaste = repaste
        self._started_at = started_at or time.time()
        self._vocabulary = vocabulary or Vocabulary()
        self._modes = modes or ModeState()
        self._state = _IDLE
        self._target = _target_class().alloc().init()
        self._row_target = _target_class().alloc().init()

        AppKit.NSApplication.sharedApplication()

        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT),
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
            | AppKit.NSWindowStyleMaskResizable
            | AppKit.NSWindowStyleMaskFullSizeContentView,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_(APP_NAME)
        window.setTitlebarAppearsTransparent_(True)
        window.setTitleVisibility_(AppKit.NSWindowTitleHidden)
        window.setMovableByWindowBackground_(True)
        window.setMinSize_(NSMakeSize(580, 640))
        # Closing must not deallocate the window: the menu bar reopens this
        # same instance, and a released one crashes the app on the second show.
        window.setReleasedWhenClosed_(False)
        window.center()
        window.setFrameAutosaveName_("AirQuoteMainWindow")
        self._window = window

        background = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        )
        background.setMaterial_(AppKit.NSVisualEffectMaterialUnderWindowBackground)
        background.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        background.setState_(AppKit.NSVisualEffectStateFollowsWindowActiveState)
        window.setContentView_(background)

        self._build(background)
        self._modes.subscribe(lambda _mode: self._call_on_main(self._sync_mode))
        self._apply_state()
        self._refresh()
        self._install_dock_reopen()

    def _install_dock_reopen(self) -> None:
        app = self._AppKit.NSApplication.sharedApplication()
        if app.delegate() is not None:
            return
        self._delegate = _delegate_class().alloc().initWithShow_(self.show)
        app.setDelegate_(self._delegate)

    # --- building -------------------------------------------------------

    def _build(self, content) -> None:
        AppKit = self._AppKit
        from Foundation import NSMakeRect

        # Header: orb, name, live status.
        orb_view = AppKit.NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 46, 46))
        _fix_size(orb_view, 46, 46)
        self._orb = ui.Orb(orb_view, 46)
        self._orb.set_speed(14.0)
        title = self._label(APP_NAME, size=24, weight=AppKit.NSFontWeightBold)
        self._dot = self._dot_view(8)
        self._state_label = self._label("", size=13, secondary=True)
        status_row = self._hstack([self._dot, self._state_label], spacing=7)
        title_stack = self._vstack([title, status_row], spacing=2)
        self._uptime_label = self._label("", size=11, tertiary=True)
        # Failsafe next to the hotkey: start and stop dictation with a click.
        self._dictate_button = self._button("Start Dictation", self._dictate_clicked, symbol="mic.fill")
        self._dictate_button.setControlSize_(AppKit.NSControlSizeLarge)
        self._dictate_button.setToolTip_(
            "Starts dictating into the app you were using before; "
            "click again here, or click the floating bar, to stop."
        )
        right = self._vstack([self._dictate_button, self._uptime_label], spacing=6)
        right.setAlignment_(AppKit.NSLayoutAttributeTrailing)
        header = self._hstack([orb_view, title_stack, self._spacer(), right], spacing=14)

        # Mode switcher.
        labels = [mode.short for mode in MODES]
        self._mode_control = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            labels, AppKit.NSSegmentSwitchTrackingSelectOne, self._target, "invoke:"
        )
        self._mode_control.setTag_(self._target.addHandler_(self._mode_clicked))
        for index, mode in enumerate(MODES):
            image = _symbol(mode.symbol, 12)
            if image is not None:
                self._mode_control.setImage_forSegment_(image, index)
        self._mode_control.setSegmentDistribution_(AppKit.NSSegmentDistributionFillEqually)
        self._mode_control.setControlSize_(AppKit.NSControlSizeLarge)
        self._mode_hint = self._label("", size=12, secondary=True, wrap=True)
        mode_title = self._section_title("Mode")

        # Today, at a glance.
        self._stat_values = {}
        cards = [
            self._stat_card("today", "Dictations today"),
            self._stat_card("words", "Words today"),
            self._stat_card("speech", "Spoken today"),
            self._stat_card("speed", "Avg. processing"),
        ]
        stats = self._hstack(cards, spacing=10)
        stats.setDistribution_(AppKit.NSStackViewDistributionFillEqually)

        # Tabs: Recent / Vocabulary / Setup.
        self._tab_control = AppKit.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ["Recent", "Recordings", "Vocabulary", "Models", "Setup"],
            AppKit.NSSegmentSwitchTrackingSelectOne,
            self._target,
            "invoke:",
        )
        self._tab_control.setTag_(self._target.addHandler_(self._tab_clicked))
        self._tab_control.setSelectedSegment_(0)
        tab_row = self._hstack([self._tab_control, self._spacer()], spacing=8)

        from praattypen.window_tabs import ModelsTab, RecordingsTab

        self.recordings = RecordingsTab(self)
        self.models_tab = ModelsTab(self, switch_model=self._switch_model_callback)
        self._tabs = AppKit.NSTabView.alloc().init()
        self._tabs.setTabViewType_(AppKit.NSNoTabsNoBorder)
        self._tabs.setDrawsBackground_(False)
        for identifier, view in (
            ("recent", self._build_recent()),
            ("recordings", self.recordings.view),
            ("vocabulary", self._build_vocabulary()),
            ("models", self.models_tab.view),
            ("setup", self._build_setup()),
        ):
            item = AppKit.NSTabViewItem.alloc().initWithIdentifier_(identifier)
            item.setView_(view)
            self._tabs.addTabViewItem_(item)

        # Footer.
        footer_buttons = [
            self._button("Settings File", lambda: self._open(self._config_path), symbol="gearshape"),
        ]
        if self._log_path is not None:
            footer_buttons.append(
                self._button("Log", lambda: self._open(self._log_path), symbol="doc.text")
            )
        private = self._hstack(
            [_image_view("lock.fill", 10, AppKit.NSColor.tertiaryLabelColor()),
             self._label("Processed on this Mac", size=11, tertiary=True)],
            spacing=5,
        )
        footer = self._hstack(footer_buttons + [self._spacer(), private], spacing=8)

        root = self._vstack(
            [header, mode_title, self._mode_control, self._mode_hint, stats, tab_row, self._tabs, footer],
            spacing=14,
        )
        root.setCustomSpacing_afterView_(22, header)
        root.setCustomSpacing_afterView_(8, mode_title)
        root.setCustomSpacing_afterView_(8, self._mode_control)
        root.setCustomSpacing_afterView_(20, self._mode_hint)
        root.setCustomSpacing_afterView_(20, stats)
        root.setCustomSpacing_afterView_(10, tab_row)
        root.setDistribution_(AppKit.NSStackViewDistributionFill)
        for view in (header, mode_title, self._mode_control, self._mode_hint, stats, tab_row, footer):
            view.setContentHuggingPriority_forOrientation_(
                AppKit.NSLayoutPriorityDefaultHigh, AppKit.NSLayoutConstraintOrientationVertical
            )
        self._tabs.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationVertical
        )

        root.setTranslatesAutoresizingMaskIntoConstraints_(False)
        content.addSubview_(root)
        constraints = [
            root.leadingAnchor().constraintEqualToAnchor_constant_(content.leadingAnchor(), _SIDE),
            root.trailingAnchor().constraintEqualToAnchor_constant_(content.trailingAnchor(), -_SIDE),
            root.topAnchor().constraintEqualToAnchor_constant_(content.topAnchor(), 44),
            root.bottomAnchor().constraintEqualToAnchor_constant_(content.bottomAnchor(), -20),
        ]
        for view in (header, self._mode_control, self._mode_hint, stats, tab_row, self._tabs, footer):
            constraints.append(view.widthAnchor().constraintEqualToAnchor_(root.widthAnchor()))
        AppKit.NSLayoutConstraint.activateConstraints_(constraints)

    def _build_recent(self):
        AppKit = self._AppKit
        container = AppKit.NSView.alloc().init()

        scroll = AppKit.NSScrollView.alloc().init()
        scroll.setDrawsBackground_(False)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(AppKit.NSNoBorder)
        document = _flipped_class().alloc().init()
        document.setTranslatesAutoresizingMaskIntoConstraints_(False)
        scroll.setDocumentView_(document)
        self._rows = self._vstack([], spacing=8)
        self._rows.setTranslatesAutoresizingMaskIntoConstraints_(False)
        document.addSubview_(self._rows)
        clip = scroll.contentView()
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                document.leadingAnchor().constraintEqualToAnchor_(clip.leadingAnchor()),
                document.trailingAnchor().constraintEqualToAnchor_(clip.trailingAnchor()),
                document.topAnchor().constraintEqualToAnchor_(clip.topAnchor()),
                self._rows.leadingAnchor().constraintEqualToAnchor_(document.leadingAnchor()),
                self._rows.trailingAnchor().constraintEqualToAnchor_(document.trailingAnchor()),
                self._rows.topAnchor().constraintEqualToAnchor_(document.topAnchor()),
                self._rows.bottomAnchor().constraintEqualToAnchor_(document.bottomAnchor()),
            ]
        )

        self._empty = self._vstack(
            [
                _image_view("waveform", 34, AppKit.NSColor.tertiaryLabelColor()),
                self._label("No dictations yet", size=15, weight=AppKit.NSFontWeightSemibold),
                self._label("", size=12, secondary=True),
            ],
            spacing=8,
        )
        self._empty.setAlignment_(AppKit.NSLayoutAttributeCenterX)
        self._empty_hint = self._empty.arrangedSubviews()[2]

        clear = self._button("Clear History…", self._confirm_clear, symbol="trash", small=True)
        clear.setBordered_(False)
        clear.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
        self._clear_button = clear

        for view in (scroll, self._empty, clear):
            view.setTranslatesAutoresizingMaskIntoConstraints_(False)
            container.addSubview_(view)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                scroll.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
                scroll.bottomAnchor().constraintEqualToAnchor_constant_(clear.topAnchor(), -6),
                clear.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                clear.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
                self._empty.centerXAnchor().constraintEqualToAnchor_(container.centerXAnchor()),
                self._empty.centerYAnchor().constraintEqualToAnchor_constant_(
                    container.centerYAnchor(), -12
                ),
            ]
        )
        self._scroll = scroll
        return container

    def _build_vocabulary(self):
        AppKit = self._AppKit
        container = AppKit.NSView.alloc().init()
        intro = self._label(
            "Names, jargon and abbreviations AirQuote should always spell your way, "
            "one per line. Whisper is nudged towards these spellings, and their "
            "capitalisation is corrected after transcribing (buas → BUas).",
            size=12,
            secondary=True,
            wrap=True,
        )
        scroll = AppKit.NSTextView.scrollableTextView()
        text_view = scroll.documentView()
        text_view.setFont_(AppKit.NSFont.systemFontOfSize_(13))
        text_view.setRichText_(False)
        text_view.setAutomaticQuoteSubstitutionEnabled_(False)
        text_view.setAutomaticDashSubstitutionEnabled_(False)
        text_view.setAutomaticSpellingCorrectionEnabled_(False)
        text_view.setTextContainerInset_((10, 10))
        text_view.setDrawsBackground_(True)
        text_view.setBackgroundColor_(AppKit.NSColor.textBackgroundColor())
        scroll.setBorderType_(AppKit.NSNoBorder)
        scroll.setWantsLayer_(True)
        scroll.layer().setCornerRadius_(10)
        scroll.layer().setMasksToBounds_(True)
        scroll.layer().setBorderWidth_(0.5)
        scroll.layer().setBorderColor_(ui.cg((0.5, 0.5, 0.5), 0.35))
        self._vocab_text = text_view

        save = self._button("Save", self._save_vocabulary, symbol="checkmark")
        save.setKeyEquivalent_("s")
        save.setKeyEquivalentModifierMask_(AppKit.NSEventModifierFlagCommand)
        save.setBezelColor_(AppKit.NSColor.controlAccentColor())
        self._vocab_status = self._label("", size=12, secondary=True)
        buttons = self._hstack([self._vocab_status, self._spacer(), save], spacing=8)

        for view in (intro, scroll, buttons):
            view.setTranslatesAutoresizingMaskIntoConstraints_(False)
            container.addSubview_(view)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                intro.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                intro.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                intro.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
                scroll.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                scroll.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                scroll.topAnchor().constraintEqualToAnchor_constant_(intro.bottomAnchor(), 12),
                scroll.bottomAnchor().constraintEqualToAnchor_constant_(buttons.topAnchor(), -12),
                buttons.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                buttons.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                buttons.bottomAnchor().constraintEqualToAnchor_(container.bottomAnchor()),
            ]
        )
        return container

    def _build_setup(self):
        AppKit = self._AppKit
        container = AppKit.NSView.alloc().init()
        self._setup_values = {}
        rows = []
        for title, key in (
            ("Speech model", "model"),
            ("Engine", "engine"),
            ("Languages", "language"),
            ("Hotkey", "hotkey"),
            ("Hands-free", "hands_free"),
            ("AI model", "assistant"),
            ("Sounds", "sounds"),
        ):
            value = self._label("", size=13)
            self._setup_values[key] = value
            rows.append([self._label(title, size=13, secondary=True), value])
        self._perm_stack = self._hstack([], spacing=14)
        rows.append([self._label("Permissions", size=13, secondary=True), self._perm_stack])
        grid = AppKit.NSGridView.gridViewWithViews_(rows)
        grid.setRowSpacing_(12)
        grid.setColumnSpacing_(18)
        grid.columnAtIndex_(0).setWidth_(120)
        grid.columnAtIndex_(0).setXPlacement_(AppKit.NSGridCellPlacementTrailing)
        card = self._card(grid, padding=18)
        card.setTranslatesAutoresizingMaskIntoConstraints_(False)
        container.addSubview_(card)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                card.leadingAnchor().constraintEqualToAnchor_(container.leadingAnchor()),
                card.trailingAnchor().constraintEqualToAnchor_(container.trailingAnchor()),
                card.topAnchor().constraintEqualToAnchor_(container.topAnchor()),
            ]
        )
        return container

    def _stat_card(self, key: str, caption: str):
        AppKit = self._AppKit
        value = self._label("–", size=22, weight=AppKit.NSFontWeightSemibold, rounded=True)
        self._stat_values[key] = value
        stack = self._vstack([value, self._label(caption, size=11, secondary=True)], spacing=2)
        return self._card(stack, padding=14)

    # --- public API -----------------------------------------------------

    def show(self) -> None:
        self._call_on_main(self._show)

    def set_idle(self) -> None:
        self._call_on_main(self._set_state, _IDLE)

    def set_listening(self) -> None:
        self._call_on_main(self._set_state, _LISTENING)

    def set_processing(self) -> None:
        self._call_on_main(self._set_state, _PROCESSING)

    def refresh(self) -> None:
        self._call_on_main(self._refresh)

    def attach_batch(self, batch, meeting, on_finished=None) -> None:
        self._call_on_main(self.recordings.attach, batch, meeting, on_finished)

    def update_job(self, job) -> None:
        self._call_on_main(self.recordings.update_job, job)

    # --- main-thread work ------------------------------------------------

    def _show(self) -> None:
        self._refresh()
        self._vocab_text.setString_(self._vocabulary.text())
        self._update_vocab_status()
        AppKit = self._AppKit
        AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self._window.makeKeyAndOrderFront_(None)

    def _set_state(self, state: str) -> None:
        self._state = state
        self._apply_state()

    def _apply_state(self) -> None:
        hotkey = ui.hotkey_name(self._config.hotkey)
        if self._state == _LISTENING:
            text, color, speed = "Listening…", ui.LISTENING, 2.0
        elif self._state == _PROCESSING:
            text, color, speed = self._modes.current.working, ui.PROCESSING, 0.9
        else:
            text, color, speed = f"Ready · hold {hotkey} to dictate", ui.READY, 14.0
        self._state_label.setStringValue_(text)
        self._dot.layer().setBackgroundColor_(ui.cg(color))
        AppKit = self._AppKit
        button = self._dictate_button
        if self._state == _LISTENING:
            button.setTitle_("Stop & Paste")
            button.setImage_(_symbol("stop.fill", 12))
            button.setBezelColor_(AppKit.NSColor.systemRedColor())
            button.setEnabled_(True)
        elif self._state == _PROCESSING:
            button.setTitle_(self._modes.current.working)
            button.setImage_(_symbol("hourglass", 12))
            button.setBezelColor_(None)
            button.setEnabled_(False)
        else:
            button.setTitle_("Start Dictation")
            button.setImage_(_symbol("mic.fill", 12))
            button.setBezelColor_(AppKit.NSColor.controlAccentColor())
            button.setEnabled_(self._toggle_dictation is not None)
        self._orb.set_speed(speed)
        started = datetime.fromtimestamp(self._started_at).strftime("%H:%M")
        self._uptime_label.setStringValue_(
            f"Running since {started} · {format_duration(time.time() - self._started_at)}"
        )

    def _sync_mode(self) -> None:
        mode = self._modes.current
        for index, candidate in enumerate(MODES):
            if candidate.key == mode.key:
                self._mode_control.setSelectedSegment_(index)
        self._mode_hint.setStringValue_(_MODE_HINTS.get(mode.key, ""))

    def _dictate_clicked(self) -> None:
        if self._toggle_dictation is not None:
            self._toggle_dictation()

    def _mode_clicked(self) -> None:
        index = int(self._mode_control.selectedSegment())
        if 0 <= index < len(MODES):
            self._modes.set(MODES[index].key)
        self._sync_mode()

    def _tab_clicked(self) -> None:
        self._tabs.selectTabViewItemAtIndex_(int(self._tab_control.selectedSegment()))

    def _refresh(self) -> None:
        self._apply_state()
        self._sync_mode()
        self._reload_stats()
        self._reload_rows()
        self._reload_setup()
        self.models_tab.refresh(self._model, self._history.recent(200))

    def _reload_stats(self) -> None:
        stats = self._history.stats()
        self._stat_values["today"].setStringValue_(str(stats.today))
        self._stat_values["words"].setStringValue_(f"{stats.words_today:,}")
        self._stat_values["speech"].setStringValue_(format_duration(stats.speech_seconds_today))
        self._stat_values["speed"].setStringValue_(
            f"{stats.average_transcribe_seconds:.1f}s" if stats.average_transcribe_seconds else "–"
        )

    def _reload_rows(self) -> None:
        AppKit = self._AppKit
        entries = list(reversed(self._history.recent(_RECENT_LIMIT)))
        entries.sort(key=lambda entry: entry.timestamp, reverse=True)

        for view in list(self._rows.arrangedSubviews()):
            self._rows.removeArrangedSubview_(view)
            view.removeFromSuperview()
        # A fresh target per reload drops the closures of the rows we just
        # threw away, instead of growing a handler list forever.
        self._row_target = _target_class().alloc().init()
        rows = [self._row(entry) for entry in entries]
        self._rows.setViews_inGravity_(rows, AppKit.NSStackViewGravityTop)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [row.widthAnchor().constraintEqualToAnchor_(self._rows.widthAnchor()) for row in rows]
        )
        self._empty_hint.setStringValue_(
            f"Hold {ui.hotkey_name(self._config.hotkey)}, speak, and let go."
        )
        self._empty.setHidden_(bool(entries))
        self._scroll.setHidden_(not entries)
        self._clear_button.setHidden_(not entries)

    def _reload_setup(self) -> None:
        AppKit = self._AppKit
        config = self._config
        hotkey = ui.hotkey_name(config.hotkey)
        values = {
            "model": ui.model_name(self._model),
            "engine": ui.engine_name(self._engine_name),
            "language": ui.language_name(config.language),
            "hotkey": f"Hold {hotkey} while you speak",
            "hands_free": f"Double-tap {hotkey} to start, tap again to stop",
            "assistant": f"{config.assistant_model} via Ollama (→ Dutch, Feedback, Rewrite)",
            "sounds": f"On · volume {int(config.sound_volume * 100)}%"
            if config.sounds_enabled
            else "Off",
        }
        for key, value in values.items():
            self._setup_values[key].setStringValue_(value)

        for view in list(self._perm_stack.arrangedSubviews()):
            self._perm_stack.removeArrangedSubview_(view)
            view.removeFromSuperview()
        trusted = accessibility_trusted()
        for name, ok in (
            ("Microphone", microphone_status() == "toegestaan"),
            ("Input Monitoring", hid_access(_LISTEN_EVENT) == "toegestaan"),
            ("Accessibility", trusted is True),
        ):
            icon = _image_view(
                "checkmark.circle.fill" if ok else "exclamationmark.circle.fill",
                13,
                AppKit.NSColor.systemGreenColor() if ok else AppKit.NSColor.systemOrangeColor(),
            )
            self._perm_stack.addArrangedSubview_(
                self._hstack([icon, self._label(name, size=12)], spacing=4)
            )

    def _row(self, entry: HistoryEntry):
        AppKit = self._AppKit
        text = self._label(" ".join(entry.text.split()), size=13, wrap=True, max_lines=3)
        text.setPreferredMaxLayoutWidth_(_WIDTH - 2 * _SIDE - 170)
        text.setToolTip_(entry.text)
        text.setContentCompressionResistancePriority_forOrientation_(
            AppKit.NSLayoutPriorityDefaultLow, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        meta_parts = [
            _friendly_time(entry.timestamp),
            f"{entry.duration_seconds:.1f}s",
            f"{entry.word_count} words",
        ]
        if not entry.pasted:
            meta_parts.append("clipboard only")
        meta = self._label("  ·  ".join(meta_parts), size=11, tertiary=True)
        body = self._vstack([text, meta], spacing=4)

        copy = self._button(
            "", lambda value=entry.text: self._copy(value), symbol="doc.on.doc", target=self._row_target
        )
        copy.setBordered_(False)
        copy.setToolTip_("Copy")
        copy.setContentTintColor_(AppKit.NSColor.secondaryLabelColor())
        views = [body, self._spacer(), copy]
        if self._repaste is not None:
            paste = self._button(
                "Paste",
                lambda value=entry.text: self._paste(value),
                target=self._row_target,
                small=True,
            )
            views.append(paste)
        row = self._hstack(views, spacing=10)
        row.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        return self._card(row, padding=12, radius=10, alpha=0.045)

    def _copy(self, text: str) -> None:
        AppKit = self._AppKit
        board = AppKit.NSPasteboard.generalPasteboard()
        board.clearContents()
        board.setString_forType_(text, AppKit.NSPasteboardTypeString)

    def _paste(self, text: str) -> None:
        if self._repaste is None or not text:
            return
        # Our window is frontmost while you click, so pasting now would put the
        # text in AirQuote. Step aside first, then type into whatever was
        # underneath.
        self._AppKit.NSApplication.sharedApplication().hide_(None)

        def paste_later() -> None:
            time.sleep(_HIDE_BEFORE_PASTE_SECONDS)
            try:
                self._repaste(text)  # type: ignore[misc]
            except Exception as error:  # noqa: BLE001
                logger.error("Opnieuw plakken mislukt: %s", error)

        threading.Thread(target=paste_later, daemon=True, name="airquote-repaste").start()

    def _confirm_clear(self) -> None:
        AppKit = self._AppKit
        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_("Clear dictation history?")
        alert.setInformativeText_(
            "This removes the saved text of all recent dictations from this Mac. "
            "It can't be undone."
        )
        alert.addButtonWithTitle_("Clear History")
        alert.addButtonWithTitle_("Cancel")
        try:
            alert.buttons()[0].setHasDestructiveAction_(True)
        except AttributeError:
            pass
        if alert.runModal() == AppKit.NSAlertFirstButtonReturn:
            self._history.clear()
            self._refresh()

    def _save_vocabulary(self) -> None:
        try:
            self._vocabulary.save(str(self._vocab_text.string()))
        except OSError as error:
            self._vocab_status.setStringValue_(f"Could not save: {error}")
            return
        self._update_vocab_status(saved=True)

    def _update_vocab_status(self, saved: bool = False) -> None:
        count = len(self._vocabulary.terms())
        terms = f"{count} term{'s' if count != 1 else ''}"
        self._vocab_status.setStringValue_(f"Saved · {terms} active" if saved else f"{terms} active")

    def _open(self, path: Path | None) -> None:
        if path is None:
            return
        from Foundation import NSURL

        self._AppKit.NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    # --- small AppKit helpers -------------------------------------------

    def _label(
        self,
        text: str,
        size: float = 13,
        weight=None,
        secondary: bool = False,
        tertiary: bool = False,
        wrap: bool = False,
        max_lines: int = 0,
        rounded: bool = False,
    ):
        AppKit = self._AppKit
        field = (
            AppKit.NSTextField.wrappingLabelWithString_(text)
            if wrap
            else AppKit.NSTextField.labelWithString_(text)
        )
        font = AppKit.NSFont.systemFontOfSize_weight_(
            size, weight if weight is not None else AppKit.NSFontWeightRegular
        )
        if rounded:
            descriptor = font.fontDescriptor().fontDescriptorWithDesign_(
                AppKit.NSFontDescriptorSystemDesignRounded
            )
            if descriptor is not None:
                font = AppKit.NSFont.fontWithDescriptor_size_(descriptor, size) or font
        field.setFont_(font)
        if tertiary:
            field.setTextColor_(AppKit.NSColor.tertiaryLabelColor())
        elif secondary:
            field.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        if wrap:
            field.setSelectable_(False)
            if max_lines:
                # Truncating line-break modes make a field single-line; wrap
                # by word and let the cell put the ellipsis on the last line.
                field.setMaximumNumberOfLines_(max_lines)
                field.setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
                field.cell().setTruncatesLastVisibleLine_(True)
        else:
            field.setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
        return field

    def _section_title(self, text: str):
        AppKit = self._AppKit
        return self._label(text.upper(), size=11, weight=AppKit.NSFontWeightSemibold, tertiary=True)

    def _card(self, view, padding: float = 14, radius: float = 12, alpha: float = 0.06):
        AppKit = self._AppKit
        from Foundation import NSMakeSize

        box = AppKit.NSBox.alloc().init()
        box.setBoxType_(AppKit.NSBoxCustom)
        box.setTitlePosition_(AppKit.NSNoTitle)
        box.setBorderWidth_(0)
        box.setCornerRadius_(radius)
        box.setFillColor_(AppKit.NSColor.labelColor().colorWithAlphaComponent_(alpha))
        box.setContentViewMargins_(NSMakeSize(0, 0))
        inner = AppKit.NSView.alloc().init()
        view.setTranslatesAutoresizingMaskIntoConstraints_(False)
        inner.addSubview_(view)
        AppKit.NSLayoutConstraint.activateConstraints_(
            [
                view.leadingAnchor().constraintEqualToAnchor_constant_(inner.leadingAnchor(), padding),
                view.trailingAnchor().constraintEqualToAnchor_constant_(inner.trailingAnchor(), -padding),
                view.topAnchor().constraintEqualToAnchor_constant_(inner.topAnchor(), padding),
                view.bottomAnchor().constraintEqualToAnchor_constant_(inner.bottomAnchor(), -padding),
            ]
        )
        box.setContentView_(inner)
        return box

    def _dot_view(self, size: float):
        AppKit = self._AppKit
        view = AppKit.NSView.alloc().init()
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(size / 2)
        _fix_size(view, size, size)
        return view

    def _spacer(self):
        AppKit = self._AppKit
        view = AppKit.NSView.alloc().init()
        view.setContentHuggingPriority_forOrientation_(
            1, AppKit.NSLayoutConstraintOrientationHorizontal
        )
        return view

    def _button(
        self,
        title: str,
        handler: Callable[[], None],
        symbol: str | None = None,
        target=None,
        small: bool = False,
    ):
        AppKit = self._AppKit
        target = target or self._target
        button = AppKit.NSButton.buttonWithTitle_target_action_(title, target, "invoke:")
        button.setTag_(target.addHandler_(handler))
        button.setBezelStyle_(AppKit.NSBezelStyleRounded)
        if symbol:
            image = _symbol(symbol, 12)
            if image is not None:
                button.setImage_(image)
                button.setImagePosition_(
                    AppKit.NSImageLeading if title else AppKit.NSImageOnly
                )
        if small:
            button.setControlSize_(AppKit.NSControlSizeSmall)
            button.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        return button

    def _vstack(self, views: list, spacing: float = 8):
        AppKit = self._AppKit
        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationVertical)
        stack.setAlignment_(AppKit.NSLayoutAttributeLeading)
        stack.setSpacing_(spacing)
        return stack

    def _hstack(self, views: list, spacing: float = 8):
        AppKit = self._AppKit
        stack = AppKit.NSStackView.stackViewWithViews_(views)
        stack.setOrientation_(AppKit.NSUserInterfaceLayoutOrientationHorizontal)
        stack.setAlignment_(AppKit.NSLayoutAttributeCenterY)
        stack.setSpacing_(spacing)
        return stack

    def _call_on_main(self, func, *args) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(func, *args)


def _symbol(name: str, size: float):
    import AppKit

    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size, AppKit.NSFontWeightMedium
    )
    return image.imageWithSymbolConfiguration_(config) or image


def _image_view(name: str, size: float, tint):
    import AppKit

    image = _symbol(name, size)
    view = AppKit.NSImageView.imageViewWithImage_(image) if image else AppKit.NSImageView.alloc().init()
    view.setContentTintColor_(tint)
    return view


def _fix_size(view, width: float, height: float) -> None:
    import AppKit

    view.setTranslatesAutoresizingMaskIntoConstraints_(False)
    AppKit.NSLayoutConstraint.activateConstraints_(
        [
            view.widthAnchor().constraintEqualToConstant_(width),
            view.heightAnchor().constraintEqualToConstant_(height),
        ]
    )


def _friendly_time(timestamp: float) -> str:
    moment = datetime.fromtimestamp(timestamp)
    today = date.today()
    if moment.date() == today:
        return f"Today {moment:%H:%M}"
    if moment.date() == today - timedelta(days=1):
        return f"Yesterday {moment:%H:%M}"
    return moment.strftime("%-d %b %H:%M")


def create_status_window(
    enabled: bool = True,
    config: AppConfig | None = None,
    engine_name: str = "",
    model: str = "",
    history: HistoryStore | None = None,
    config_path: Path | None = None,
    log_path: Path | None = None,
    repaste: Callable[[str], object] | None = None,
    vocabulary: Vocabulary | None = None,
    modes: ModeState | None = None,
    switch_model: Callable[[str], None] | None = None,
    toggle_dictation: Callable[[], None] | None = None,
) -> StatusWindow:
    """Build the status window, falling back to a no-op silently."""
    if not enabled:
        return NullStatusWindow()

    try:
        return RecordingStatusWindow(
            config=config or AppConfig(),
            engine_name=engine_name,
            model=model,
            history=history or HistoryStore(),
            config_path=config_path or Path("config.yaml"),
            log_path=log_path,
            repaste=repaste,
            vocabulary=vocabulary,
            modes=modes,
            switch_model=switch_model,
            toggle_dictation=toggle_dictation,
        )
    except ImportError as error:
        logger.warning(
            "Statusvenster uitgeschakeld: %s. Installeer pyobjc-framework-Cocoa "
            "voor het venster.",
            error,
        )
        return NullStatusWindow()
