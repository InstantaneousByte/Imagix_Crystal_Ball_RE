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

## Downchannel wire framing — RESOLVED from decomp 2026-06-16
Every downchannel message is delimited by literal markers; the device buffers the stream
and extracts via `strstr` (FUN_42020050 -> FUN_4201ff70):

```
$START_JSON<json>$END_JSON
```

No separators, no length fields; multiple messages may be concatenated. The slice BETWEEN
the markers is what reaches `data_json_handle`. **A bare-JSON reply is silently dropped** (no
`Got new session`, no fallback log) because the markers are never found. The session-start
must therefore be sent as `$START_JSON{"connected":<ms>,"session_id":"<id>"}$END_JSON`.
This was the live failure on 2026-06-16: TLS + downchannel opened, server replied with bare
JSON, gate never flipped. `orb_server.py` now wraps via `frame_json()`.

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

> **UPDATED 2026-06-18 — see "Conversation turn — WORKING local reply path" at the bottom of
> this file.** The flow below is from the *real cloud* and was correct for that capture, but two
> details differ when a *local* server drives the turn by pushing on the downchannel rather than
> answering in-band: (a) ExpectSpeech then routes as a `[465] background directive`, not
> `[432] user directive` (both reach the same "Has Url" fetch); and (b) the `/api/audio` fetch is a
> separate TLS-verifying client, so a local server must serve it over **plain HTTP**, not https.
> The verified, reproduced-on-hardware contract is the bottom section.

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


---

# Steady-state, standby/wake & memory — live serial capture 2026-06-17

First long idle capture of the fully de-clouded orb (local server at `https://192.168.8.245:9000`),
entirely cloud-free. Documents what "just sitting there" looks like and the standby<->wake cycle.

## Idle steady state — a ~12 s reconnect loop, NOT a held-open channel
The orb does not keep a persistent push stream. On a fixed cadence:
```
downchannel_mon_task: [1615] Need to check now            # ~every 12 s
asio_io_handler_http2 / free_ssl_session_data             # tear down
downchannel_clean_up: [707] downchannel_clean_up
nghttp_new_session: [871] ... uri: [https://<box>:9000/connect]   # reopen
enable_tcp_keepalive / open_downchannel: done open downchannel
data_json_handle: [400] Got new session: {"connected":<ms>,"session_id":"AIMWLXXXXXXXXXXX"}
```
So the device **polls** `/connect` — it recycles the session roughly every 12 s and re-reads the
framed session-start each time. Implication: a server that only *answers* `/connect` keeps the boot
gate satisfied forever, but to **push** to the device (TTS, directives, asset updates) the server
must hold the h2 stream open instead of letting it recycle. (Roadmap item 7.)

On every reconnect the device also re-pushes telemetry the server can currently just ACK:
- `on_evt_queue_cb [1309] update user infor` — `{"volume":80,"mute":false,"timezone":"America/Phoenix","device_id":"AIMWLXXXXXXXXXXX","wakeword_sensitivity_level":"medium","top_led_brightness":100,"bottom_led_brightness":100,"fan_brightness":100}`
- `on_evt_queue_cb [1318] update user state` — `"warm_up_standby"` -> `"idle"`

These are useful hooks once the server goes interactive.

## Memory across the churn
`free_ssl_session_data` logs free heap each cycle. Over ~2 min idle it declines monotonically by
~2 KB/cycle (2550096 -> 2548032 -> 2546260 -> ... -> 2529524 bytes). Against ~2.6 MB free that's
~600 KB/hr worst-case — not urgent. The decline is in the **firmware's own** teardown/reopen path
(unmodified by the de-cloud; just exercised every 12 s here because the bare server lets the channel
recycle). Could be a small leak or merely session caches / fragmentation that plateau — confirm by
idling ~1 hr and watching whether `RAM left` flattens or keeps falling. Holding the stream open
(roadmap item 7) removes the churn either way. (The larger one-off drop to ~2.44 MB right after wake
is transient audio/animation buffers, not the trend.)

## Standby -> wake — fully local, no cloud
After inactivity the orb drops to light standby (fan off, display idle; WiFi + the 12 s poll stay
alive), then wakes on the wake word — every asset served from SD, nothing from the network:
```
wakekup system form standby
request fan on  /  Set cmd fan ON
local_play: /sdcard/Ember/en-US/local_voice/wake_up.ogg     # local wake SFX
Anim_man: starting play ... [eb_idle_02_eb1_64.bin]         # local idle animation
Disable vad and enable ww                                   # back to wake-word listening
setReadyForUser 1
```
So the whole standby<->wake cycle survives de-clouding intact: the wake SFX, idle animation, and
wake-word engine are all local. The cloud only ever mattered for the conversation turn itself
(STT/LLM/TTS), which is roadmap item 5.

