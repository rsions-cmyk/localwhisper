import sys
import types

from praattypen.appearance import apply_activation_policy


class FakeApp:
    def __init__(self) -> None:
        self.policy = None
        self.main_menu = object()

    def setActivationPolicy_(self, policy) -> bool:
        self.policy = policy
        return True

    def mainMenu(self):
        return self.main_menu


def fake_appkit(monkeypatch) -> FakeApp:
    app = FakeApp()
    module = types.ModuleType("AppKit")
    module.NSApplicationActivationPolicyRegular = 0
    module.NSApplicationActivationPolicyAccessory = 1
    module.NSApplication = types.SimpleNamespace(sharedApplication=lambda: app)
    monkeypatch.setitem(sys.modules, "AppKit", module)
    return app


def test_dock_icon_promotes_the_app_to_a_regular_one(monkeypatch) -> None:
    app = fake_appkit(monkeypatch)

    assert apply_activation_policy(True) is True
    assert app.policy == 0


def test_no_dock_icon_keeps_the_app_an_accessory(monkeypatch) -> None:
    app = fake_appkit(monkeypatch)

    assert apply_activation_policy(False) is True
    assert app.policy == 1


def test_missing_appkit_does_not_stop_praattypen(monkeypatch) -> None:
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name == "AppKit":
            raise ImportError("geen pyobjc")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    assert apply_activation_policy(True) is False
