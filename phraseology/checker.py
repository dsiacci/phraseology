"""Rule-based readback checker. No model decides anything here.

The rules, in our own words (legal basis in Europe: SERA.8015(e),
Commission Implementing Regulation (EU) No 923/2012):

* Always read back: the runway in use; clearances and instructions to
  enter, land on, take off from, hold short of, cross or backtrack on a
  runway; altimeter settings; transponder codes; new frequencies.
* Other instructions, taxi instructions included, are read back or
  acknowledged so that it is clear they were understood and will be
  complied with. This game asks for taxi instructions in full, as the
  UK and many aerodromes do.
* A readback ends with the aircraft callsign. A call that starts a new
  exchange begins with it.
* "Wilco" when you will comply with an instruction, "roger" when you
  have only received information; neither replaces a readback.
* A wrong readback gets "negative" from the tower, then the correct
  version, and you read it back again.

The output says what is missing or wrong. There is no score.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .normalize import is_digit, is_letter, letter_runs, normalize

# Why each kind of item has to be read back, shown in the feedback.
REASONS = {
    "runway": "the runway in use is always read back",
    "qnh": "altimeter settings are always read back",
    "squawk": "transponder codes are always read back",
    "holding_point": "this game asks for taxi instructions in full",
    "via": "this game asks for taxi instructions in full",
    "taxi_limit": "this game asks for taxi instructions in full",
    "frequency": "frequency changes are read back",
    "clearance": "runway clearances are always read back",
    "circuit": "this instruction is part of the clearance",
    "sequence": "confirm your number in the sequence",
    "traffic": "confirm you have the traffic in sight",
    "instruction": "confirm the instruction",
}


@dataclass(frozen=True)
class Item:
    """One thing the pilot has to say back (or say, for a report)."""

    kind: str
    value: str = ""
    label: str = ""
    display: str = ""
    spoken: str = ""
    words: tuple = ()
    conflicts: tuple = ()
    alternatives: tuple = ()
    station: str = ""
    ask: str = ""
    optional: bool = False


@dataclass
class Finding:
    level: str  # "error" or "note"
    code: str
    text: str
    item: Item | None = None


@dataclass
class Expectation:
    """What the checker expects from the pilot's next transmission.

    kind: "readback" (items read back, callsign last), "ack" (wilco or
    roger, callsign last) or "report" (pilot starts the call, callsign
    first).
    """

    kind: str
    items: list[Item] = field(default_factory=list)
    full_callsign_required: bool = False
    station: str = ""  # "ground" or "tower" when the call names a station
    new_station: bool = False


@dataclass
class Result:
    transcript: str
    tokens: list[str]
    findings: list[Finding] = field(default_factory=list)
    wrong: list[Item] = field(default_factory=list)
    missing: list[Item] = field(default_factory=list)
    callsign: str | None = None  # "full", "short", "wrong" or None
    say_again: bool = False

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors and not self.say_again


# ---------------------------------------------------------------- matching

def _digits_after(tokens: list[str], start: int, count: int,
                  allow_decimal: bool = False) -> str:
    out = ""
    i = start
    while i < len(tokens) and len(out) < count:
        if is_digit(tokens[i]):
            out += tokens[i]
        elif allow_decimal and tokens[i] == "decimal":
            pass
        else:
            break
        i += 1
    return out


def _digit_groups(tokens: list[str]) -> list[tuple[int, str]]:
    groups = []
    i = 0
    while i < len(tokens):
        if is_digit(tokens[i]):
            j = i
            while j < len(tokens) and is_digit(tokens[j]):
                j += 1
            groups.append((i, "".join(tokens[i:j])))
            i = j
        else:
            i += 1
    return groups


def find_phrase(tokens: list[str], words: tuple, start: int = 0,
                max_gap: int = 3) -> int:
    """Index of the first word of `words` found in order, or -1."""
    if not words:
        return -1
    for i in range(start, len(tokens)):
        if tokens[i] != words[0]:
            continue
        pos = i
        ok = True
        for w in words[1:]:
            window = tokens[pos + 1: pos + 2 + max_gap]
            if w in window:
                pos = pos + 1 + window.index(w)
            else:
                ok = False
                break
        if ok:
            return i
    return -1


def _undo_nine_oh(run: str) -> str:
    """'Niner niner eight' heard as '9098' or '90908'. No altimeter setting
    reads above 1100 hPa, and 'niner' sounds like 'nine-oh', so drop the
    zeros that follow a nine."""
    if run and int(run) > 1100 and "90" in run:
        return run.replace("90", "9")
    return run


def _match_number(tokens, keywords, expected, conflicts=(), fix=None):
    """Keyword followed by digits: runway 27, QNH 1013, squawk 4521."""
    said = None
    for i, tok in enumerate(tokens):
        if tok in keywords or tok in conflicts:
            digits = _digits_after(tokens, i + 1, len(expected))
            if not digits:
                continue
            if tok in conflicts:
                return "wrong", f"{tok.upper()} {digits}"
            if digits == expected:
                return "ok", digits
            if fix and fix(_digits_after(tokens, i + 1, len(expected) + 2)) == expected:
                return "ok", expected
            # Show what was said in full: "QNH 1008", not "QNH 100".
            said = _digits_after(tokens, i + 1, 4) if fix else digits
    if said is not None:
        return "wrong", said
    for _, group in _digit_groups(tokens):
        if group == expected:
            return "ok-loose", group
    return "missing", None


def _match_designator(tokens, keywords, expected, callsigns=()):
    """Keyword followed by a letter and digits: holding point A1, via B."""
    letters = [c for c in expected if c.isalpha()]
    digits = "".join(c for c in expected if c.isdigit())
    for i, tok in enumerate(tokens):
        if tok not in keywords:
            continue
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if not is_letter(nxt):
            continue
        said_digits = _digits_after(tokens, i + 2, max(len(digits), 1)) if digits else ""
        said = nxt + said_digits
        if nxt == letters[0] and said_digits == digits:
            return "ok", said
        return "wrong", said
    for i, tok in enumerate(tokens):
        if tok == letters[0] and digits and _digits_after(tokens, i + 1, len(digits)) == digits:
            return "ok-loose", expected
    if not digits:
        # A lone taxiway letter, once the callsign's letters are set aside
        # ("vacate right, wire Charlie, Ground ..., Foxtrot Charlie Delta").
        for start, end in letter_runs(tokens):
            run = "".join(tokens[start:end])
            for cs in callsigns:
                run = run.replace(cs, "", 1) if cs and cs in run else run
            if letters[0] in run and len(run) <= 2:
                return "ok-loose", expected
    return "missing", None


def _match_frequency(tokens, expected):
    want = expected.replace(".", "")
    best = None
    for i, tok in enumerate(tokens):
        if not is_digit(tok) or (i > 0 and (is_digit(tokens[i - 1]) or tokens[i - 1] == "decimal")):
            continue
        said = _digits_after(tokens, i, 6, allow_decimal=True)
        whole = _digits_after(tokens, i, 12, allow_decimal=True)
        if said == want or (want in whole and len(whole) <= len(want) + 1):
            # A doubled digit ("1118 decimal 505") is a transcription slip;
            # a different digit is not.
            return "ok", expected
        if len(said) >= 5 and said.startswith("1"):
            best = said[:3] + "." + said[3:]
    if best:
        return "wrong", best
    return "missing", None


def match_item(tokens: list[str], item: Item, callsigns=()) -> tuple[str, str | None]:
    """Return ("ok" | "ok-loose" | "wrong" | "missing", what was said)."""
    k = item.kind
    if k == "runway":
        return _match_number(tokens, ("runway",), item.value)
    if k == "qnh":
        setting = item.words[0] if item.words else "qnh"
        other = "qfe" if setting == "qnh" else "qnh"
        return _match_number(tokens, (setting,), item.value, (other,), fix=_undo_nine_oh)
    if k == "squawk":
        return _match_number(tokens, ("squawk",), item.value)
    if k == "holding_point":
        return _match_designator(tokens, ("holdingpoint", "point"), item.value)
    if k == "via":
        return _match_designator(tokens, ("via", "taxiway"), item.value, callsigns)
    if k == "frequency":
        return _match_frequency(tokens, item.value)
    if k == "sequence":
        return _match_number(tokens, ("number",), item.value)
    # Phrase items: clearances, instructions, report words. A conflict is
    # a phrase that means something else ("cleared to land" when the
    # tower said "cleared touch and go").
    for words, display in item.conflicts:
        if find_phrase(tokens, words) >= 0 and find_phrase(tokens, item.words) < 0:
            return "wrong", display
    if item.words and find_phrase(tokens, item.words) >= 0:
        return "ok", item.value
    for alt in item.alternatives:
        if find_phrase(tokens, alt) >= 0:
            return "ok", item.value
    return "missing", None


# ---------------------------------------------------------------- callsign

def find_callsign(tokens: list[str], full_letters: str, short_letters: str):
    """Locate the callsign. Returns (form, start, end, said)."""
    stream = []
    for start, end in letter_runs(tokens):
        run = "".join(tokens[start:end])
        for form, letters in (("full", full_letters), ("short", short_letters)):
            pos = run.find(letters)
            if pos >= 0:
                stream.append((form, start + pos, start + pos + len(letters), letters))
    if stream:
        # Prefer the last occurrence: a readback ends with the callsign.
        stream.sort(key=lambda s: (s[2], s[0] == "full"))
        return stream[-1]
    for start, end in letter_runs(tokens):
        run = "".join(tokens[start:end])
        if len(run) >= 3 and run[0] == full_letters[0]:
            return ("wrong", start, end, run)
    return (None, -1, -1, None)


TRAILING_OK = {"over", "thanks", "thank", "you", "cheers", "bye", "good", "day"}


def _is_station_name(tok: str, aerodrome: str) -> bool:
    if tok in ("ground", "tower"):
        return True
    return difflib.SequenceMatcher(None, tok, aerodrome.lower()).ratio() >= 0.6


# ---------------------------------------------------------------- check

@dataclass
class Context:
    callsign_full: str      # "FABCD"
    callsign_short: str     # "FCD"
    display_full: str       # "F-ABCD"
    display_short: str      # "F-CD"
    aerodrome: str = "Isola"


def _fmt(letters: str) -> str:
    return f"{letters[0]}-{letters[1:]}" if len(letters) > 1 else letters


def check(expect: Expectation, transcript: str, ctx: Context) -> Result:
    tokens = normalize(transcript)
    res = Result(transcript=transcript, tokens=tokens)

    if not tokens:
        res.say_again = True
        res.findings.append(Finding("error", "empty", "Nothing was heard. Hold the key while you speak."))
        return res
    if "sayagain" in tokens or ("repeat" in tokens and len(tokens) <= 6):
        res.say_again = True
        if "repeat" in tokens and "sayagain" not in tokens:
            res.findings.append(Finding("note", "repeat", "On the radio, ask for \"say again\", not \"repeat\"."))
        return res

    # Callsign: present, right one, right form, right place.
    form, start, end, said = find_callsign(tokens, ctx.callsign_full, ctx.callsign_short)
    res.callsign = form
    if form is None:
        res.findings.append(Finding("error", "callsign_missing",
                                    "Your callsign is missing." +
                                    (" A readback always ends with it." if expect.kind != "report"
                                     else " Start the call with it.")))
    elif form == "wrong":
        yours = ctx.display_full if expect.full_callsign_required else ctx.display_short
        res.findings.append(Finding("error", "callsign_wrong",
                                    f"Callsign: you said {_fmt(said)}; yours is {yours}."))
    else:
        if form == "short" and expect.full_callsign_required:
            res.findings.append(Finding("note", "callsign_short_early",
                                        f"Use your full callsign, {ctx.display_full}, until the station shortens it."))
        if expect.kind in ("readback", "ack"):
            trailing = [t for t in tokens[end:] if t not in TRAILING_OK]
            if trailing:
                mine = ctx.display_full if expect.full_callsign_required else ctx.display_short
                res.findings.append(Finding("note", "callsign_position",
                                            f"Put your callsign at the end of a readback: \"…, {mine}\"."))
        else:
            before = [t for t in tokens[:start]
                      if not _is_station_name(t, ctx.aerodrome) and t not in ("hello", "good", "morning", "afternoon")]
            if before:
                mine = ctx.display_full if expect.full_callsign_required else ctx.display_short
                res.findings.append(Finding("note", "callsign_position",
                                            f"When you start a call, say your callsign first: \"{mine}, …\"."))

    if expect.kind == "report" and expect.station:
        if expect.station not in tokens:
            res.findings.append(Finding("note", "station_missing",
                                        f"On first contact, name the station: \"{ctx.aerodrome} {expect.station.capitalize()}\"."))

    # Items.
    readback_items = [i for i in expect.items if not i.optional]
    acknowledged = any(t in tokens for t in ("roger", "wilco"))
    for item in expect.items:
        status, said_value = match_item(tokens, item, (ctx.callsign_full, ctx.callsign_short))
        if status in ("ok", "ok-loose"):
            if status == "ok-loose" and item.kind == "qnh":
                res.findings.append(Finding("note", "qnh_word", f"Say \"QNH\" before the value: \"{item.display}\"."))
            if item.kind == "qnh" and int(item.value) < 1000 and "hpa" not in tokens:
                res.findings.append(Finding("note", "qnh_unit",
                                            f"Below 1000, say the unit too: \"{item.display}\"."))
            continue
        if item.optional:
            continue
        if expect.kind == "ack":
            res.findings.append(Finding("note", "ack_missing",
                                        f"Acknowledge an instruction with \"wilco, {ctx.display_short}\"."))
            continue
        if status == "wrong":
            res.wrong.append(item)
            res.findings.append(Finding("error", "wrong",
                                        f"{_cap(item.label)}: you said {_said(item, said_value)}, "
                                        f"the tower said {item.display}.", item))
        else:
            res.missing.append(item)
            if expect.kind == "report":
                text = f"Missing: {item.label}."
            else:
                reason = REASONS.get(item.kind, "it is part of the clearance")
                text = f"Missing: {item.display} ({reason})."
            res.findings.append(Finding("error", "missing", text, item))

    if expect.kind == "readback" and res.missing and len(res.missing) == len(readback_items) and acknowledged:
        res.findings.append(Finding("error", "roger_instead",
                                    "\"Roger\" or \"wilco\" only says you heard. This message has parts you must read back."))

    if expect.kind == "ack":
        if "roger" in tokens and "wilco" not in tokens and any(i.kind == "instruction" for i in expect.items):
            res.findings.append(Finding("note", "roger_not_wilco",
                                        "For an instruction, \"wilco\" (will comply) fits better than \"roger\" (received)."))
    return res


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _said(item: Item, said: str | None) -> str:
    if said is None:
        return "nothing"
    if item.kind == "runway":
        return f"runway {said}"
    if item.kind == "qnh":
        return said if not said[0].isdigit() else f"{(item.words[0] if item.words else 'qnh').upper()} {said}"
    if item.kind == "squawk":
        return f"squawk {said}"
    if item.kind == "holding_point":
        return f"holding point {said}"
    if item.kind == "via":
        return f"via {said}"
    if item.kind == "sequence":
        return f"number {said}"
    return f"\"{said}\""
