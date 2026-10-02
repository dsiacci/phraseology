import io
import json
import threading
import urllib.error
import urllib.request
import wave

import numpy as np
import pytest

from phraseology.scenario import Settings
from phraseology.server import App, Limits, make_server
from phraseology.speech import array_to_wav


class FakeTranscriber:
    name = "fake"

    def __init__(self):
        self.next = ""

    def transcribe(self, audio, prompt=""):
        return self.next


@pytest.fixture
def server():
    stt = FakeTranscriber()
    app = App(Settings(surprises=False), stt, None, public=True,
              limits=Limits(turns_per_window=3, window_seconds=60))
    srv = make_server(app, "127.0.0.1", 0)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", stt
    srv.shutdown()
    srv.server_close()


def call(base, path, data=None, ctype="application/json"):
    body = None if data is None else (data if isinstance(data, bytes) else json.dumps(data).encode())
    req = urllib.request.Request(base + path, body, {"Content-Type": ctype} if body is not None else {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def tone(seconds):
    t = np.arange(int(16000 * seconds)) / 16000
    return array_to_wav(0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32))


def test_page_and_health(server):
    base, _ = server
    status, headers, body = call(base, "/")
    assert status == 200 and b"press-to-talk" in body.lower() or b"Space" in body
    assert "Content-Security-Policy" in headers
    status, _, body = call(base, "/healthz")
    assert status == 200 and json.loads(body)["status"] == "ok"
    assert call(base, "/static/../server.py")[0] == 404


def test_game_by_text(server):
    base, _ = server
    _, _, body = call(base, "/api/game", {"seed": 7})
    g = json.loads(body)
    assert g["step"] == 1 and g["expect"] == "report"
    _, _, body = call(base, "/api/text", {"game": g["game"], "text": "Isola Ground, F-ABCD, request taxi for circuits"})
    r = json.loads(body)
    assert r["accepted"] and r["transmissions"][0]["text"].startswith("F-ABCD, Isola Ground")


def test_turn_with_audio(server):
    base, stt = server
    g = json.loads(call(base, "/api/game", {"seed": 7})[2])
    stt.next = "Isola Ground, Foxtrot Alpha Bravo Charlie Delta, request taxi"
    status, _, body = call(base, f"/api/turn?game={g['game']}", tone(2), "audio/wav")
    r = json.loads(body)
    assert status == 200 and r["accepted"] and r["transcript"] == stt.next


def test_limits(server):
    base, stt = server
    g = json.loads(call(base, "/api/game", {"seed": 7})[2])["game"]
    assert call(base, f"/api/turn?game={g}", b"RIFF0000WAVEjunk", "audio/wav")[0] == 400
    assert call(base, f"/api/turn?game={g}", tone(12), "audio/wav")[0] == 413
    assert call(base, f"/api/turn?game={g}", b"\0" * 500_000, "audio/wav")[0] == 413
    assert call(base, "/api/turn?game=nope", tone(1), "audio/wav")[0] == 404
    stt.next = "say again"
    statuses = [call(base, f"/api/turn?game={g}", tone(1), "audio/wav")[0] for _ in range(4)]
    assert statuses[:3] == [200, 200, 200] and statuses[3] == 429


def test_wav_roundtrip():
    x = (0.1 * np.ones(1600)).astype(np.float32)
    data = array_to_wav(x)
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
