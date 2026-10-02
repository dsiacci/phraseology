from phraseology.scenario import Game, Settings


def play_perfect(game):
    game.start()
    turns = 0
    while not game.done:
        turn = game.submit(game.current.example)
        assert turn.accepted, (game.seed, game.current.id)
        turns += 1
    return turns


def test_same_seed_same_game():
    a, b = Game(seed=42), Game(seed=42)
    assert [e.id for e in a.exchanges] == [e.id for e in b.exchanges]
    assert a.summary() == b.summary()


def test_many_games_complete():
    for seed in range(1, 300):
        g = Game(seed=seed)
        assert len(g.surprises()) <= 3
        assert play_perfect(g) == len(g.exchanges)


def test_without_surprises():
    g = Game(Settings(surprises=False), seed=5)
    ids = [e.id for e in g.exchanges]
    assert ids[0] == "request_taxi" and ids[-1] == "taxi_back"
    assert "clearance_1" in ids and "clearance_2" in ids


def test_negative_then_correct():
    g = Game(Settings(surprises=False), seed=7)
    g.start()
    g.submit(g.current.example)  # request taxi
    wrong = g.current.example.replace(str(g.altimeter_value), str(g.altimeter_value + 1))
    turn = g.submit(wrong)
    assert not turn.accepted
    assert turn.transmissions[0].text.startswith("F-ABCD, negative, QNH")
    turn = g.submit(f"QNH {g.altimeter_value}, F-ABCD")
    assert turn.accepted and g.current.id == "ready_ground"


def test_say_again_repeats_last_message():
    g = Game(Settings(surprises=False), seed=7)
    g.start()
    first = g.submit(g.current.example).transmissions[0]
    turn = g.submit("say again")
    assert turn.transmissions == [first]
    assert g.current.id == "taxi_clearance"


def test_full_stop_surprise_skips_second_circuit():
    g = next(Game(seed=s) for s in range(1, 500) if Game(seed=s).unable_touch_and_go)
    ids = [e.id for e in g.exchanges]
    assert "full_stop_1" in ids and "downwind_2" not in ids


def test_values_are_plausible():
    for seed in range(1, 200):
        g = Game(seed=seed)
        assert all(c in "01234567" for c in g.squawk) and g.squawk not in ("7500", "7600", "7700")
        assert 990 <= g.qnh <= 1035
        assert g.runway in g.aero.runways