---

# Conversation turn — WORKING local reply path (reproduced on hardware 2026-06-18)

The local server now drives a **complete voice turn end-to-end**: wake → device captures speech →
server endpoints it → server ends the turn → server pushes a reply → **the orb fetches and plays our
audio**, then re-opens the mic for the next turn. Verified on hardware against `server/orb_server.py`
in `--reply` mode (placeholder opus; swapping in real STT→LLM→TTS is now the only remaining work).
Everything below was derived from the decompilation and confirmed live — **no mitmproxy capture was
needed** (roadmap item 3 turned out unnecessary for the conversation turn).

## The full chain (one turn)

```
wake word "Ember" (micro_wake_word; wakenet_init:0)         # device, local
  -> [LISTENNING], speech_recognizer_start_capture          # sets the "interacting" guard
  -> POST /stream  SpeechRecognizer/Recognize  (is-record)  # device streams mic audio up
       body = raw PCM, AUDIO_L16_RATE_16000_CHANNELS_1 (16 kHz / 16-bit / mono LE)
SERVER: Silero VAD endpoints the utterance (device has NO end-of-speech VAD of its own)
  (1) answer the Recognize POST with body b"finish"  ->  device: on_recv_data_chunk [624] Text Finish
  (2) push SpeechRecognizer/ExpectSpeech on the held-open downchannel
DEVICE:
  data_json_handle [418] data json [ ... ExpectSpeech ... ]
  data_json_handle [465] background directive
  olli_background_directive_handle [888] Has Url            # reaches the fetch
  state: listening -> thinking -> responding
  set_system_stream_url -> http GET  http://<server-ip>:9001/api/audio?id=<uuid>
SERVER: [audio] GET /api/audio -> 200 <N> B audio/ogg
DEVICE: ESP_DECODER Detect audio type is OGG -> plays it (anim eb_responding)
  -> Enable vad and disable ww  -> re-opens mic for the next turn (continuous conversation)
```

## (1) Ending the user turn — the `finish` token (THE missing piece)

The device sets an **"interacting" flag** (struct byte `+0x5d`) in `speech_recognizer_start_capture`
and will **reject any pushed directive while it is set** (`olli_background_directive_handle [883]
"Reject this message. User interacting"`). Because the device has no end-of-speech VAD, that flag
otherwise stays set for the whole ~30 s capture window.

The flag is cleared in exactly one place — `on_recv_data_chunk` (`FUN_420211c8`, the nghttp2
`on_data_chunk_recv` callback). On the **Recognize (upstream) stream**, a data chunk that is the
literal 6 bytes **`finish`** triggers:

```c
if (len == 6 && strcmp("finish", data) == 0) {     // on the recognize stream
    struct->interacting = 0;                         // clears +0x5d
    log "Text Finish";                               // [624]
    dispatch(); state -> thinking;                   // FUN_42022ec0 / FUN_42020fcc(0x38,0)
}
```

So the server must **answer the `SpeechRecognizer/Recognize` POST with a body of `finish`** (not a
bare-200-close). This is the app-level "the cloud is done recognizing" signal. `content-type` is
advisory (`text/plain` works). NOTE: this is **not** multipart — `param_5` in the callback is the
chunk *length*, not a type code; the device just wants the raw token.

## (2) The reply directive — ExpectSpeech, pushed on the downchannel = `[465] background`

```json
{"header":{"namespace":"SpeechRecognizer","name":"ExpectSpeech","messageId":"<uuid>",
  "sessionId":"AIMWLXXXXXXXXXXX","target":{"deviceIDs":["AIMWLXXXXXXXXXXX"]},
  "dialogRequestId":"<dialogRequestId of the Recognize that opened this turn>"},
 "payload":{"urls":["http://<server-ip>:9001/api/audio?id=<uuid>"]}}
```

Framed `$START_JSON…$END_JSON` on the held-open downchannel like every downchannel message.

- **Targeting is enforced** (`olli_data_check_target_device`, `FUN_4201d280`): the directive
  dispatches only if `header.sessionId == the device's own id` **OR** that id is in
  `target.deviceIDs`; otherwise it logs `[426] user is_approved` and drops it (received-but-not-mine).
  Set **both** to the device's real id. `orb_server.py` learns the real id from the `device-id` /
  `olli-session-id` upload header and targets the directive at it, so a wrong/placeholder
  `--device-id` no longer matters.
