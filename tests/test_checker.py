import pytest

from phraseology.checker import Context, Expectation, check
from phraseology.scenario import Game, Settings

CTX = Context("FABCD", "FCD", "F-ABCD", "F-CD", "Isola")


@pytest.fixture
def game():
    return Game(Settings(surprises=False), seed=7)


def exchange(game, ident):
    return next(e for e in game.exchanges if e.id == ident)


def codes(res):
    return [f.code for f in res.findings]


def test_examples_are_accepted(game):
    for ex in game.exchanges:
        res = check(ex.expect, ex.example, game.ctx)
        assert res.ok, (ex.id, ex.example, codes(res))


def test_wrong_qnh_is_caught(game):
    ex = exchange(game, "taxi_clearance")
    wrong = game.altimeter_value + 10
    said = ex.example.replace(str(game.altimeter_value), str(wrong))
    res = check(ex.expect, said, game.ctx)
    assert not res.ok
    assert [i.kind for i in res.wrong] == ["qnh"]


def test_missing_runway_is_caught(game):
    ex = exchange(game, "taxi_clearance")
    said = ex.example.replace(f"Runway {game.runway}, ", "")
    res = check(ex.expect, said, game.ctx)
    assert [i.kind for i in res.missing] == ["runway"]


def test_roger_instead_of_readback(game):
    ex = exchange(game, "taxi_clearance")
    res = check(ex.expect, "Roger, F-ABCD", game.ctx)
    assert "roger_instead" in codes(res)
    assert not res.ok


def test_wrong_clearance_is_caught(game):
    ex = exchange(game, "clearance_1")  # cleared touch and go
    res = check(ex.expect, f"Runway {game.runway}, cleared to land, F-CD", game.ctx)
    assert [i.value for i in res.wrong] == ["touchandgo"]


def test_callsign_rules(game):
    ex = exchange(game, "takeoff")
    good = f"Runway {game.runway}, cleared for take-off, F-CD"
    assert check(ex.expect, good, game.ctx).ok
    first = check(ex.expect, f"F-CD, runway {game.runway}, cleared for take-off", game.ctx)
    assert first.ok and "callsign_position" in codes(first)
    none = check(ex.expect, f"Runway {game.runway}, cleared for take-off", game.ctx)
    assert "callsign_missing" in codes(none) and not none.ok
    other = check(ex.expect, f"Runway {game.runway}, cleared for take-off, F-CB", game.ctx)
    assert "callsign_wrong" in codes(other)


def test_abbreviated_too_early(game):
    ex = exchange(game, "taxi_clearance")  # Ground has not shortened it yet
    said = ex.example.replace("F-ABCD", "F-CD")
    res = check(ex.expect, said, game.ctx)
    assert res.ok and "callsign_short_early" in codes(res)


def test_wilco_and_roger(game):
    ex = exchange(game, "report_final_1")
    assert check(ex.expect, "Wilco, F-CD", game.ctx).ok
    res = check(ex.expect, "Roger, F-CD", game.ctx)
    assert res.ok and "roger_not_wilco" in codes(res)


def test_report_needs_intention(game):
    ex = exchange(game, "downwind_1")
    assert check(ex.expect, "F-CD, downwind, touch and go", game.ctx).ok
    res = check(ex.expect, "F-CD, downwind", game.ctx)
    assert not res.ok and res.missing[0].value == "touchandgo"


def test_say_again():
    res = check(Expectation("readback"), "Say again, F-CD", CTX)
    assert res.say_again


def test_frequency_readback(game):
    ex = exchange(game, "contact_tower")
    said = f"Squawk {game.squawk}, Tower 118.550, F-CD"
    res = check(ex.expect, said, game.ctx)
    assert [i.kind for i in res.wrong] == ["frequency"]


def test_unit_below_1000():
    g = next(Game(Settings(surprises=False), seed=s) for s in range(1, 500)
             if Game(Settings(surprises=False), seed=s).altimeter_value < 1000)
    ex = exchange(g, "taxi_clearance")
    res = check(ex.expect, ex.example.replace(" hectopascals", ""), g.ctx)
    assert res.ok and "qnh_unit" in codes(res)


def test_niner_heard_as_nine_oh():
    g = next(Game(Settings(surprises=False), seed=s) for s in range(1, 500)
             if Game(Settings(surprises=False), seed=s).altimeter_value == 998)
    ex = exchange(g, "taxi_clearance")
    said = ex.example.replace("QNH 998", "QNH 9098")
    assert check(ex.expect, said, g.ctx).ok
    wrong = ex.example.replace("QNH 998", "QNH 990")
    assert [i.kind for i in check(ex.expect, wrong, g.ctx).wrong] == ["qnh"]


def test_traffic_named_but_in_sight_misheard():
    g = next(Game(seed=s) for s in range(1, 500) if "follow" in Game(seed=s).surprises())
    ex = next(e for e in g.exchanges if e.id.startswith("follow"))
    t = g.traffic_type
    res = check(ex.expect, f"Number 2, {t} and set, F-CD", g.ctx)
    assert res.ok and "in_sight" in codes(res)
    assert not check(ex.expect, "Number 2, F-CD", g.ctx).ok
