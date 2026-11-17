from __future__ import annotations

from praattypen.logging_setup import get_logger

logger = get_logger(__name__)

_MIC_STATUS = {
    0: "nog niet gevraagd",
    1: "geblokkeerd door beheer",
    2: "GEWEIGERD",
    3: "toegestaan",
}


def microphone_status() -> str:
    """What macOS thinks of our microphone access.

    Worth asking explicitly: a denied app is not given an error, it is given
    silence, which is indistinguishable from a quiet room until you look here.
    """
    try:
        import AVFoundation
    except ImportError:
        return "onbekend (pyobjc-framework-AVFoundation niet geinstalleerd)"

    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio
    )
    return _MIC_STATUS.get(int(status), f"onbekend ({status})")


_HID_ACCESS = {0: "toegestaan", 1: "GEWEIGERD", 2: "nog niet gevraagd"}

# IOHIDRequestType
_POST_EVENT = 0
_LISTEN_EVENT = 1


def hid_access(request_type: int) -> str:
    """Ask IOKit directly about Input Monitoring / event posting.

    The Fn listener is a listen-only event tap, and macOS gates those on Input
    Monitoring -- a different switch from Accessibility, in a different list.
    Checking the wrong one sends you to the wrong settings pane.
    """
    import ctypes

    try:
        iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.restype = ctypes.c_uint32
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
    except (OSError, AttributeError) as error:
        return f"onbekend ({error})"
    return _HID_ACCESS.get(int(iokit.IOHIDCheckAccess(request_type)), "onbekend")


def accessibility_trusted() -> bool | None:
    """Whether we may observe keys. Without it the Fn hotkey never fires."""
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return None
    return bool(AXIsProcessTrusted())


def log_permissions() -> None:
    trusted = accessibility_trusted()
    listening = hid_access(_LISTEN_EVENT)
    posting = hid_access(_POST_EVENT)
    logger.info(
        "Permissies: microfoon=%s | invoermonitoring=%s | toetsen posten=%s | toegankelijkheid=%s",
        microphone_status(),
        listening,
        posting,
        {True: "toegestaan", False: "GEWEIGERD", None: "onbekend"}[trusted],
    )

    if listening != "toegestaan":
        logger.warning(
            "De Fn-toets komt niet aan zonder Invoermonitoring. Systeeminstellingen -> "
            "Privacy en beveiliging -> Invoermonitoring -> PraatTypen aanzetten. "
            "Let op: dit is een andere lijst dan Toegankelijkheid."
        )
    if trusted is False:
        logger.warning(
            "Zonder Toegankelijkheid kan PraatTypen de transcriptie niet plakken "
            "(Cmd+V wordt dan genegeerd). Systeeminstellingen -> Privacy en beveiliging "
            "-> Toegankelijkheid -> PraatTypen aanzetten."
        )
