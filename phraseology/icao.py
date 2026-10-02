"""ICAO radiotelephony spelling: how letters, digits and values are spoken.

Every value the tower says exists in two forms: what the page shows
("runway 27") and what the voice says ("runway two seven").
"""

from __future__ import annotations

from dataclasses import dataclass

ALPHABET = {
    "A": "Alpha", "B": "Bravo", "C": "Charlie", "D": "Delta", "E": "Echo",
    "F": "Foxtrot", "G": "Golf", "H": "Hotel", "I": "India", "J": "Juliett",
    "K": "Kilo", "L": "Lima", "M": "Mike", "N": "November", "O": "Oscar",
    "P": "Papa", "Q": "Quebec", "R": "Romeo", "S": "Sierra", "T": "Tango",
    "U": "Uniform", "V": "Victor", "W": "Whiskey", "X": "X-ray",
    "Y": "Yankee", "Z": "Zulu",
}

# The tower voice says "niner", as most controllers do. Pilots may say
# "tree", "fife" or "niner"; the normalizer accepts all of them.
DIGITS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "niner",
}


def spell(letters: str) -> str:
    """'ABCD' -> 'Alpha Bravo Charlie Delta'."""
    return " ".join(ALPHABET[c] for c in letters.upper() if c in ALPHABET)


def say_digits(digits: str) -> str:
    """'1013' -> 'one zero one three'; '121.805' -> '... decimal ...'."""
    words = []
    for c in digits:
        if c == ".":
            words.append("decimal")
        elif c in DIGITS:
            words.append(DIGITS[c])
    return " ".join(words)


def say_designator(code: str) -> str:
    """'A1' -> 'Alpha One'; 'B' -> 'Bravo'."""
    out = []
    for c in code.upper():
        if c in ALPHABET:
            out.append(ALPHABET[c])
        elif c in DIGITS:
            out.append(DIGITS[c].capitalize())
    return " ".join(out)


@dataclass(frozen=True)
class Callsign:
    """An aircraft registration such as F-ABCD or G-ABCD.

    The abbreviated form keeps the first character and the last two
    letters (F-ABCD -> F-CD). A pilot may use it only once the station
    has used it first.
    """

    registration: str

    @property
    def letters(self) -> str:
        return self.registration.replace("-", "").upper()

    @property
    def short_letters(self) -> str:
        return self.letters[0] + self.letters[-2:]

    @property
    def full(self) -> str:
        return self.registration.upper()

    @property
    def short(self) -> str:
        return f"{self.letters[0]}-{self.letters[-2:]}"

    @property
    def spoken_full(self) -> str:
        return spell(self.letters)

    @property
    def spoken_short(self) -> str:
        return spell(self.short_letters)

    def display(self, abbreviated: bool) -> str:
        return self.short if abbreviated else self.full

    def spoken(self, abbreviated: bool) -> str:
        return self.spoken_short if abbreviated else self.spoken_full


def say_runway(rwy: str) -> str:
    return "runway " + say_digits(rwy)


def say_qnh(qnh: int, setting: str = "QNH") -> str:
    text = f"{setting} {say_digits(str(qnh))}"
    if qnh < 1000:
        text += " hectopascals"
    return text


def show_qnh(qnh: int, setting: str = "QNH") -> str:
    text = f"{setting} {qnh}"
    if qnh < 1000:
        text += " hectopascals"
    return text


def say_wind(direction: int, speed: int) -> str:
    if speed == 0:
        return "wind calm"
    return (f"wind {say_digits(f'{direction:03d}')} degrees, "
            f"{say_digits(str(speed))} knots")


def show_wind(direction: int, speed: int) -> str:
    if speed == 0:
        return "wind calm"
    return f"wind {direction:03d} degrees {speed} knots"


def say_frequency(freq: str) -> str:
    return say_digits(freq)
