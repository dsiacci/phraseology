"""Turn a transcript into tokens the checker can compare.

Whisper writes the same radio call in many ways: "Runway 0-9",
"runway zero niner", "QNH 9 and 9 are 8". The checker never looks at
the raw text. It looks at tokens:

* single digits: "1", "0", "1", "3"
* single upper-case letters for spelled letters: "F", "C", "D"
* lower-case words, with a few phrases glued into one token
  ("takeoff", "touchandgo", "holdingpoint", "lineup", "insight")
"""

from __future__ import annotations

import re

from .icao import ALPHABET

LETTER_WORDS = {name.lower().replace("-", ""): letter
                for letter, name in ALPHABET.items()}
LETTER_WORDS.update({
    "alfa": "A", "charly": "C", "juliet": "J", "whisky": "W",
    "xray": "X", "foxtrott": "F",
})

DIGIT_WORDS = {
    "zero": "0", "one": "1", "wun": "1", "two": "2", "three": "3",
    "tree": "3", "four": "4", "fower": "4", "five": "5", "fife": "5",
    "six": "6", "seven": "7", "eight": "8", "ait": "8", "nine": "9",
    "niner": "9", "nina": "9", "minor": "9", "nyner": "9",
}

TEENS = {
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19",
}
TENS = {
    "twenty": "2", "thirty": "3", "forty": "4", "fifty": "5",
    "sixty": "6", "seventy": "7", "eighty": "8", "ninety": "9",
}

# All-caps words Whisper writes that are not spelled registrations.
ACRONYMS = {"QNH", "QFE", "VFR", "IFR", "ATC", "ATIS", "POB", "OK", "TV",
            "AM", "PM", "UK", "US", "HPA"}

FILLERS = {"uh", "um", "umm", "erm", "ah", "hmm", "mm"}

# Phrases glued into one token, applied to the raw text before splitting.
PHRASES = [
    (r"\bfox[\s-]+trot\b", "foxtrot"),
    (r"\bx[\s-]+ray\b", "xray"),
    (r"\btake[\s-]*offs?\b", "takeoff"),
    (r"\btouch[\s-]*(?:and|&|n|an)[\s-]*go(?:es)?\b", "touchandgo"),
    (r"\btouch[\s-]*n[\s-]*go\b", "touchandgo"),
    # "touch, seven, go": one misheard word between "touch" and "go".
    (r"\btouch[\s,-]+[a-z]{1,6}[\s,-]+go(?:es)?\b", "touchandgo"),
    # "touch and..." with the last word swallowed: nothing else starts so.
    (r"\btouch[\s-]+(?:and|&)\b(?![\s-]*go)", "touchandgo"),
    (r"\bline[\s-]*up\b", "lineup"),
    (r"\bhold(?:ing)?[\s-]*points?\b", "holdingpoint"),
    (r"\bin[\s-]*sight\b", "insight"),
    (r"\bfull[\s-]*stop\b", "fullstop"),
    (r"\bright[\s-]*hand(?:ed)?\b", "righthand"),
    (r"\bleft[\s-]*hand(?:ed)?\b", "lefthand"),
    (r"\bhecto[\s-]*pascals?\b", "hectopascals"),
    (r"\bmilli[\s-]*bars?\b", "hectopascals"),
    (r"\bq\s*(?:and|n|en)\s*h\b", "QNH"),
    (r"\bq\.?\s*n\.?\s*h\.?", "QNH"),
    (r"\bq\.?\s*f\.?\s*e\.?", "QFE"),
    (r"\bsay[\s-]+again\b", "sayagain"),
    (r"\bwill[\s-]*co\b", "wilco"),
    (r"\bwilko\b", "wilco"),
    (r"\bsquawking\b|\bsquak\b|\bsqwak\b|\bsquark\b|\bsquawks\b", "squawk"),
    (r"\bdown[\s-]+wind\b", "downwind"),
    (r"(\d)\s*(?:point|decimal)\s*(\d)", r"\1 decimal \2"),
    (r"(\d)\.(\d)", r"\1 decimal \2"),
]

