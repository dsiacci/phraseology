"""Command line: serve the page, download the models, or play in text."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from .scenario import Aerodrome, Game, Settings
from .speech import DEFAULT_VOICES_DIR

TOWER_VOICE = "en_GB-cori-medium"
TRAFFIC_VOICE = "en_US-norman-medium"


def settings_from(args) -> Settings:
    return Settings(callsign=args.callsign.upper(), aerodrome=Aerodrome(name=args.aerodrome),
                    altimeter=args.altimeter.upper())


def add_game_options(p):
    p.add_argument("--callsign", default="F-ABCD", help="your registration, e.g. F-ABCD or G-ABCD")
    p.add_argument("--aerodrome", default="Isola", help="name of the (fictional) aerodrome")
    p.add_argument("--altimeter", default="QNH", choices=["QNH", "QFE", "qnh", "qfe"])


def cmd_serve(args):
    from .server import App, Limits, make_server
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    transcriber = speaker = None
    if not args.no_speech:
        from .speech import Speaker, Transcriber
        print(f"Loading Whisper {args.model} (first run downloads it)…", flush=True)
        transcriber = Transcriber(args.model, threads=args.threads, beam_size=args.beam)
        speaker = Speaker(Path(args.voices_dir))
        speaker.preload(args.tower_voice, args.traffic_voice)
    limits = Limits(max_seconds=args.max_seconds)
    app = App(settings_from(args), transcriber, speaker, public=args.public, limits=limits,
              tower_voice=args.tower_voice, traffic_voice=args.traffic_voice)
    server = make_server(app, args.host, args.port)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    print(f"Ready: {url}  (Ctrl+C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_download(args):
    from faster_whisper import download_model
    from piper.download_voices import download_voice
    voices_dir = Path(args.voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    for voice in args.voices:
        print(f"Voice {voice} → {voices_dir}", flush=True)
        download_voice(voice, voices_dir)
    for model in args.models:
        print(f"Whisper {model}…", flush=True)
        download_model(model)
    print("Done.")


def cmd_play(args):
    """A text-only game in the terminal: type what you would say."""
    game = Game(settings_from(args), seed=args.seed)
    for t in game.start():
        print(f"  {t.speaker.upper():8} {t.text}")
    while not game.done:
        print(f"\n[{game.index + 1}/{len(game.exchanges)}] {game.current.hint}")
        try:
            said = input("you > ")
        except EOFError:
            return
        turn = game.submit(said)
        for f in turn.result.findings:
            print(f"  {'✗' if f.level == 'error' else '·'} {f.text}")
        if not turn.accepted and not turn.result.say_again:
            print(f"  e.g. {turn.example}")
        for t in turn.transmissions:
            print(f"  {t.speaker.upper():8} {t.text}")
    print("\nCircuit complete.", game.summary())


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m phraseology", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="serve the practice page")
    add_game_options(s)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--model", default="small.en", help="Whisper model: small.en (default), base.en, tiny.en")
    s.add_argument("--threads", type=int, default=0, help="CPU threads for Whisper (0 = all)")
    s.add_argument("--beam", type=int, default=5)
    s.add_argument("--voices-dir", default=str(DEFAULT_VOICES_DIR))
    s.add_argument("--tower-voice", default=TOWER_VOICE)
    s.add_argument("--traffic-voice", default=TRAFFIC_VOICE)
    s.add_argument("--max-seconds", type=float, default=10.0)
    s.add_argument("--public", action="store_true", help="online mode: limits on, voice notice changes")
    s.add_argument("--no-speech", action="store_true", help="text only, no models (for development)")
    s.add_argument("--open", action="store_true", help="open the page in the browser")
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("download", help="download the Whisper model and the Piper voices")
    d.add_argument("--models", nargs="+", default=["small.en"])
    d.add_argument("--voices", nargs="+", default=[TOWER_VOICE, TRAFFIC_VOICE])
    d.add_argument("--voices-dir", default=str(DEFAULT_VOICES_DIR))
    d.set_defaults(func=cmd_download)

    t = sub.add_parser("play", help="a text-only circuit in the terminal")
    add_game_options(t)
    t.add_argument("--seed", type=int, default=None)
    t.set_defaults(func=cmd_play)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
