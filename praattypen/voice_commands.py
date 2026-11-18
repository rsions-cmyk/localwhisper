"""Spoken commands: a few words at the start of a dictation that say what to do.

    "Translate to English: ik kom morgen iets later"   -> I'll be a bit late tomorrow
    "Formal: can't make Thursday, how about Friday"     -> the same, formally
    "Feedback: good structure, sources are weak"        -> structured student feedback
    "Rewrite this more academically"   (text selected)  -> the selection, rewritten
    "AirQuote, write a polite reply saying I'll be late" -> whatever you asked for

Switched on or off in the window (Mode → Voice commands). Ordinary dictation
must never be swallowed by accident, so:

- only the very start of a dictation counts;
- one-word triggers ("Formal", "Feedback") only count when followed by a
  pause, which Whisper writes as a colon, full stop or comma;
- "Rewrite …" / "Make this …" only count while text is selected or freshly
  copied: without a target they are just a sentence you are dictating.

Whisper sometimes hears the Dutch and English triggers in the other language
("vertaal naar het Engels" for "translate to English"), so both are accepted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    kind: str  # "translate" | "feedback" | "style" | "instruct"
    label: str  # shown on the floating bar: "→ English", "Formal", …
    body: str  # what was said after the trigger
    argument: str = ""  # target language (translate) or style (style)
    needs_target: bool = False  # instruct: only with selected/copied text


# Language names as Whisper writes them, English or Dutch, -> English name.
LANGUAGES = {
    "english": "English", "engels": "English",
    "dutch": "Dutch", "nederlands": "Dutch",
    "german": "German", "duits": "German",
    "french": "French", "frans": "French",
    "spanish": "Spanish", "spaans": "Spanish",
    "italian": "Italian", "italiaans": "Italian",
    "portuguese": "Portuguese", "portugees": "Portuguese",
}
SHORT_LANGUAGE = {"English": "→ English", "Dutch": "→ Dutch"}

_EMAIL = (
    "a short, complete email: a greeting, the message in clear full sentences, and a "
    "polite closing line, without a name or signature"
)

# One-word styles -> how the AI model is asked to write.
STYLES = {
    "formal": "formal", "formally": "formal", "formeel": "formal",
    "informal": "informal, relaxed", "casual": "informal, relaxed", "informeel": "informal, relaxed",
    "academic": "academic", "academically": "academic", "academisch": "academic",
    "friendly": "warm and friendly", "vriendelijk": "warm and friendly",
    "polite": "polite", "beleefd": "polite",
    "professional": "professional", "professioneel": "professional", "zakelijk": "professional, businesslike",
    "shorter": "shorter and more concise", "concise": "shorter and more concise",
    "korter": "shorter and more concise", "beknopt": "shorter and more concise",
    "email": _EMAIL, "e-mail": _EMAIL, "mail": _EMAIL,
}

_LANG = "|".join(sorted(LANGUAGES, key=len, reverse=True))
_STYLE = "|".join(sorted((re.escape(word) for word in STYLES), key=len, reverse=True))

# A pause after the trigger: Whisper's punctuation, possibly with spaces.
_PAUSE = r"\s*[:.,;!?—–-]+\s*"
_END = r"(?:" + _PAUSE + r"|\s+|$)"

_TRANSLATE = re.compile(
    r"^(?:please\s+|graag\s+)?"
    r"(?:translate|translation|vertaal|vertaling)\s+"
    r"(?:this\s+|it\s+|that\s+|dit\s+|het\s+|dat\s+)?"
    r"(?:to|into|in|naar|in\s+het|naar\s+het)\s+(?:het\s+)?"
    rf"(?P<lang>{_LANG})\b" + _END,
    re.IGNORECASE,
)
# "In English: …" / "In het Engels: …" only with a pause after it.
_IN_LANGUAGE = re.compile(
    rf"^(?:in|naar)\s+(?:het\s+)?(?P<lang>{_LANG})" + _PAUSE, re.IGNORECASE
)
_FEEDBACK = re.compile(
    r"^(?:student\s+|studenten)?feedback"
    r"(?:\s+(?:for|voor)\s+(?:the\s+|de\s+|a\s+|een\s+)?student)?" + _PAUSE,
    re.IGNORECASE,
)
_STYLE_WORD = re.compile(rf"^(?:as\s+an?\s+|als\s+)?(?P<style>{_STYLE})" + _PAUSE, re.IGNORECASE)
# "In a formal tone: …", "In een vriendelijke toon: …"
_STYLE_PHRASE = re.compile(
    r"^in\s+(?:a|an|een)\s+(?P<style>[\w\s-]{2,40}?)\s+"
    r"(?:tone|style|way|register|toon|stijl|manier)" + _PAUSE,
    re.IGNORECASE,
)
# Commands about the selected text: the whole sentence is the instruction.
_REWRITE = re.compile(
    r"^(?:please\s+)?(?:rewrite|rephrase|reword|shorten|summari[sz]e|"
    r"make\s+(?:this|it|that)|turn\s+(?:this|it)|"
    r"herschrijf|herformuleer|vat\s+(?:dit|het)|maak\s+(?:dit|het|daar)|"
    r"zet\s+(?:dit|het))\b",
    re.IGNORECASE,
)
# "… and make it shorter": the translation comes with more instructions.
_MORE = re.compile(r"^\s*(?:and|en|but|maar|then|dan)\b", re.IGNORECASE)
# "AirQuote, …": anything you ask.
_ADDRESS = re.compile(r"^(?:hey\s+|hé\s+|hee\s+)?air[\s-]?quotes?\b" + _END, re.IGNORECASE)


def parse(text: str) -> Command | None:
    """The command at the start of a dictation, or None for plain dictation."""
    spoken = text.strip().lstrip("\"'“‘")
    if not spoken:
        return None

    match = _TRANSLATE.match(spoken) or _IN_LANGUAGE.match(spoken)
    if match:
        language = LANGUAGES[match.group("lang").lower()]
        label = SHORT_LANGUAGE.get(language, f"→ {language}")
        rest = spoken[match.end():]
        if _MORE.match(rest):
            # "Translate this to English and make it more commercial": one
            # instruction for the AI model, not text to translate.
            return Command("instruct", label, spoken)
        return Command("translate", label, _rest(spoken, match), argument=language)

    match = _FEEDBACK.match(spoken)
    if match:
        return Command("feedback", "Feedback", _rest(spoken, match))

    match = _STYLE_WORD.match(spoken)
    if match:
        word = match.group("style").lower()
        return Command("style", word.capitalize(), _rest(spoken, match), argument=STYLES[word])

    match = _STYLE_PHRASE.match(spoken)
    if match:
        style = match.group("style").strip()
        label = style.capitalize()
        if len(label) > 16:  # the floating bar has room for a word or two
            label = label[:15].rstrip() + "…"
        return Command("style", label, _rest(spoken, match), argument=style)

    match = _ADDRESS.match(spoken)
    if match:
        return Command("instruct", "Command", _rest(spoken, match))

    if _REWRITE.match(spoken):
        return Command("instruct", "Rewrite", spoken, needs_target=True)

    return None


def _rest(spoken: str, match: re.Match) -> str:
    rest = spoken[match.end():].strip()
    # "Translate to English: 'Ik kom later.'" -> drop the quotes you spoke.
    if len(rest) >= 2 and rest[0] in "\"'“‘" and rest[-1] in "\"'”’":
        rest = rest[1:-1].strip()
    return rest[:1].upper() + rest[1:] if rest else rest


def style_prompt(text: str, style: str) -> str:
    return (
        f"Rewrite the following dictated text as {style_phrase(style)}. Keep its "
        "language (Dutch stays Dutch, English stays English), its meaning, and all "
        "names, facts and references. Fix dictation errors. Do not add content. "
        "Reply with the resulting text only, ready to paste: no preamble, no quotes, "
        "no Markdown.\n\n"
        f"Text:\n{text}"
    )


def style_phrase(style: str) -> str:
    if style.startswith(("a ", "an ")):
        return style
    return f"text in a {style} style"
