# server/ — local stand-in for the Imagix cloud

`orb_server.py` — HTTP/2-over-TLS server that replaces the ORB's cloud. Two jobs:

**1. Boot gate.** Answers the ORB's `/connect` downchannel with the framed session-start that
defeats the boot gate: `$START_JSON{"connected":<ms>,"session_id":"<device_id>"}$END_JSON`.
Self-signed cert (accepted because of the control-channel `authmode=NONE` firmware patch). Logs
every stream; ACKs events; holds the downchannel open so it can push directives.

**2. Conversation turn (`--reply`) — WORKING end-to-end on hardware (2026-06-18).** On a speech
upload it: endpoints the utterance with **Silero VAD**, answers the `Recognize` POST with a
`finish` token to clear the device's "interacting" guard, pushes a `SpeechRecognizer/ExpectSpeech`
directive on the downchannel (auto-targeted at the real device id, learned from upload headers),
and serves the reply audio over **plain HTTP/1.1** on `--port+1` (the device's audio client verifies
TLS, so the audio URL must be `http://`). Currently serves a placeholder opus beep — swap in real
STT → local LLM → TTS → ogg/opus. Full wire contract: `../docs/observed_protocol.md`
("WORKING local reply path").

```
pip install h2 onnxruntime numpy cryptography --break-system-packages   # + ffmpeg for opus
# fetch the Silero VAD model next to orb_server.py (gitignored, not vendored):
curl -L -o silero_vad.onnx \
  https://raw.githubusercontent.com/snakers4/silero-vad/master/src/silero_vad/data/silero_vad.onnx
python3 orb_server.py --reply        # boot gate only: drop --reply
```

The server auto-detects the LAN IP and auto-learns the device id, so usually no flags are needed
beyond `--reply`. See `../docs/bench_decloud_steps.md` for the full bench procedure.

## Third-party

Speech endpointing uses **[Silero VAD](https://github.com/snakers4/silero-vad)** (© Silero Team,
MIT License). The `silero_vad.onnx` model is fetched separately and is **not** committed to this
repo. See `../CREDITS.md`.
