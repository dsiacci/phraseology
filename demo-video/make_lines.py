"""Synthesize the pilot's lines for the demo video.

Three kinds of voice are supported, set by "pilot_voice" in lines.json:

* "kokoro:<voice>@<lang>": Kokoro-82M (open weights, Apache 2.0) through
  kokoro-onnx. <voice> is a voice name or a blend such as
  "ff_siwis*0.5+am_michael*0.5"; <lang> is the phonemizer language
  ("en-us" for English sounds, "fr-fr" to read with French rules). The
  model files are fetched once into $KOKORO_DIR
  (~/.local/share/phraseology/kokoro by default).
* "elevenlabs:<voice name or id>": ElevenLabs text to speech (eleven_v3),
  with an accent tag in front of each line. The key comes from the
  ELEVENLABS_API_KEY environment variable, never from this repository.
  Lines already synthesized with the same voice, model and text are not
  requested again (out/lines.json keeps track), so credits are spent once.
* any Piper voice ("fr_FR-mls-medium#6"), reading the "say" spelling.

    python demo-video/make_lines.py            # writes demo-video/out/pilot_XX.wav
"""

import hashlib
import io
import json
import os
import sys
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
VOICES = HERE / "voices"
API = "https://api.elevenlabs.io"


def elevenlabs(voice: str, text: str, cfg: dict) -> bytes:
    key = os.environ["ELEVENLABS_API_KEY"]
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    if not voice.isalnum() or len(voice) < 16:  # a name such as "Brian": look up its id
        req = urllib.request.Request(f"{API}/v1/voices", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r:
            voices = json.loads(r.read())["voices"]
        voice = next(v["voice_id"] for v in voices if v["name"].split(" - ")[0] == voice)
    body = {"text": cfg.get("prefix", "") + text, "model_id": cfg.get("model", "eleven_v3")}
    req = urllib.request.Request(f"{API}/v1/text-to-speech/{voice}?output_format=pcm_22050",
                                 json.dumps(body).encode(), headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        pcm = r.read()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(pcm)
    return buf.getvalue()


KOKORO_FILES = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_kokoro = None


def kokoro(spec: str, text: str, speed: float) -> bytes:
    global _kokoro
    from kokoro_onnx import Kokoro
    folder = Path(os.environ.get("KOKORO_DIR", Path.home() / ".local/share/phraseology/kokoro"))
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        if not (folder / name).exists():
            urllib.request.urlretrieve(KOKORO_FILES + name, folder / name)
    if _kokoro is None:
        _kokoro = Kokoro(str(folder / "kokoro-v1.0.onnx"), str(folder / "voices-v1.0.bin"))
    voice, _, lang = spec.partition("@")
    style = voice
    if "+" in voice or "*" in voice:  # a blend of voices
        style = 0
        for part in voice.split("+"):
            name, _, weight = part.partition("*")
            style = style + float(weight or 1) * _kokoro.get_voice_style(name)
    samples, rate = _kokoro.create(text, voice=style, speed=speed, lang=lang or "en-us")
    pcm = (samples.clip(-1, 1) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def main():
    script = json.loads((HERE / "lines.json").read_text())
    voice = script["pilot_voice"]
    OUT.mkdir(exist_ok=True)
    done_file = OUT / "lines.json"
    done = json.loads(done_file.read_text()) if done_file.exists() else {}
    speaker = None
    for i, line in enumerate(script["lines"]):
        target = OUT / f"pilot_{i:02d}.wav"
        if voice.startswith("kokoro:"):
            text = line.get("say_kokoro", line["means"])
            speed = script.get("speed", 1.0)
            stamp = hashlib.sha1(json.dumps([voice, speed, text]).encode()).hexdigest()
            if done.get(target.name) == stamp and target.exists():
                continue
            target.write_bytes(kokoro(voice.split(":", 1)[1], text, speed))
        elif voice.startswith("elevenlabs:"):
            cfg = script.get("elevenlabs", {})
            text = line["means"]
            stamp = hashlib.sha1(json.dumps([voice, cfg, text]).encode()).hexdigest()
            if done.get(target.name) == stamp and target.exists():
                continue
            target.write_bytes(elevenlabs(voice.split(":", 1)[1], text, cfg))
        else:
            from piper.download_voices import download_voice
            from phraseology.speech import Speaker
            if speaker is None:
                VOICES.mkdir(exist_ok=True)
                download_voice(voice.split("#")[0], VOICES)
                speaker = Speaker(VOICES)
            text = line["say"]
            stamp = hashlib.sha1(json.dumps([voice, text]).encode()).hexdigest()
            target.write_bytes(speaker.wav(text, voice, script.get("length_scale", 1.0)))
        done[target.name] = stamp
        done_file.write_text(json.dumps(done, indent=1))
        print(f"{target.name}  {line['means']}")


if __name__ == "__main__":
    sys.exit(main())
