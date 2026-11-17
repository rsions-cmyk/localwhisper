from __future__ import annotations

from collections.abc import Callable

from praattypen.config import CleanupConfig


CleanupClient = Callable[[str, str, float], str]


class CleanupService:
    def __init__(
        self,
        config: CleanupConfig,
        client: CleanupClient | None = None,
        timeout: float = 12.0,
    ) -> None:
        self.config = config
        self.client = client or ollama_generate
        self.timeout = timeout

    def clean(self, text: str) -> str:
        if not self.config.enabled or self.config.provider == "off":
            return text
        if self.config.provider != "ollama":
            return text

        prompt = build_cleanup_prompt(text, self.config.remove_fillers)
        try:
            cleaned = self.client(prompt, self.config.model, self.timeout).strip()
        except Exception:
            return text
        return cleaned or text


def build_cleanup_prompt(text: str, remove_fillers: bool = True) -> str:
    filler_instruction = "verwijder stopwoorden (uh, eh), " if remove_fillers else ""
    return (
        "Corrigeer interpunctie en hoofdletters, "
        f"{filler_instruction}"
        "Verander de betekenis of woordkeuze niet. "
        "Geef alleen de gecorrigeerde tekst terug.\n\n"
        f"Tekst:\n{text}"
    )


def ollama_generate(
    prompt: str,
    model: str,
    timeout: float,
    think: bool | None = None,
    keep_alive: str | None = None,
) -> str:
    import requests

    payload: dict = {"model": model, "prompt": prompt, "stream": False}
    if think is not None:
        # Reasoning models (gemma4, qwen3) otherwise write ~4x more tokens of
        # hidden "thinking" first, which is pure waiting time for dictation.
        payload["think"] = think
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive
    response = requests.post(
        "http://127.0.0.1:11434/api/generate",
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return str(data.get("response", ""))
