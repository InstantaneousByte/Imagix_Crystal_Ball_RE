# Credits & third-party attributions

This project reverse-engineers and re-hosts an Imagix Crystal Ball locally. The reverse-engineering,
firmware analysis, and tooling are original work (see the methodology note in `README.md`). The
following third-party components are used by the local server and are credited here.

## Silero VAD

`server/orb_server.py` uses **Silero VAD** to detect when the user has stopped speaking (the device
itself ships no end-of-speech VAD, so the local server performs endpointing). 

- Project: https://github.com/snakers4/silero-vad
- Copyright © Silero Team
- License: **MIT**
- Usage here: the pre-trained ONNX model (`silero_vad.onnx`) is run via `onnxruntime`, unmodified.
  The model is **not vendored** in this repository — it is fetched separately at setup time and is
  listed in `.gitignore`. All credit for the model and method belongs to the Silero Team; please
  refer to the upstream repository for its license text, model card, and citation.

## Other dependencies

The local server and tooling also use these open-source components under their respective licenses:
`h2` (HTTP/2), `onnxruntime`, `numpy`, `cryptography`, `esptool`, and `ffmpeg` (opus encoding).
