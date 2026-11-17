"""Quiet audio cues: recording started, recording stopped, text delivered.

Built from macOS system sounds played at low volume, so they confirm what
happened without drawing attention in a meeting. Safe to call from any thread:
playback is handed to the main run loop, like the rest of the UI.
"""

from __future__ import annotations

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

# System sound per moment. Short and soft, and each one sounds different so
# you can tell them apart without looking.
CUES = {
    "start": "Pop",
    "hands_free": "Tink",
    "stop": "Bottle",
    "done": "Glass",
    "error": "Basso",
}


class SoundCues:
    def __init__(self, enabled: bool = True, volume: float = 0.25) -> None:
        self.enabled = enabled
        self.volume = max(0.0, min(1.0, volume))
        self._sounds: dict[str, object] = {}

    def start(self) -> None:
        self._play("start")

    def hands_free(self) -> None:
        self._play("hands_free")

    def stop(self) -> None:
        self._play("stop")

    def done(self) -> None:
        self._play("done")

    def error(self) -> None:
        self._play("error")

    def _play(self, cue: str) -> None:
        if not self.enabled or self.volume <= 0:
            return
        try:
            from PyObjCTools import AppHelper
        except ImportError:
            return
        AppHelper.callAfter(self._play_on_main, cue)

    def _play_on_main(self, cue: str) -> None:
        try:
            import AppKit

            sound = self._sounds.get(cue)
            if sound is None:
                sound = AppKit.NSSound.soundNamed_(CUES[cue])
                if sound is None:
                    return
                # soundNamed_ hands out one shared instance per name; a copy
                # keeps our volume from leaking into other users of it.
                sound = sound.copy()
                sound.setVolume_(self.volume)
                self._sounds[cue] = sound
            if sound.isPlaying():
                sound.stop()
            sound.play()
        except Exception as error:  # noqa: BLE001 - a missing sound must never break dictation
            logger.debug("Geluid %s niet afgespeeld: %s", cue, error)
