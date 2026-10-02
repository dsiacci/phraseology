from phraseology.normalize import normalize


def test_digits_and_letters():
    assert normalize("Runway 27, QNH 1013") == ["runway", "2", "7", "qnh", "1", "0", "1", "3"]
    assert normalize("F-ABCD") == ["F", "A", "B", "C", "D"]
    assert normalize("Foxtrot Charlie Delta") == ["F", "C", "D"]
    assert normalize("Fox-trot Alpha Bravo Charlie Delta") == ["F", "A", "B", "C", "D"]
    assert normalize("holding point A1 via B") == ["holdingpoint", "A", "1", "via", "B"]


def test_icao_pronunciations():
    assert normalize("tree fife niner") == ["3", "5", "9"]
    assert normalize("QNH niner niner eight hectopascals") == ["qnh", "9", "9", "8", "hpa"]


def test_what_whisper_does_with_niner():
    # Real base.en outputs for "zero niner" and "niner niner eight".
    assert normalize("Runway 0-9-R, cleared for take-off") == ["runway", "0", "9", "cleared", "for", "takeoff"]
    assert normalize("QNH 9 and 9 are 8 hectopascals")[:4] == ["qnh", "9", "9", "8"]
    assert normalize("QNH, 9 or 9 or 8, hectopascals")[:4] == ["qnh", "9", "9", "8"]
    assert normalize("QNH 9 at 9 at 8")[:4] == ["qnh", "9", "9", "8"]


def test_homophones_only_in_numbers():
    assert normalize("Squawk for 107") == ["squawk", "4", "1", "0", "7"]
    assert normalize("cleared for take-off") == ["cleared", "for", "takeoff"]
    assert normalize("QNH 1 oh 1 3") == ["qnh", "1", "0", "1", "3"]


def test_frequencies():
    want = ["1", "2", "1", "decimal", "8", "0", "5"]
    assert normalize("121.805") == want
    assert normalize("121 decimal 805") == want
    assert normalize("one two one decimal eight zero five") == want
    assert normalize("121 point 805") == want
    assert normalize("1-2-1, disseminate 0-5")[:5] == ["1", "2", "1", "decimal", "8"]


def test_phrases_glued():
    assert normalize("touch-and-go") == ["touchandgo"]
    assert normalize("touch and go") == ["touchandgo"]
    assert normalize("downwind, touch, seven, go") == ["downwind", "touchandgo"]
    assert normalize("line up and wait")[0] == "lineup"
    assert normalize("Cherokee Insight") == ["cherokee", "insight"]
    assert normalize("say again") == ["sayagain"]
    assert normalize("Q N H") == ["qnh"]


def test_group_numbers():
    assert normalize("runway twenty seven") == ["runway", "2", "7"]
    assert normalize("QNH ten thirteen") == ["qnh", "1", "0", "1", "3"]
