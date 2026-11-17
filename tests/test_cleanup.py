from praattypen.cleanup import CleanupService
from praattypen.config import CleanupConfig


def test_cleanup_returns_raw_text_when_disabled() -> None:
    service = CleanupService(CleanupConfig(enabled=False), client=lambda *_args, **_kwargs: "clean")

    assert service.clean("uh ik eh wil koffie") == "uh ik eh wil koffie"


def test_cleanup_uses_client_when_ollama_enabled() -> None:
    seen: dict[str, object] = {}

    def fake_client(prompt: str, model: str, timeout: float) -> str:
        seen["prompt"] = prompt
        seen["model"] = model
        seen["timeout"] = timeout
        return "Ik wil koffie."

    service = CleanupService(
        CleanupConfig(enabled=True, provider="ollama", model="qwen2.5:3b"),
        client=fake_client,
    )

    assert service.clean("uh ik eh wil koffie") == "Ik wil koffie."
    assert "Verander de betekenis" in str(seen["prompt"])
    assert "uh ik eh wil koffie" in str(seen["prompt"])
    assert seen["model"] == "qwen2.5:3b"


def test_cleanup_falls_back_to_raw_text_when_client_fails() -> None:
    def broken_client(_prompt: str, _model: str, _timeout: float) -> str:
        raise RuntimeError("ollama is not running")

    service = CleanupService(CleanupConfig(enabled=True), client=broken_client)

    assert service.clean("ruwe tekst") == "ruwe tekst"
