"""Synthesize the pilot's lines for the demo video with Piper.

The pilot is a French voice (fr_FR-mls, trained from scratch on
Multilingual LibriSpeech, CC BY 4.0) reading English written the way a
French speaker would say it, for a real French accent.

    python demo-video/make_lines.py            # writes demo-video/out/pilot_XX.wav
"""

import json
import sys
from pathlib import Path

from piper.download_voices import download_voice

from phraseology.speech import Speaker

HERE = Path(__file__).parent
OUT = HERE / "out"
VOICES = HERE / "voices"


def main():
    script = json.loads((HERE / "lines.json").read_text())
    voice = script["pilot_voice"]
    OUT.mkdir(exist_ok=True)
    VOICES.mkdir(exist_ok=True)
    download_voice(voice.split("#")[0], VOICES)
    speaker = Speaker(VOICES)
    for i, line in enumerate(script["lines"]):
        wav = speaker.wav(line["say"], voice, script.get("length_scale", 1.0))
        (OUT / f"pilot_{i:02d}.wav").write_bytes(wav)
        print(f"pilot_{i:02d}.wav  {line['means']}")


if __name__ == "__main__":
    sys.exit(main())
