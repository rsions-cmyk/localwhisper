import pytest

from praattypen.modes import ModeState, ModeTransformer
from praattypen.voice_commands import parse


@pytest.mark.parametrize(
    "spoken, kind, argument, body",
    [
        ("Translate to English: ik kom morgen later.", "translate", "English", "Ik kom morgen later."),
        ("Translate to English ik kom morgen later", "translate", "English", "Ik kom morgen later"),
        ("Vertaal naar het Engels. Ik kom later.", "translate", "English", "Ik kom later."),
        ("Translate this into Dutch, see you tomorrow.", "translate", "Dutch", "See you tomorrow."),
        ("Vertaal naar het Duits: tot morgen", "translate", "German", "Tot morgen"),
        ("In English: tot morgen.", "translate", "English", "Tot morgen."),
        ("Translate to English.", "translate", "English", ""),
        ("Feedback: goede structuur, bronnen zwak.", "feedback", "", "Goede structuur, bronnen zwak."),
        ("Formal: can't make it Thursday.", "style", "formal", "Can't make it Thursday."),
        ("Formeel. Ik kan donderdag niet.", "style", "formal", "Ik kan donderdag niet."),
        ("Email: I'll be late.", "style", "_EMAIL", "I'll be late."),
        ("In a cheerful tone: we won.", "style", "cheerful", "We won."),
        ("In een vriendelijke toon: we hebben gewonnen.", "style", "vriendelijke", "We hebben gewonnen."),
        ("AirQuote, write a polite reply.", "instruct", "", "Write a polite reply."),
        ("Air quote, schrijf een bedankje.", "instruct", "", "Schrijf een bedankje."),
    ],
)
def test_parse_recognises_commands(spoken, kind, argument, body) -> None:
    command = parse(spoken)
    assert command is not None
    if argument == "_EMAIL":
        assert command.argument.startswith("a short, complete email")
        argument = command.argument
    assert (command.kind, command.argument, command.body) == (kind, argument, body)


@pytest.mark.parametrize(
    "spoken",
    [
        "Ik kom morgen later.",
        "Formal education matters a lot.",  # no pause after the word
        "Feedback is important for students.",
        "The translation to English was poor.",
        "Please read the feedback below.",
        "Email John about the meeting.",
        "",
    ],
)
def test_ordinary_dictation_is_not_a_command(spoken) -> None:
    assert parse(spoken) is None


def test_rewrite_words_need_a_selection() -> None:
    command = parse("Make this more academic.")
    assert command.kind == "instruct" and command.needs_target
    assert command.body == "Make this more academic."


def _transformer(tmp_path, reply="done", commands=True):
    state = ModeState(path=tmp_path / "mode.txt")
    state.set_commands(commands)
    prompts = []
    transformer = ModeTransformer(state, generate=lambda p, _m, _t: prompts.append(p) or reply)
    return transformer, prompts


def test_commands_switch_persists_and_defaults_off(tmp_path) -> None:
    assert not ModeState(path=tmp_path / "mode.txt").commands_enabled
    ModeState(path=tmp_path / "mode.txt").set_commands(True)
    assert ModeState(path=tmp_path / "mode.txt").commands_enabled


def test_commands_off_dictates_the_words(tmp_path) -> None:
    transformer, prompts = _transformer(tmp_path, commands=False)
    assert transformer.transform("Translate to English: tot morgen") == "Translate to English: tot morgen"
    assert not prompts


def test_translate_command_translates_the_rest(tmp_path) -> None:
    transformer, prompts = _transformer(tmp_path, reply="See you tomorrow")
    working = []
    transformer.on_command = working.append
    assert transformer.transform("Translate to English: tot morgen") == "See you tomorrow"
    assert "English" in prompts[0] and "Tot morgen" in prompts[0]
    assert transformer.last_command == "→ English"
    assert working == ["Translating…"]


def test_bare_command_works_on_selected_text(tmp_path) -> None:
    transformer, prompts = _transformer(tmp_path)
    transformer.selection = transformer.context = "De resultaten zijn goed."
    assert transformer.transform("Formal.") == "done"
    assert "De resultaten zijn goed." in prompts[0]


def test_bare_command_without_anything_selected_dictates(tmp_path) -> None:
    transformer, prompts = _transformer(tmp_path)
    assert transformer.transform("Translate to English.") == "Translate to English."
    assert not prompts and transformer.last_command == ""


def test_make_this_without_selection_is_plain_dictation(tmp_path) -> None:
    transformer, prompts = _transformer(tmp_path)
    # Only freshly copied text, no selection: still just a sentence.
    transformer.context = "copied text"
    assert transformer.transform("Make it clear to the students.") == "Make it clear to the students."
    assert not prompts
    transformer.selection = transformer.context = "Selected text."
    assert transformer.transform("Make this shorter.") == "done"
    assert "Selected text." in prompts[0]


def test_command_overrides_mode_and_falls_back_when_ai_is_off(tmp_path) -> None:
    state = ModeState(path=tmp_path / "mode.txt")
    state.set("feedback")
    state.set_commands(True)

    def broken(_prompt, _model, _timeout):
        raise ConnectionError("ollama down")

    transformer = ModeTransformer(state, generate=broken)
    assert transformer.transform("Formal: hoi") == "Formal: hoi"
    assert transformer.last_failed


def test_translate_with_extra_instructions_is_one_instruction(tmp_path) -> None:
    spoken = "translate this to English and make it a very commercial marketing like text"
    command = parse(spoken)
    assert command.kind == "instruct" and command.label == "→ English"
    assert command.body == spoken
    transformer, prompts = _transformer(tmp_path)
    transformer.selection = transformer.context = "Onze nieuwe app helpt toeristen."
    assert transformer.transform(spoken) == "done"
    assert spoken in prompts[0] and "Onze nieuwe app helpt toeristen." in prompts[0]
