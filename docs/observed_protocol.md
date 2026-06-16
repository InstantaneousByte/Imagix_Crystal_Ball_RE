# Observed Cloud Protocol — live serial capture 2026-06-15

Source: serial console of a registered unit booting against the **real** cloud
(`https://chat-buddyos-us.iviet.com`). The firmware logs its JSON in plaintext, so this is
ground truth for the boot-critical path even though the traffic did not pass through a proxy.

## Connection
- Device opens an HTTP/2 stream to **`https://chat-buddyos-us.iviet.com/connect`** (nghttp2),
  TCP keepalive enabled (`enable_tcp_keepalive`).
- `-us` host resolves: CNAME → `ae759ebec14e03ef5.awsglobalaccelerator.com` →
  `166.117.191.68` / `15.197.248.55` (**AWS Global Accelerator** — different infra from the
  no-`us` host's Singapore ELB).

## Session-start (server → device) — THE BOOT GATE
On opening `/connect`, the server returns a single small JSON:

```json
{"connected":1781574923904,"session_id":"AIMWLXXXXXXXXXXX"}
```

- `connected`: unix-ms timestamp.
- `session_id`: the device_id (`DEVICE_ID_STR`).

Logged as `data_json_handle: [400] Got new session`. **This message alone advances the device
from the "Artificial Imagery" logo → local persona load → Ember + ready.** The persona content
is NOT sent by the server for the gate — it loads from SD (`CHARACTER_STR=Ember`,
`/sdcard/Ember/...`). The cloud only signals that the session is live.

## Device → server events (after session; server just ACKs)
Emitted via `olli_h2_send_event` (each stamped with `device_id`):
- profile id: `<profile-uuid>`
- user info: `{"volume":80,"mute":false,"timezone":"America/Phoenix","device_id":"AIMWLXXXXXXXXXXX","wakeword_sensitivity_level":"medium","top_led_brightness":100,"bottom_led_brightness":100,"fan_brightness":100}`
- persona: `{"character":"Ember","persona":"buddyos_official_ember_the_dragon","language":"en-US","userprofile_id":"<profile-uuid>"}`
- user state: `{"state":"warm_up_standby"}`

## Server → device background directives (NON-ESSENTIAL for boot)
`FileManager` / `GetLocalAudios` (handled as `update audio`), shape:

```json
{"header":{"namespace":"FileManager","name":"GetLocalAudios","messageId":"...",
  "sessionId":"AIMWLXXXXXXXXXXX","target":{"deviceIDs":["AIMWLXXXXXXXXXXX"]},
  "Client":"CHATBOTCLIENT",...},
 "payload":{"character":"Ember","persona":"Ember the Baby Dragon",
  "files":[{"id":"...","name":"eb_us_15.zip","size":1569795,
            "url":"https://d1hyrqalbm6xdh.cloudfront.net/bya/production/persona-media/.../eb_us_15.zip",
            "order":1,"is_bootup":false,"language":"en-US","version":"1.15"}],
  "media_type":"audio"}}
```

Pushes downloadable persona/system audio (cloudfront zips). Device handles via
`add_new_persona_audio_update`. **A local server can omit these or send empty `files`.**

## Local server MVP (defeats the cloud boot gate)
1. Serve HTTP/2 at `/connect`.
2. On connect, send `{"connected":<unix_ms>,"session_id":"<device_id>"}`.
3. ACK the device's events.

No token minting required — pre-seed NVS with existing creds (already held). Persona and
animations are local to the SD card.

## Still to confirm — needs a frame-level capture (mitmproxy)
The serial log gives JSON *payloads* but not HTTP/2 *framing*:
- The exact request the device makes to `/connect`: method, `:path`, and whether an
  `Authorization` header carries the NVS JWT (`TOKEN_STR`).
- How device→server events are framed (new client streams vs. DATA on the downchannel).
- Content-Type / framing of the server's session-start reply.

Now that `ENDPOINT_STR` holds a real URL again, a single mitmproxy reverse-proxy capture
(`-us` override → box → upstream `166.117.191.68`) will lock these down.

---

# Conversation turn — live serial capture 2026-06-15 (button → STT → TTS)

The same unit, after boot, captured a full voice turn. This is the contract a local-LLM
persona server must satisfy beyond the boot gate.

## Flow
1. **Button** (`eBUTTON_ACTIONS function 1`) → local listening prompt
   (`/sdcard/Ember/en-US/local_voice/maika_response2.ogg`), anim `eb_listening`, VAD opens.
2. **Speech upload** → `olli_h2_send_tts` opens a **new h2 stream** (observed `stream id 21`)
   and streams mic audio to the server. User-state events: `listening` → `thinking`.
   Device receives `on_recv_data_chunk: Text Finish` when the server is done.
3. **Server → device user directive** (`data_json_handle [432] user directive`):
   ```json
   {"header":{"namespace":"SpeechRecognizer","name":"ExpectSpeech","messageId":"...",
     "sessionId":"AIMWLXXXXXXXXXXX","target":{"deviceIDs":["AIMWLXXXXXXXXXXX"]}},
    "payload":{"urls":["https://chat-buddyos-us.iviet.com/api/audio?id=<uuid>"]}}
   ```
   Handled by `olli_user_directive_handle` ("Has Url"). User-state → `responding`.
4. **TTS playback** → device does a plain HTTP GET on the `/api/audio?id=<uuid>` URL,
   receives `content-type: audio/opus`, streams it (observed ~574 KB at ~6 KB/s ≈ server-paced
   real-time TTS), plays it via the audio pipeline (anim `eb_responding`). User-state → `idle`.

## Directive taxonomy (refined)
- `data_json_handle [400] Got new session`  → session-start (boot gate).
- `data_json_handle [465] background directive` → e.g. `FileManager/GetLocalAudios` (asset updates; skippable).
- `data_json_handle [432] user directive`  → e.g. `SpeechRecognizer/ExpectSpeech` (conversation TTS URL).

Headers follow **Amazon AVS** conventions (namespace/name/messageId/dialogRequestId/sessionId/target).

## TTS audio fetch
- Separate HTTP GET to `<endpoint>/api/audio?id=<uuid>`, returns `audio/opus`.
- The URL is fully-qualified and **server-chosen** — a local server can point it at
  `http://<box>:<port>/api/audio?id=<uuid>` and serve the opus from anywhere.

## Local server — full conversational replacement
Beyond the boot-gate MVP, to replace the persona with a local LLM:
1. session-start on `/connect` (boot gate).
2. Receive the STT audio upload (device→server h2 stream).
3. STT (e.g. whisper) → local LLM (Qwen, Ember persona/system prompt) → TTS → opus.
4. Reply with a `SpeechRecognizer/ExpectSpeech` directive carrying `payload.urls:[<local opus URL>]`.
5. Serve the opus at that URL (`content-type: audio/opus`).

Maps directly onto the existing local stack (LM Studio/Qwen + Chatterbox TTS; add an STT front-end).
