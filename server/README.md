# server/ — local stand-in for the Imagix cloud

`orb_server.py` — minimal HTTP/2-over-TLS server that answers the ORB's `/connect`
downchannel with the session-start message that defeats the boot gate:
`{"connected":<ms>,"session_id":"<device_id>"}`. Self-signed cert (accepted because
of the cert-trust firmware patch). Logs every stream; ACKs events.

    pip install h2 --break-system-packages
    python3 orb_server.py --port 9000

This is the boot-gate MVP. The conversational layer (SpeechRecognizer/ExpectSpeech +
/api/audio, backed by STT → local LLM → TTS) is TODO. Set DEVICE_ID to your unit's id.
See ../docs/observed_protocol.md for the wire contract and ../docs/bench_decloud_steps.md
for the full bench procedure.
