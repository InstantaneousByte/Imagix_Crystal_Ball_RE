# TTS: "Fred" (macOS) → Ogg-Opus for the ORB

Status: **CONFIRMED playing on hardware.** Generates a robotic MacinTalk "Fred"
voice line and encodes it to the exact audio format the ORB's ESP32 firmware accepts.

## Why these params (from firmware RE)
- The ESP32 audio path is an **Ogg demuxer** that accepts Vorbis or Opus and checks
  for the `OpusHead` magic → must be **Ogg-Opus** (a proper Ogg stream, NOT raw packets).
- I2S output is single-channel (`I2S_CHANNEL_TYPE_ONLY_LEFT`) → **mono**.
- An `RSP_FILTER` resampler sits in the pipeline → **input sample rate is forgiving**
  (it resamples to the speaker rate; 24 kHz works, 48/16 also fine).
- Protocol labels the stream `tts_audio_encoding` / `Content-Encoding: audio_opus`
  (server-side tag the spoof server sets; the bytes themselves are just Ogg-Opus mono).

## Proven-working spec (verified from a known-good file)
| field        | value          |
|--------------|----------------|
| container    | Ogg            |
| codec        | Opus (OpusHead)|
| channels     | 1 (mono)       |
| input rate   | 24000 Hz       |
| encoder      | ffmpeg/libopus |
| size         | ~6 KB / sentence |

## Recipe (proven — ffmpeg path)
```bash
say -v Fred -o /tmp/fred.aiff "It looks like you're writing a letter."
ffmpeg -y -i /tmp/fred.aiff -c:a libopus -ac 1 -ar 24000 -b:a 24k -application voip fred.opus
```

## Alternative (opus-tools)
```bash
say -v Fred --file-format=WAVE --data-format=LEI16@24000 -o /tmp/fred.wav "..."
opusenc --downmix-mono --bitrate 24 /tmp/fred.wav fred.opus
```

## Reusable shell function (~/.zshrc)
```bash
fredopus() {
  local txt="$1" out="${2:-fred.opus}" tmp="$(mktemp -t fred).aiff"
  say -v Fred -o "$tmp" "$txt"
  ffmpeg -y -loglevel error -i "$tmp" -c:a libopus -ac 1 -ar 24000 -b:a 24k -application voip "$out"
  rm -f "$tmp"; echo "wrote $out"
}
# usage: fredopus "your text" [out.opus]
```

## Character knobs
- `say -r <wpm>` rate is the main robot dial (~140 = slow/mechanical, default ~175).
- Alternates: `-v Zarvox` / `-v Trinoids` for harsher synthetic; `-v Fred` is the classic.

## Pending hookup
Wire into the spoof server's TTS response: LLM text → `fredopus` → stream bytes back
with the `audio_opus` encoding tag → play out orb speaker while firing `eb_talk`,
drop to `eb_idle` when audio ends.
