from praattypen.modes import ModeState, ModeTransformer, strip_thinking
from praattypen.vocabulary import apply_casing, build_prompt, parse_terms


def test_mode_state_persists_and_rejects_unknown(tmp_path) -> None:
    path = tmp_path / "mode.txt"
    state = ModeState(path=path)
    assert state.current.key == "dictate"
    state.set("feedback")
    state.set("nonsense")
    assert ModeState(path=path).current.key == "feedback"


def test_whisper_translates_only_in_english_mode(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    assert state.whisper_task() == "transcribe"
    state.set("to_english")
    assert state.whisper_task() == "translate"


def test_transformer_falls_back_to_raw_text_when_ollama_fails(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    state.set("feedback")

    def broken(_prompt, _model, _timeout):
        raise ConnectionError("ollama down")

    assert ModeTransformer(state, generate=broken).transform("goed werk") == "goed werk"


def test_transformer_strips_reasoning_and_passes_dictation_through(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    calls = []
    transformer = ModeTransformer(
        state, generate=lambda prompt, _m, _t: calls.append(prompt) or "<think>x</think>Hallo"
    )
    assert transformer.transform("hello") == "hello"
    assert not calls
    state.set("to_dutch")
    assert transformer.transform("hello") == "Hallo"
    assert strip_thinking("<think>a</think>b<think>c</think>d") == "bd"


def test_rewrite_prompt_includes_captured_context(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    state.set("rewrite")
    prompts = []
    transformer = ModeTransformer(state, generate=lambda p, _m, _t: prompts.append(p) or "done")
    transformer.context = "De resultaten zijn goed."
    assert transformer.transform("maak dit academischer") == "done"
    assert "De resultaten zijn goed." in prompts[0]
    assert "maak dit academischer" in prompts[0]


def test_vocabulary_parsing_prompt_and_casing() -> None:
    terms = parse_terms("# comment\nBUas\nPraatTypen, ILO\nbuas\n")
    assert terms == ["BUas", "PraatTypen", "ILO"]
    assert build_prompt(terms) == "BUas, PraatTypen, ILO."
    assert build_prompt([]) is None
    assert apply_casing("bij buas en de ilo's", terms) == "bij BUas en de ILO's"
    assert apply_casing("busas", terms) == "busas"


def test_word_accuracy_counts_word_errors_ignoring_case_and_punctuation() -> None:
    from praattypen.benchmark import word_accuracy

    assert word_accuracy("Morgen is het feest.", "morgen is het feest") == 1.0
    assert word_accuracy("one two three four", "one too three four") == 0.75
    assert word_accuracy("one two", "") == 0.0


def test_rewrite_without_selection_or_fresh_copy_just_dictates(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    state.set("rewrite")
    calls = []
    transformer = ModeTransformer(state, generate=lambda p, _m, _t: calls.append(p) or "rewritten")
    transformer.context = ""
    assert transformer.transform("check if it works now") == "check if it works now"
    assert not calls
