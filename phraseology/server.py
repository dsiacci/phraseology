"""A small HTTP server: the page, a JSON API, and the guards for running
it on the internet.

Local use: one person, no limits, the voice never leaves the laptop.
Online use (--public): one transcription at a time with a short queue,
10 seconds of audio at most, a per-address rate limit, bounded request
sizes. Audio is decoded in memory, transcribed, and dropped. The log
holds method, path, status and timing, never audio, text or addresses.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
import time
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from .icao import Callsign
from .scenario import Game, Settings, Transmission
from .speech import build_prompt, wav_to_array

log = logging.getLogger("phraseology")

WEB_DIR = Path(__file__).parent / "web"
STATIC_FILES = {"app.js", "style.css", "recorder-worklet.js", "favicon.svg"}


@dataclass
class Limits:
    max_seconds: float = 10.0
    max_audio_bytes: int = 400_000       # 10 s of 16 kHz mono 16-bit is 320 kB
    max_json_bytes: int = 8_000
    queue_size: int = 6                  # transcriptions waiting behind the current one
    queue_timeout: float = 30.0
    turns_per_window: int = 60           # transcriptions per address per window
    requests_per_window: int = 600       # API calls per address per window
    window_seconds: float = 600.0
    max_games: int = 300
    game_ttl: float = 3600.0
    audio_cache: int = 400               # synthesized messages kept in memory


class RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 10_000:  # forget idle addresses
                for k in [k for k, v in self.hits.items() if not v]:
                    del self.hits[k]
            return True


class App:
    def __init__(self, settings: Settings, transcriber=None, speaker=None, *,
                 public: bool = False, limits: Limits | None = None,
                 tower_voice: str = "en_GB-cori-medium",
                 traffic_voice: str = "en_US-norman-medium"):
        self.settings = settings
        self.transcriber = transcriber
        self.speaker = speaker
        self.public = public
        self.limits = limits or Limits()
        self.voices = {"ground": tower_voice, "tower": tower_voice, "traffic": traffic_voice}
        self.games: OrderedDict[str, tuple[Game, float, dict]] = OrderedDict()
        self.audio: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()
        self.queue = threading.Semaphore(1 + self.limits.queue_size)
        self.turns = RateLimiter(self.limits.turns_per_window, self.limits.window_seconds)
        self.requests = RateLimiter(self.limits.requests_per_window, self.limits.window_seconds)
        self.started = time.time()

    # ---------------------------------------------------------------- games

    def new_game(self, seed=None, surprises=True, slow=False):
        settings = Settings(**{**self.settings.__dict__, "surprises": surprises})
        game = Game(settings, seed=seed)
        gid = secrets.token_urlsafe(12)
        with self.lock:
            self.games[gid] = (game, time.monotonic(), {"slow": slow})
            self._expire()
        return gid, game

    def get_game(self, gid):
        with self.lock:
            entry = self.games.get(gid or "")
            if not entry:
                return None, None
            game, _, opts = entry
            self.games[gid] = (game, time.monotonic(), opts)
            self.games.move_to_end(gid)
            return game, opts

    def _expire(self):
        now = time.monotonic()
        while self.games:
            gid, (_, seen, _) = next(iter(self.games.items()))
            if len(self.games) > self.limits.max_games or now - seen > self.limits.game_ttl:
                self.games.popitem(last=False)
            else:
                break

    # ---------------------------------------------------------------- audio

    def audio_url(self, t: Transmission, slow: bool) -> str | None:
        if self.speaker is None:
            return None
        aid = secrets.token_urlsafe(10)
        with self.lock:
            self.audio[aid] = {"text": t.spoken, "voice": self.voices.get(t.speaker, self.voices["tower"]),
                               "scale": 1.25 if slow else 1.0, "wav": None}
            while len(self.audio) > self.limits.audio_cache:
                self.audio.popitem(last=False)
        return f"/api/audio/{aid}"

    def audio_bytes(self, aid: str) -> bytes | None:
        with self.lock:
            item = self.audio.get(aid)
        if item is None:
            return None
        if item["wav"] is None:
            item["wav"] = self.speaker.wav(item["text"], item["voice"], item["scale"])
        return item["wav"]

    # ---------------------------------------------------------------- views

    def transmissions(self, items, slow):
        out = []
        for t in items:
            out.append({"speaker": t.speaker, "text": t.text, "toPilot": t.to_pilot,
                        "audio": self.audio_url(t, slow)})
        return out

    def state(self, game: Game, opts: dict, transmissions, turn=None, transcript=None):
        ex = game.current
        body = {
            "step": min(game.index + 1, len(game.exchanges)),
            "steps": len(game.exchanges),
            "done": game.done,
            "transmissions": self.transmissions(transmissions, opts.get("slow")),
            "hint": None if game.done else ex.hint,
            "expect": None if game.done else game.expectation.kind,
            "retry": bool(game.retry),
        }
        if not game.done:
            body["station"] = ex.station
            body["frequency"] = game.aero.ground_freq if ex.station == "ground" else game.aero.tower_freq
        if turn is not None:
            res = turn.result
            body.update({
                "transcript": transcript if transcript is not None else (res.transcript if res else ""),
                "accepted": turn.accepted,
                "sayAgain": bool(res and res.say_again),
                "findings": [{"level": f.level, "text": f.text} for f in (res.findings if res else [])],
                "example": turn.example,
            })
        if game.done:
            body["summary"] = game.summary()
        return body

    def config(self):
        a = self.settings.aerodrome
        cs = Callsign(self.settings.callsign)
        return {
            "version": __version__,
            "mode": "online" if self.public else "local",
            "callsign": cs.full, "callsignShort": cs.short,
            "aerodrome": a.name, "icao": a.icao,
            "maxSeconds": self.limits.max_seconds,
            "speech": self.transcriber is not None,
            "voice": self.speaker is not None,
            "model": getattr(self.transcriber, "name", None),
        }

    def prompt(self, game: Game) -> str:
        return build_prompt(game.aero, game.cs)


class Handler(BaseHTTPRequestHandler):
    server_version = "phraseology"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 30
    app: App = None  # set by make_server

    # ---------------------------------------------------------------- plumbing

    def log_message(self, fmt, *args):  # no addresses, no query strings
        pass

    def _log(self, status, started):
        path = urlparse(self.path).path
        if path.startswith("/api/audio/"):
            path = "/api/audio/…"
        log.info("%s %s %d %.0fms", self.command, path, status, (time.monotonic() - started) * 1000)

    def client_key(self) -> str:
        if self.app.public and self.client_address[0] in ("127.0.0.1", "::1"):
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd:
                return fwd.split(",")[-1].strip()
        return self.client_address[0]

    def send(self, status, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if ctype.startswith(("application/json", "audio")) else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
                             "style-src 'self'; script-src 'self'; connect-src 'self'; "
                             "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.send_header("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self._status = status

    def error(self, status, message, headers=None):
        self.send(status, {"error": message}, headers=headers)

    def read_body(self, limit) -> bytes | None:
        length = self.headers.get("Content-Length")
        if length is None:
            self.error(HTTPStatus.LENGTH_REQUIRED, "Content-Length required")
            return None
        try:
            n = int(length)
        except ValueError:
            self.error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
            return None
        if n < 0 or n > limit:
            self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"request too large (max {limit} bytes)")
            self.close_connection = True
            return None
        return self.rfile.read(n)

    def read_json(self):
        raw = self.read_body(self.app.limits.max_json_bytes)
        if raw is None:
            return None
        try:
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            self.error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return None

    # ---------------------------------------------------------------- routes

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        started = time.monotonic()
        self._status = 0
        try:
            self._get()
        finally:
            self._log(self._status, started)

    def do_POST(self):
        started = time.monotonic()
        self._status = 0
        try:
            if not self.app.requests.allow(self.client_key()):
                self.error(HTTPStatus.TOO_MANY_REQUESTS, "too many requests, try again in a few minutes",
                           {"Retry-After": "60"})
                return
            self._post()
        finally:
            self._log(self._status, started)

    def _get(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self.static("index.html")
        if path.startswith("/static/"):
            name = path[len("/static/"):]
            if name in STATIC_FILES:
                return self.static(name)
            return self.error(HTTPStatus.NOT_FOUND, "not found")
        if path == "/healthz":
            app = self.app
            return self.send(HTTPStatus.OK, {"status": "ok", "version": __version__,
                                             "model": getattr(app.transcriber, "name", None),
                                             "games": len(app.games),
                                             "uptime": round(time.time() - app.started)})
        if path == "/api/config":
            return self.send(HTTPStatus.OK, self.app.config())
        if path.startswith("/api/audio/"):
            aid = path[len("/api/audio/"):]
            try:
                wav = self.app.audio_bytes(aid)
            except FileNotFoundError as exc:
                return self.error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            if wav is None:
                return self.error(HTTPStatus.NOT_FOUND, "audio expired")
            return self.send(HTTPStatus.OK, wav, "audio/wav")
        return self.error(HTTPStatus.NOT_FOUND, "not found")

    def static(self, name):
        f = WEB_DIR / name
        if not f.is_file():
            return self.error(HTTPStatus.NOT_FOUND, "not found")
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self.send(HTTPStatus.OK, f.read_bytes(), ctype)

    def _post(self):
        url = urlparse(self.path)
        path = url.path
        app = self.app
        if path == "/api/game":
            data = self.read_json()
            if data is None:
                return
            seed = data.get("seed")
            seed = int(seed) if isinstance(seed, int) or (isinstance(seed, str) and seed.isdigit()) else None
            gid, game = app.new_game(seed=seed, surprises=bool(data.get("surprises", True)),
                                     slow=bool(data.get("slow", False)))
            _, opts = app.get_game(gid)
            body = app.state(game, opts, game.start())
            body["game"] = gid
            return self.send(HTTPStatus.OK, body)

        if path in ("/api/text", "/api/skip", "/api/say-again"):
            data = self.read_json()
            if data is None:
                return
            game, opts = app.get_game(data.get("game"))
            if game is None:
                return self.error(HTTPStatus.NOT_FOUND, "game expired, start a new circuit")
            if "slow" in data:
                opts["slow"] = bool(data["slow"])
            if path == "/api/skip":
                return self.send(HTTPStatus.OK, app.state(game, opts, game.skip()))
            if path == "/api/say-again":
                again = [game.last_to_pilot] if game.last_to_pilot else []
                return self.send(HTTPStatus.OK, app.state(game, opts, again))
            text = str(data.get("text", ""))[:500]
            turn = game.submit(text)
            return self.send(HTTPStatus.OK, app.state(game, opts, turn.transmissions, turn, text))

        if path == "/api/turn":
            if app.transcriber is None:
                return self.error(HTTPStatus.SERVICE_UNAVAILABLE, "speech recognition is not loaded")
            qs = parse_qs(url.query)
            game, opts = app.get_game((qs.get("game") or [""])[0])
            if game is None:
                return self.error(HTTPStatus.NOT_FOUND, "game expired, start a new circuit")
            raw = self.read_body(app.limits.max_audio_bytes)
            if raw is None:
                return
            try:
                audio, seconds = wav_to_array(raw)
            except ValueError as exc:
                return self.error(HTTPStatus.BAD_REQUEST, str(exc))
            if seconds > app.limits.max_seconds + 0.5:
                return self.error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                                  f"recordings are limited to {app.limits.max_seconds:.0f} seconds")
            if app.public and not app.turns.allow(self.client_key()):
                return self.error(HTTPStatus.TOO_MANY_REQUESTS,
                                  "that's a lot of transmissions for now, try again in a few minutes",
                                  {"Retry-After": "120"})
            if not app.queue.acquire(blocking=False):
                return self.error(HTTPStatus.SERVICE_UNAVAILABLE, "the frequency is busy, try again shortly",
                                  {"Retry-After": "10"})
            try:
                t0 = time.monotonic()
                with _Waiter(app) as ok:
                    if not ok:
                        return self.error(HTTPStatus.SERVICE_UNAVAILABLE,
                                          "the frequency is busy, try again shortly", {"Retry-After": "10"})
                    text = app.transcriber.transcribe(audio, app.prompt(game))
                turn = game.submit(text)
                body = app.state(game, opts, turn.transmissions, turn, text)
                body["sttMs"] = round((time.monotonic() - t0) * 1000)
                return self.send(HTTPStatus.OK, body)
            finally:
                app.queue.release()

        return self.error(HTTPStatus.NOT_FOUND, "not found")


class _Waiter:
    """Hold the single transcription slot; give up after queue_timeout."""

    _slot = threading.Lock()

    def __init__(self, app: App):
        self.app = app
        self.ok = False

    def __enter__(self):
        self.ok = self._slot.acquire(timeout=self.app.limits.queue_timeout)
        return self.ok

    def __exit__(self, *exc):
        if self.ok:
            self._slot.release()
        return False


def make_server(app: App, host: str = "127.0.0.1", port: int = 8000) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    ThreadingHTTPServer.request_queue_size = 64
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server