- **Routing:** delivered in-band on the Recognize stream (as the real cloud does), ExpectSpeech is a
  `[432] user directive` → `olli_user_directive_handle`. Pushed on the **downchannel** (as the local
  server does), it is a `[465] background directive` → `olli_background_directive_handle`. **Both
  reach `Has Url` and fetch the URL**, so the downchannel push is sufficient — provided the
  interacting flag was cleared first by `finish` (above).
- The `dialogRequestId` should echo the `Recognize` that opened the turn (matters for in-band
  routing; harmless and correct to include on the downchannel push).

## (3) The audio fetch — MUST be plain HTTP (two TLS clients on the device)

The audio fetch is a **separate HTTP client** from the control channel:

| | Control / downchannel | Audio fetch |
|---|---|---|
| stack | `asio_new_http2_session` (nghttp2) | ESP-ADF `http_stream` → `esp_http_client` (HTTP/1.1) |
| TLS verify | **lenient** — accepts our self-signed cert (authmode patched to NONE on this client) | **verifies** — attaches the Mozilla bundle (`esp_x509_crt_bundle`, `FUN_420c8f68`) |

So an `https://` audio URL dies at the fetch's TLS handshake with
`mbedtls_ssl_handshake -0x2700` (`MBEDTLS_ERR_X509_CERT_VERIFY_FAILED`) — the device never even
sends the GET, and falls back to its local `server_url_timeout.ogg` ("Sorry, I didn't get that").
The `authmode=NONE` de-cloud patch covered only the control client, not this one.

Fix without a second firmware patch: **serve the audio over plain HTTP/1.1** and hand the device an
`http://` URL. `esp_http_client` selects transport by scheme (`http` → plain TCP, no verify), and the
device plays whatever URL we put in `payload.urls` verbatim — including the port. `orb_server.py`
runs a plain-HTTP audio listener on `--port + 1` (default 9001) and points ExpectSpeech there.
(Alternative, if all-https is wanted: patch the audio client's bundle-attach off the same way
`authmode` was patched — a second cert site, not yet done.)

- Response: `200`, body = ogg/opus, `content-type: audio/ogg` (the decoder sniffs the OGG container,
  so the type is advisory). Real TTS should be `ogg`-contained opus (`tts-audio-encoding: ogg`).

## Continuous conversation & stopping

`ExpectSpeech` literally means "reply **and** expect more speech" — after playback the device does
`Enable vad and disable ww` and re-opens the mic with no wake word (stock natural-conversation
behavior). Because the server sends `ExpectSpeech` every turn, the conversation never closes on its
own beyond the device's own idle timer (`start_check_idle_time`). To end a conversation the server
simply **stops offering a turn** (reply without `ExpectSpeech`, or don't reply) and the device idles
back to wake-word standby. No `timeoutInMilliseconds`-style field was observed in the ExpectSpeech
payload parser (`FUN_4201f25c`), so the listen window is device-default, not server-set — the clean
control is whether/when to send `ExpectSpeech`. (A dedicated "play-without-relisten" terminal
directive and the exact idle-timer value are open follow-ups, not blockers.)

## Bonus findings (from misc serial captures, folded in here so they aren't lost)

- **Hidden debug serial console.** The console accepts commands: `help`, `play`, `http`, `cmd1`–
  `cmd5`, `fan` (direct fan/blade command), `cmd_serial`, `cmd_swv`, `cmd_mac`. A direct fan command
  off the UART is a side channel worth exploring for display testing independent of the downchannel.
- **Boot/transition animation inventory (`bu_*.bin`).** `bu_on`, `bu_connecting`, `bu_connected`,
  `bu_bootup`, `bu_inprogress` (the **processing/"thinking"** animation), `bu_updone`, and
  `bu_eb2el` / `bu_el2eb`. **`eb` = Ember, `el` = Ellie** (the two stock personas; see boot log
  `Ellie version [1.03]` and the `[both]` bundle) — so `eb2el`/`el2eb` are **persona-switch
  morphs**, not pipeline states. The per-turn states are the `eb_*` set:
  `eb_listening` → (`bu_inprogress`) → `eb_responding` → `eb_idle`.
- **`cmd=54` boot timeout is benign for the reply path.** `uart_get_data_received_with_timeout:
  Command fail rev timeout 1500, cmd=54` fires every boot — that's the fan-MCU UART handshake dying,
  so the *fan-side* feedback chime never arms. It does **not** touch our audio: our opus plays
  through the ESP32's own codec (the same path that plays `bootup.ogg` / `connected_narrator_voice`).
