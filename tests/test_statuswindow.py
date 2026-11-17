from praattypen.statuswindow import NullStatusWindow, create_status_window


def test_null_status_window_methods_are_no_ops() -> None:
    window = NullStatusWindow()

    window.show()
    window.set_idle()
    window.set_listening()
    window.set_processing()
    window.refresh()


def test_create_status_window_returns_null_window_when_disabled() -> None:
    assert isinstance(create_status_window(enabled=False), NullStatusWindow)