SYNONYMS = {
    # what "via" becomes in the transcript now and then
    "wire": "via", "vier": "via", "vya": "via", "fia": "via", "viya": "via",
    "clear": "cleared",
    "hectopascals": "hpa",
    "hpa": "hpa",
    "qnh": "qnh",
    "qfe": "qfe",
    "landing": "land",
    "downwinds": "downwind",
    "vacating": "vacate",
    "holding": "holding",
}

TOKEN_RE = re.compile(r"[A-Za-z]+|\d+")


def _expand_word(word: str) -> list[str]:
    """Map one alphabetic token to zero or more canonical tokens."""
    low = word.lower()
    if low in LETTER_WORDS:
        return [LETTER_WORDS[low]]
    if word.isupper() and word not in ACRONYMS and len(word) <= 6:
        # "ABCD" from "F-ABCD", "FCD", "B" from "via B". A lone "A" or "I"
        # is far more often an article or a pronoun than a letter.
        if len(word) > 1 or word not in ("A", "I"):
            return list(word)
    if low in FILLERS:
        return []
    if low in DIGIT_WORDS:
        return [DIGIT_WORDS[low]]
    if low in TEENS:
        return list(TEENS[low])
    return [SYNONYMS.get(low, low)]


def normalize(text: str) -> list[str]:
    """Return canonical tokens for a transcript or a typed message."""
    for pattern, repl in PHRASES:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    # Single upper-case letter glued to digits: "A1" -> "Alpha 1".
    text = re.sub(r"\b([A-Z])(\d{1,2})\b",
                  lambda m: f"{ALPHABET[m.group(1)]} {m.group(2)}", text)
    raw = TOKEN_RE.findall(text)

    tokens: list[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        if tok.isdigit():
            tokens.extend(tok)
        else:
            low = tok.lower()
            if low in TENS:
                tokens.append(TENS[low])
                nxt = raw[i + 1].lower() if i + 1 < len(raw) else ""
                if DIGIT_WORDS.get(nxt, "0") != "0" and nxt not in TEENS:
                    tokens.append(DIGIT_WORDS[nxt])
                    i += 1
                else:
                    tokens.append("0")
            else:
                tokens.extend(_expand_word(tok))
        i += 1
    return _fix_homophones(_fix_niner(tokens))


# Words Whisper writes for a spoken digit ("squawk for 107"). They become
# digits only between a number keyword or a digit and another digit, so
# "cleared for take-off" stays as it is.
HOMOPHONES = {"for": "4", "fore": "4", "to": "2", "too": "2", "oh": "0",
              "won": "1", "ate": "8"}
NUMBER_KEYWORDS = {"squawk", "qnh", "qfe", "runway", "number", "decimal",
                   "ground", "tower", "wind"}


def _fix_homophones(tokens: list[str]) -> list[str]:
    out = list(tokens)
    for i, tok in enumerate(out):
        if tok == "point" and 0 < i < len(out) - 1 and is_digit(out[i - 1]) and is_digit(out[i + 1]):
            out[i] = "decimal"
            continue
        if tok not in HOMOPHONES or i + 1 >= len(out):
            continue
        prev = out[i - 1] if i > 0 else ""
        nxt = out[i + 1]
        if (is_digit(prev) or prev in NUMBER_KEYWORDS) and (is_digit(nxt) or (nxt == "hpa" and is_digit(prev))):
            out[i] = HOMOPHONES[tok]
    return out


def _fix_niner(tokens: list[str]) -> list[str]:
    """Undo what "niner" turns into: "9-R", "9er", "9 and 9 are 8"."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        out.append(tok)
        if tok == "9" and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            after = tokens[i + 2] if i + 2 < len(tokens) else ""
            if nxt in ("R", "r", "er", "ner", "nah"):
                i += 2
                continue
            if nxt in ("and", "are", "or", "a") and (after.isdigit() or after in HOMOPHONES):
                i += 2
                continue
        i += 1
    return out


def is_letter(tok: str) -> bool:
    return len(tok) == 1 and tok.isalpha() and tok.isupper()


def is_digit(tok: str) -> bool:
    return len(tok) == 1 and tok.isdigit()


def letter_runs(tokens: list[str]) -> list[tuple[int, int]]:
    """(start, end) spans of consecutive spelled letters."""
    runs = []
    i = 0
    while i < len(tokens):
        if is_letter(tokens[i]):
            j = i
            while j < len(tokens) and is_letter(tokens[j]):
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1
    return runs
