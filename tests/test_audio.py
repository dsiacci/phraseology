"""Round trip with the real models: Piper speaks a readback, Whisper
transcribes it, the checker must reach the right verdict.

Slow and needs the models: python -m pytest -m audio
Model and voices can be changed with PHRASEOLOGY_TEST_MODEL and
PHRASEOLOGY_TEST_VOICES (comma-separated).
"""

import os

import pytest

from phraseology.checker import check
from phraseology.icao import say_designator, say_digits, spell
from phraseology.scenario import Game, Settings
from phraseology.speech import build_prompt

pytestmark = pytest.mark.audio

MODEL = os.environ.get("PHRASEOLOGY_TEST_MODEL", "base.en")
VOICES = os.environ.get("PHRASEOLOGY_TEST_VOICES", "en_GB-cori-medium,en_US-norman-medium").split(",")


@pytest.fixture(scope="module")
def models():
    try:
        from phraseology.speech import Speaker, Transcriber
        stt = Transcriber(MODEL, threads=int(os.environ.get("PHRASEOLOGY_TEST_THREADS", "2")))
        speaker = Speaker()
        speaker.preload(*VOICES)
    except Exception as exc:  # models not downloaded
        pytest.skip(f"models not available: {exc}")
    return stt, speaker


def build_cases():
    g = Game(Settings(surprises=False), seed=7)
    low = next(Game(Settings(surprises=False), seed=s) for s in range(1, 500)
               if Game(Settings(surprises=False), seed=s).altimeter_value < 1000)
    rwy = say_digits(g.runway)
    qnh = say_digits(str(g.altimeter_value))
    hp = say_designator(g.layout["holding_point"])
    full, short = g.cs.spoken_full, g.cs.spoken_short
    sq = say_digits(g.squawk)
    bad_sq = say_digits(g.squawk[:3] + ("1" if g.squawk[3] != "1" else "2"))
    tower = say_digits(g.aero.tower_freq)
    ground = say_digits(g.aero.ground_freq)
    side = g.layout["vacate"]
    return [
        ("taxi readback", g, "taxi_clearance", f"Runway {rwy}, QNH {qnh}, holding point {hp} via Bravo, {full}", "ok"),
        ("taxi, wrong QNH", g, "taxi_clearance",
         f"Runway {rwy}, QNH {say_digits(str(g.altimeter_value + 10))}, holding point {hp} via Bravo, {full}", ("wrong", "qnh")),
        ("taxi, no runway", g, "taxi_clearance", f"QNH {qnh}, holding point {hp} via Bravo, {full}", ("missing", "runway")),
        ("taxi, roger only", g, "taxi_clearance", f"Roger, {full}", ("rejected", None)),
        ("squawk and frequency", g, "contact_tower", f"Squawk {sq}, Tower {tower}, {short}", "ok"),
        ("wrong squawk", g, "contact_tower", f"Squawk {bad_sq}, Tower {tower}, {short}", ("wrong", "squawk")),
        ("wrong frequency", g, "contact_tower",
         f"Squawk {sq}, Tower {say_digits('118.550')}, {short}", ("wrong", "frequency")),
        ("take-off readback", g, "takeoff", f"Runway {rwy}, cleared for take-off, {short}", "ok"),
        ("take-off read as landing", g, "takeoff", f"Runway {rwy}, cleared to land, {short}", ("wrong", "clearance")),
        ("wrong callsign", g, "takeoff", f"Runway {rwy}, cleared for take-off, {spell('FCB')}", ("code", ("callsign_wrong", "callsign_missing"))),
        ("downwind report", g, "downwind_1", f"{short}, downwind, touch and go", "ok"),
        ("wilco", g, "report_final_1", f"Wilco, {short}", "ok"),
        ("say again", g, "report_final_1", f"Say again, {short}", "say_again"),
        ("vacate and frequency", g, "vacate", f"Vacate {side} via Charlie, Ground {ground}, {short}", "ok"),
        ("QNH below 1000", low, "taxi_clearance",
         f"Runway {say_digits(low.runway)}, QNH {say_digits(str(low.altimeter_value))} hectopascals, "
         f"holding point {say_designator(low.layout['holding_point'])} via Bravo, {low.cs.spoken_full}", "ok"),
    ]


CASES = build_cases()


def run(models, voice, cases):
    stt, speaker = models
    rows = []
    for name, game, ex_id, spoken, want in cases:
        ex = next(e for e in game.exchanges if e.id == ex_id)
        transcript = stt.transcribe(speaker.array(spoken, voice), build_prompt(game.aero, game.cs))
        res = check(ex.expect, transcript, game.ctx)
        rows.append((name, want, transcript, res))
        print(f"{voice:22} {name:26} {'ok ' if res.ok else 'say again' if res.say_again else 'no '} {transcript}")
    return rows


@pytest.mark.parametrize("voice", VOICES)
def test_wrong_readbacks_are_never_accepted(models, voice):
    """The property that matters: a wrong readback never gets through.

    When Whisper mishears, the transcript may come out wrong in another
    way than the pilot's mistake, so only the verdict is asserted here;
    the reasons are covered word for word in test_checker.py.
    """
    wrong = [c for c in CASES if c[4] not in ("ok", "say_again")]
    for name, want, transcript, res in run(models, voice, wrong):
        assert not res.ok, f"{name}: {transcript!r} was accepted"


@pytest.mark.parametrize("voice", VOICES)
def test_correct_readbacks_are_accepted(models, voice):
    """Correct readbacks get through, allowing for a few mishearings.

    A synthetic voice through the smallest model is sometimes misheard
    ("squawk 510" for "five one five zero"). The checker then rejects a
    correct readback and the tower asks for it again: annoying, never
    unsafe. At most one in four may go that way.
    """
    good = [c for c in CASES if c[4] in ("ok", "say_again")]
    rows = run(models, voice, good)
    misses = [(n, t) for n, want, t, res in rows
              if not (res.say_again if want == "say_again" else res.ok)]
    assert len(misses) <= len(good) // 4, misses
