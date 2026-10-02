"""A traffic circuit at a fictional aerodrome, as a deterministic state machine.

One game: taxi, line up, take off, a touch and go, a second circuit to
land, vacate, taxi back. Runway, wind, QNH, squawk and traffic are drawn
from a seeded random generator, so a seed always replays the same game.
Up to three surprises can change the plan.

Every message here is our own wording of standard ICAO phraseology,
with made-up data. Nothing comes from a recording or a manual's
examples.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from . import icao
from .checker import Context, Expectation, Item, Result, check
from .icao import ALPHABET, Callsign


@dataclass
class Aerodrome:
    name: str = "Isola"
    icao: str = "ZZZZ"
    elevation_ft: int = 240
    ground_freq: str = "121.805"
    tower_freq: str = "118.505"
    taxiway: str = "B"          # parallel taxiway
    exit_taxiway: str = "C"     # mid-runway exit
    parking: str = "the flying club"
    parking_words: tuple = ("club",)
    # Per runway: holding point, circuit direction, side of the exit.
    runways: dict = field(default_factory=lambda: {
        "27": {"holding_point": "A1", "circuit": "left", "vacate": "right", "wind": (200, 330)},
        "09": {"holding_point": "D1", "circuit": "right", "vacate": "left", "wind": (20, 150)},
    })


@dataclass
class Settings:
    callsign: str = "F-ABCD"
    traffic_callsign: str = "F-GXYZ"
    aerodrome: Aerodrome = field(default_factory=Aerodrome)
    altimeter: str = "QNH"          # or "QFE"
    surprises: bool = True
    traffic_types: tuple = ("Cherokee", "Cessna", "Robin")


@dataclass
class Transmission:
    speaker: str        # "ground", "tower", "traffic"
    text: str           # what the page shows
    spoken: str         # what the voice says
    to_pilot: bool = True


@dataclass
class Exchange:
    id: str
    hint: str
    expect: Expectation
    station: str                                  # who the pilot talks to
    tower: Transmission | None = None             # message the pilot answers
    chatter: list = field(default_factory=list)   # other traffic, before
    example: str = ""                             # one correct answer
    abbreviated: bool = False                     # station shortens our callsign


@dataclass
class Turn:
    result: Result | None
    transmissions: list
    example: str
    done: bool
    accepted: bool


def _join(parts):
    return ", ".join(p[0] for p in parts), ", ".join(p[1] for p in parts)


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


class Game:
    def __init__(self, settings: Settings | None = None, seed: int | None = None):
        self.settings = settings or Settings()
        self.seed = seed if seed is not None else random.randrange(1, 10**9)
        self.rng = random.Random(self.seed)
        self.cs = Callsign(self.settings.callsign)
        self.tcs = Callsign(self.settings.traffic_callsign)
        self.aero = self.settings.aerodrome
        self.ctx = Context(self.cs.letters, self.cs.short_letters,
                           self.cs.full, self.cs.short, self.aero.name)
        self._draw()
        self._abbr = {"ground": False, "tower": False}
        self.exchanges: list[Exchange] = []
        self._build()
        self.index = 0
        self.retry: Expectation | None = None
        self.last_to_pilot: Transmission | None = None
        self.done = False

    # ------------------------------------------------------------ the draw

    def _draw(self):
        rng = self.rng
        self.runway = rng.choice(sorted(self.aero.runways))
        lo, hi = self.aero.runways[self.runway]["wind"]
        self.wind_dir = rng.randrange(lo, hi + 1, 10)
        self.wind_speed = 0 if rng.random() < 0.1 else rng.randint(3, 13)
        self.qnh = rng.randint(994, 1032)
        alt = self.settings.altimeter.upper()
        self.altimeter_value = self.qnh if alt == "QNH" else self.qnh - round(self.aero.elevation_ft / 28)
        while True:
            code = str(rng.randint(1, 6)) + "".join(str(rng.randint(0, 7)) for _ in range(3))
            if code not in ("1200", "2000", "1000"):
                break
        self.squawk = code
        self.traffic_type = rng.choice(self.settings.traffic_types)
        self.surprise_slots = [None, None]
        self.unable_touch_and_go = False
        if self.settings.surprises:
            self.unable_touch_and_go = rng.random() < 0.35
            slots = 1 if self.unable_touch_and_go else 2
            for s in range(slots):
                self.surprise_slots[s] = rng.choice([None, "follow", "extend", "orbit"])
        self.layout = self.aero.runways[self.runway]

    def _wind(self):
        """Wind changes a little at each report."""
        if self.wind_speed == 0:
            return 0, 0
        lo, hi = self.aero.runways[self.runway]["wind"]
        d = min(hi, max(lo, self.wind_dir + self.rng.choice([-20, -10, 0, 0, 10, 20])))
        s = max(2, self.wind_speed + self.rng.choice([-2, -1, 0, 0, 1, 2]))
        return d, s

    def surprises(self) -> list[str]:
        out = [s for s in self.surprise_slots if s]
        if self.unable_touch_and_go:
            out.append("full stop")
        return out

    # ------------------------------------------------------------ items

    def _station(self, unit: str):
        name = f"{self.aero.name} {unit.capitalize()}"
        return (name, name)

    def _msg(self, station: str, parts, first_contact=False) -> Transmission:
        """A message from Ground or Tower to us.

        A station's first reply uses the full callsign; after that it
        shortens it, and from then on the pilot may shorten it too.
        """
        abbreviated = not first_contact
        cs = (self.cs.display(abbreviated), self.cs.spoken(abbreviated))
        head = [cs, self._station(station)] if first_contact else [cs]
        text, spoken = _join(head + list(parts))
        if abbreviated:
            self._abbr[station] = True
        return Transmission(station, text, spoken)

    def _traffic(self, text_parts, speaker="traffic"):
        text, spoken = _join(text_parts)
        return Transmission(speaker, text, spoken, to_pilot=False)

    @staticmethod
    def i_runway(rwy):
        return Item("runway", rwy, "runway", f"runway {rwy}", icao.say_runway(rwy))

    def i_altimeter(self):
        setting = self.settings.altimeter.upper()
        v = self.altimeter_value
        return Item("qnh", str(v), setting, icao.show_qnh(v, setting),
                    icao.say_qnh(v, setting), words=(setting.lower(),))

    def i_squawk(self):
        return Item("squawk", self.squawk, "squawk", f"squawk {self.squawk}",
                    f"squawk {icao.say_digits(self.squawk)}")

    @staticmethod
    def i_holding_point(hp):
        return Item("holding_point", hp, "holding point", f"holding point {hp}",
                    f"holding point {icao.say_designator(hp)}")

    @staticmethod
    def i_via(twy):
        return Item("via", twy, "taxi route", f"via {ALPHABET[twy]}", f"via {ALPHABET[twy]}")

    def i_frequency(self, unit):
        freq = self.aero.ground_freq if unit == "ground" else self.aero.tower_freq
        return Item("frequency", freq, "frequency", f"{unit.capitalize()} {freq}",
                    f"{unit} {icao.say_frequency(freq)}", station=unit)

    @staticmethod
    def i_clearance(kind):
        table = {
            "takeoff": ("take-off clearance", "cleared for take-off", ("cleared", "takeoff")),
            "touchandgo": ("touch-and-go clearance", "cleared touch and go", ("cleared", "touchandgo")),
            "land": ("landing clearance", "cleared to land", ("cleared", "land")),
        }
        label, display, words = table[kind]
        conflicts = tuple((w, d) for k, (_, d, w) in table.items() if k != kind)
        return Item("clearance", kind, label, display, display, words=words, conflicts=conflicts)

    @staticmethod
    def i_lineup():
        return Item("clearance", "lineup", "line-up instruction", "line up and wait",
                    "line up and wait", words=("lineup", "wait"), alternatives=(("lineup",),))

    def i_circuit(self):
        side = self.layout["circuit"]
        other = "left" if side == "right" else "right"
        return Item("circuit", side, "circuit direction", f"after departure {side}-hand circuit",
                    f"after departure {side} hand circuit", words=(f"{side}hand",),
                    conflicts=(((f"{other}hand",), f"{other}-hand circuit"),))

    @staticmethod
    def i_wilco(position):
        return Item("instruction", "wilco", "acknowledgement", "wilco", "wilco",
                    words=("wilco",), alternatives=(("roger",), ("report", position), ("will", "report")))

    @staticmethod
    def i_report(value, label, words, alternatives=(), ask=""):
        return Item("report", value, label, label, label, words=words,
                    alternatives=alternatives, ask=ask)

    # ------------------------------------------------------------ build

    def _readback(self, station, items, full_required=None):
        if full_required is None:
            full_required = not self._abbr[station]
        return Expectation("readback", items, full_callsign_required=full_required)

    def _example(self, items, station):
        cs = self.cs.display(self._abbr[station])
        return _cap(", ".join(i.display for i in items)) + f", {cs}"

    def _add(self, **kw):
        kw.setdefault("abbreviated", self._abbr[kw["station"]])
        self.exchanges.append(Exchange(**kw))

    def _build(self):
        a = self.aero
        rwy = self.runway
        hp = self.layout["holding_point"]
        twy = a.taxiway

        # 1. First call to Ground.
        self._add(id="request_taxi", station="ground",
                  hint=f"Call {a.name} Ground. You're parked at {a.parking} and you want to taxi for circuits.",
                  expect=Expectation("report", [self.i_report("taxi", "your request (taxi)", ("taxi",))],
                                     full_callsign_required=True, station="ground", new_station=True),
                  example=f"{a.name} Ground, {self.cs.full}, at {a.parking}, request taxi for circuits")

        # 2. Taxi clearance. Kept short enough to read back in 10 seconds.
        items = [self.i_runway(rwy), self.i_altimeter(),
                 self.i_holding_point(hp), self.i_via(twy)]
        alt = items[1]
        tower = self._msg("ground", [(f"runway {rwy}", icao.say_runway(rwy)), (alt.display, alt.spoken),
                                     (f"taxi holding point {hp} via {ALPHABET[twy]}",
                                      f"taxi holding point {icao.say_designator(hp)} via {ALPHABET[twy]}")],
                          first_contact=True)
        self._add(id="taxi_clearance", station="ground", tower=tower,
                  hint="Read back the taxi clearance.",
                  expect=self._readback("ground", items), example=self._example(items, "ground"))

        # 3. Ready at the holding point.
        self._add(id="ready_ground", station="ground",
                  hint=f"You're at holding point {hp}, checks done. Tell Ground you're ready.",
                  expect=Expectation("report", [self.i_report("ready", "that you're ready", ("ready",))],
                                     full_callsign_required=not self._abbr["ground"]),
                  example=f"{self.cs.display(self._abbr['ground'])}, holding point {hp}, ready")

        # 4. Squawk and frequency change to Tower.
        items = [self.i_squawk(), self.i_frequency("tower")]
        tower = self._msg("ground", [(items[0].display, items[0].spoken),
                                     (f"contact Tower {a.tower_freq}", f"contact tower {icao.say_frequency(a.tower_freq)}")])
        self._add(id="contact_tower", station="ground", tower=tower,
                  hint="Read back the squawk and the frequency change.",
                  expect=self._readback("ground", items), example=self._example(items, "ground"))

        # 5. First call to Tower.
        self._add(id="ready_tower", station="tower",
                  hint=f"Call {a.name} Tower: you're at holding point {hp}, ready for departure.",
                  expect=Expectation("report", [self.i_report("ready", "that you're ready for departure", ("ready",))],
                                     full_callsign_required=True, station="tower", new_station=True),
                  example=f"{a.name} Tower, {self.cs.full}, holding point {hp}, ready for departure")

        # 6. Line up (and the circuit direction when it is right-hand).
        items = []
        parts = []
        if self.layout["circuit"] == "right":
            c = self.i_circuit()
            items.append(c)
            parts.append((c.display, c.spoken))
        items += [self.i_runway(rwy), self.i_lineup()]
        parts += [(f"runway {rwy}", icao.say_runway(rwy)), ("line up and wait", "line up and wait")]
        tower = self._msg("tower", parts, first_contact=True)
        self._add(id="line_up", station="tower", tower=tower, hint="Read back the line-up instruction.",
                  expect=self._readback("tower", items), example=self._example(items, "tower"))

        # 7. Take-off.
        d, s = self._wind()
        items = [self.i_runway(rwy), self.i_clearance("takeoff")]
        tower = self._msg("tower", [(f"runway {rwy}", icao.say_runway(rwy)),
                                    ("cleared for take-off", "cleared for take-off"),
                                    (icao.show_wind(d, s), icao.say_wind(d, s))])
        self._add(id="takeoff", station="tower", tower=tower, hint="Read back the take-off clearance.",
                  expect=self._readback("tower", items), example=self._example(items, "tower"))

        # 8-11. First circuit, touch and go.
        self._circuit(1, self.surprise_slots[0], touch_and_go=True)
        if self.unable_touch_and_go:
            self._vacate()
            return
        # 12-15. Second circuit, full stop.
        self._circuit(2, self.surprise_slots[1], touch_and_go=False)
        self._vacate()

    def _circuit(self, n, surprise, touch_and_go):
        a = self.aero
        rwy = self.runway
        cs = self.cs.display(True)
        t = self.traffic_type
        tcs = (self.tcs.short, self.tcs.spoken_short)

        chatter = []
        if surprise == "follow":
            chatter = [self._traffic([tcs, ("base", "base")]),
                       self._traffic([tcs, ("report final", "report final")], speaker="tower"),
                       self._traffic([("wilco", "wilco"), tcs])]
        elif surprise in ("extend", "orbit"):
            d, s = self._wind()
            chatter = [self._traffic([tcs, ("final", "final")]),
                       self._traffic([tcs, (f"runway {rwy}", icao.say_runway(rwy)),
                                      ("cleared to land", "cleared to land"),
                                      (icao.show_wind(d, s), icao.say_wind(d, s))], speaker="tower"),
                       self._traffic([(f"runway {rwy}", icao.say_runway(rwy)),
                                      ("cleared to land", "cleared to land"), tcs])]

        if touch_and_go:
            intent = self.i_report("touchandgo", "your intention (touch and go)", ("touchandgo",),
                                   ask="say intentions")
            hint = "You've turned downwind. Report it, with your intention: a touch and go."
            example = f"{cs}, downwind, touch and go"
        else:
            intent = self.i_report("land", "your intention (to land)", ("land",),
                                   alternatives=(("fullstop",),), ask="say intentions")
            hint = "Second circuit, downwind again. This time you'll land and stop."
            example = f"{cs}, downwind to land"
        self._add(id=f"downwind_{n}", station="tower", chatter=chatter, hint=hint,
                  expect=Expectation("report", [self.i_report("downwind", "your position (downwind)", ("downwind",)),
                                                intent]),
                  example=example)

        # The tower's answer to the downwind call.
        if surprise == "follow":
            items = [Item("sequence", "2", "number in the sequence", "number 2", "number two"),
                     Item("traffic", t, "traffic in sight", f"{t} in sight", f"{t} in sight",
                          words=("insight",), alternatives=(("follow",), ("looking",)))]
            tower = self._msg("tower", [("number 2", "number two"), (f"follow the {t} on base", f"follow the {t} on base")])
            self._add(id=f"follow_{n}", station="tower", tower=tower,
                      hint=f"Confirm your number and that you see the {t} (you do).",
                      expect=self._readback("tower", items), example=self._example(items, "tower"))
        elif surprise == "extend":
            items = [Item("instruction", "extend", "extend downwind", "extend downwind", "extend downwind",
                          words=("extend",)),
                     Item("sequence", "2", "number in the sequence", "number 2", "number two")]
            tower = self._msg("tower", [("extend downwind", "extend downwind"), ("number 2", "number two"),
                                        (f"number 1 is a {t} on final", f"number one is a {t} on final")])
            self._add(id=f"extend_{n}", station="tower", tower=tower, hint="Read back the instruction.",
                      expect=self._readback("tower", items), example=self._example(items, "tower"))
        elif surprise == "orbit":
            side = "right" if self.layout["circuit"] == "left" else "left"
            other = "left" if side == "right" else "right"
            items = [Item("instruction", "orbit", "orbit", f"orbit {side}", f"orbit {side}",
                          words=("orbit", side), conflicts=((("orbit", other), f"orbit {other}"),)),
                     Item("sequence", "2", "number in the sequence", "number 2", "number two")]
            tower = self._msg("tower", [(f"orbit {side} for spacing", f"orbit {side} for spacing"),
                                        ("number 2", "number two"),
                                        (f"number 1 is a {t} on final", f"number one is a {t} on final")])
            self._add(id=f"orbit_{n}", station="tower", tower=tower, hint="Read back the instruction.",
                      expect=self._readback("tower", items), example=self._example(items, "tower"))
        else:
            item = self.i_wilco("final")
            tower = self._msg("tower", [("report final", "report final")])
            self._add(id=f"report_final_{n}", station="tower", tower=tower,
                      hint="Acknowledge the instruction.",
                      expect=Expectation("ack", [item]), example=f"Wilco, {cs}")

        # Final.
        self._add(id=f"final_{n}", station="tower", hint="You've turned final. Report it.",
                  expect=Expectation("report", [self.i_report("final", "your position (final)", ("final",))]),
                  example=f"{cs}, final")

        d, s = self._wind()
        wind = (icao.show_wind(d, s), icao.say_wind(d, s))
        if touch_and_go and self.unable_touch_and_go:
            items = [self.i_runway(rwy), self.i_clearance("land")]
            tower = self._msg("tower", [("unable to approve touch and go due traffic",
                                         "unable to approve touch and go due traffic"),
                                        ("make full stop landing", "make full stop landing"),
                                        (f"runway {rwy}", icao.say_runway(rwy)),
                                        ("cleared to land", "cleared to land"), wind])
            self._add(id=f"full_stop_{n}", station="tower", tower=tower,
                      hint="Plans change. Read back the clearance you were given.",
                      expect=self._readback("tower", items), example=self._example(items, "tower"))
        else:
            kind = "touchandgo" if touch_and_go else "land"
            items = [self.i_runway(rwy), self.i_clearance(kind)]
            phrase = "cleared touch and go" if touch_and_go else "cleared to land"
            tower = self._msg("tower", [(f"runway {rwy}", icao.say_runway(rwy)), (phrase, phrase), wind])
            self._add(id=f"clearance_{n}", station="tower", tower=tower,
                      hint=f"Read back the {'touch-and-go' if touch_and_go else 'landing'} clearance.",
                      expect=self._readback("tower", items), example=self._example(items, "tower"))

    def _vacate(self):
        a = self.aero
        side = self.layout["vacate"]
        other = "left" if side == "right" else "right"
        exit_twy = a.exit_taxiway
        items = [Item("instruction", "vacate", "vacate instruction", f"vacate {side}", f"vacate {side}",
                      words=("vacate", side), conflicts=((("vacate", other), f"vacate {other}"),)),
                 self.i_via(exit_twy), self.i_frequency("ground")]
        tower = self._msg("tower", [(f"vacate {side} via {ALPHABET[exit_twy]}", f"vacate {side} via {ALPHABET[exit_twy]}"),
                                    (f"contact Ground {a.ground_freq}", f"contact ground {icao.say_frequency(a.ground_freq)}")])
        self._add(id="vacate", station="tower", tower=tower,
                  hint="You've landed and slowed down. Read back the instructions.",
                  expect=self._readback("tower", items), example=self._example(items, "tower"))

        self._add(id="vacated", station="ground",
                  hint=f"You're clear of the runway on {ALPHABET[exit_twy]}. Call Ground.",
                  expect=Expectation("report", [self.i_report("vacated", "that you've vacated the runway",
                                                              ("vacated",), alternatives=(("vacate",),))],
                                     station="ground"),
                  example=f"{a.name} Ground, {self.cs.display(True)}, runway vacated")

        items = [Item("taxi_limit", "parking", "taxi clearance limit", f"taxi to {a.parking}",
                      f"taxi to {a.parking}", words=a.parking_words),
                 self.i_via(a.taxiway)]
        tower = self._msg("ground", [(f"taxi to {a.parking} via {ALPHABET[a.taxiway]}",
                                      f"taxi to {a.parking} via {ALPHABET[a.taxiway]}")])
        self._add(id="taxi_back", station="ground", tower=tower, hint="Read back the taxi instruction.",
                  expect=self._readback("ground", items), example=self._example(items, "ground"))

    # ------------------------------------------------------------ play

    @property
    def current(self) -> Exchange | None:
        return None if self.done else self.exchanges[self.index]

    @property
    def expectation(self) -> Expectation | None:
        if self.done:
            return None
        return self.retry or self.current.expect

    def start(self) -> list[Transmission]:
        return self._enter(0)

    def _enter(self, index) -> list[Transmission]:
        self.index = index
        self.retry = None
        if index >= len(self.exchanges):
            self.done = True
            return []
        ex = self.exchanges[index]
        out = list(ex.chatter)
        if ex.tower:
            out.append(ex.tower)
            self.last_to_pilot = ex.tower
        return out

    def submit(self, transcript: str) -> Turn:
        if self.done:
            return Turn(None, [], "", True, False)
        ex = self.current
        expect = self.expectation
        result = check(expect, transcript, self.ctx)
        example = ex.example

        if result.say_again:
            repeat = []
            if not result.errors and self.last_to_pilot and (ex.tower or self.retry):
                repeat = [self.last_to_pilot]
            return Turn(result, repeat, example, False, False)

        if result.ok:
            nxt = self._enter(self.index + 1)
            return Turn(result, nxt, example, self.done, True)

        correction = self._correction(ex, expect, result)
        self.last_to_pilot = correction
        return Turn(result, [correction], example, False, False)

    def skip(self) -> list[Transmission]:
        """Move on without a correct answer (the page offers it after a miss)."""
        return self._enter(self.index + 1)

    def _correction(self, ex: Exchange, expect: Expectation, result: Result) -> Transmission:
        station = ex.station
        cs = (self.cs.display(ex.abbreviated), self.cs.spoken(ex.abbreviated))
        unit = f"{self.aero.name} {station.capitalize()}"
        if result.wrong:
            parts = [(i.display, i.spoken) for i in result.wrong + result.missing]
            if any(i.kind == "clearance" for i in result.wrong) and not any(i.kind == "runway" for i in result.wrong):
                rwy = self.runway
                parts.insert(0, (f"runway {rwy}", icao.say_runway(rwy)))
            text, spoken = _join([cs, ("negative", "negative")] + parts)
            self.retry = Expectation("readback", result.wrong + result.missing,
                                     full_callsign_required=expect.full_callsign_required)
            return Transmission(station, text, spoken)
        if result.missing and expect.kind == "readback":
            labels = _and([i.label for i in result.missing])
            text, spoken = _join([cs, (f"read back {labels}", f"read back {labels}")])
            self.retry = Expectation("readback", result.missing,
                                     full_callsign_required=expect.full_callsign_required)
            return Transmission(station, text, spoken)
        if result.missing and expect.kind == "report":
            ask = next((i.ask for i in result.missing if i.ask), "say again")
            text, spoken = _join([cs, (ask, ask)])
            self.retry = Expectation("report", result.missing,
                                     full_callsign_required=expect.full_callsign_required,
                                     station=expect.station)
            return Transmission(station, text, spoken)
        # Callsign missing or wrong.
        text = f"Station calling {unit}, say again your callsign"
        return Transmission(station, text, text)

    def summary(self) -> dict:
        return {
            "seed": self.seed,
            "runway": self.runway,
            "altimeter": f"{self.settings.altimeter.upper()} {self.altimeter_value}",
            "squawk": self.squawk,
            "traffic": self.traffic_type,
            "surprises": self.surprises(),
            "steps": len(self.exchanges),
        }


def _and(words):
    words = list(dict.fromkeys(words))
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]
