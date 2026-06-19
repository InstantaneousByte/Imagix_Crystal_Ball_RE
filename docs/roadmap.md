# Roadmap — next steps after the de-cloud (captured 2026-06-16)

The de-cloud is **done and verified** (see `decloud_runbook.md`): firmware accepts any cert
(authmode=NONE), endpoint pinned, Ember boots off the local server. This file is the parking
lot for what comes next, written down while it's fresh. Nothing here is started yet.

Effort tags: 🟢 weekend · 🟡 real project · 🔴 mini-build.

---

## 1. Custom animations on the blade — 🟢 (does NOT need the server)
Personas are encoded `.bin` files on the SD card; the encoder/decoder toolchain is verified
(round-trip confirmed) and the display format is fully decoded. Direct path:
1. Encode frames → `.bin` with the existing `tools/orb_encode.py`.
2. Drop it over an existing slot (e.g. `eb_idle_eb1_64.bin`) on the SD.
3. Boot.

Pure SD swap, independent of the cloud work. **A custom character on the blade is a weekend task.**
The de-cloud doesn't enable this — but it means no cloud persona-check second-guesses the swap.

**SD card asset layout (learned 2026-06-16, main-board FAT32 card):**
- `/sdcard/<Char>/<code>_<profile>/` — persona animation `.bin`s + `character.info` (e.g.
  `Ember/eb1_64/`, with an `Original/` subfolder). Path fmt `/sdcard/%s/%s_%02x/`.
- `/sdcard/<Char>/<lang>/local_voice/` — persona voice lines (`maika_response*`, `expect_*`, …).
- `/sdcard/narrator/` — narrator/system speech (`narrator/local_voice/<lang>/`). NOTE: a
  `bootup.ogg`/`poweron.ogg` also sit at the narrator root, BUT they are **decoys** — the boot
  chimes are NOT played from SD (see "Boot chimes are embedded" below). Editing them does nothing.
- `/sdcard/<NAME_BOOT>_<profile>/` — **boot animation** `.bin` set + `character.info` (e.g.
  `bu1_67/`; `NAME_BOOT=bu1`). 8 bins: `bu_bootup` (logo), `bu_connecting`/`bu_connected`,
  `bu_on` (**= the boot-completion gate `FUN_4202d050` checks for — do NOT rename/break**),
  `bu_inprogress`/`bu_updone` (OTA states), `bu_eb2el`/`bu_el2eb` (character-switch transitions).
  **Boot anims use CPR=2100** (not 2016 — e.g. `bu_bootup_67.bin` = 256200 cols = 122 frames),
  so encode at `--cpr 2100` when swapping the boot logo. Path fmt `/sdcard/%s_%02x/`.
- `/sdcard/narrator/local_voice/<lang>/` — narrator/system voice.
- `/sdcard/ww_model/` — wake-word models (see item 6). `/sdcard/pcb_testing/` — factory SFX.
- Profile suffix `_64`/`_67` is the display variant selected by `baud:N` in `Config.txt`; this
  unit boots the `_67` set (`bu_bootup_67.bin`). Confirm a file's real home with a recursive
  search, and verify a swap took via serial `play_binary: remaining <bytes>` matching the new size.

### Boot chimes are EMBEDDED in firmware (poweron.ogg / bootup.ogg) — 🟡 needs a flash
The boot sounds are NOT SD files — they are compiled into the app image's DROM segment and played
by `play_binary` from a fixed `{id, start_va, end_va}` table. The filename->id matcher uses a
**substring (`strstr`) test**, so ANY play request whose path merely *contains* `bootup.ogg`
(including `/sdcard/narrator/bootup.ogg`) is hijacked to the embedded blob — SD is never read.
That's why swapping the SD file does nothing; serial still shows `play_binary: remaining 53818`.

Verified embedded table (app dumped from ota_0 @0x20000; DROM file->VA = +0x3c1d0000):
```
  entry     id   start VA      end VA        file off   size
  poweron    4   0x3c29326f    0x3c298e70    0x0c326f   23553   (table struct @ file 0x223928)
  bootup     5   0x3c286031    0x3c29326b    0x0b6031   53818   (table struct @ file 0x223934)
```
**Fix (reliable): splice the blob** with `tools/patch_system_audio.py` (replaces ANY of the 26
embedded sounds by name, not just the boot chimes). Writes the new OGG into the slot, zero-pads,
rewrites the end-pointer to the new length, and re-fixes the image checksum+SHA256 (round-trip
verified byte-identical). Constraint: replacement must be **<= the slot size**, so encode
small/mono, e.g. `ffmpeg -i in.wav -ac 1 -ar 48000 -c:a libvorbis -b:a 32k out.ogg`.
**Headroom trick:** repurpose the big unused narration slots — id 0
`character_selection_narrator_voice` (~315 KB) and id 10 `setup_device` (~381 KB), never triggered
on a de-clouded single-persona unit. Full manifest + mechanism in `docs/system_audio.md`. Run on
`app_final.bin` to keep de-cloud patches, then `write_flash 0x20000`.

**Alt (REJECTED, 🔴): "read all sounds from SD".** Verified not viable by patching: `play_binary`
(`FUN_420124d4`) is embedded-only — `if (id<0x1a) play table[id]; else return -1` — there is NO
SD fallback, and the bare-name-vs-path routing is decided upstream. Redirecting to SD would need
injected path-building code (the cmd1 wall: no free segment space, console-task stack overflow).
Deleting embedded blobs does NOT free usable space either (fixed image layout, absolute pointers) —
only zeroes dead weight. The per-slot splice + big-slot repurpose covers the "need more room" case.

## 2. Character "OTA" — push our own asset updates — 🟡 (this is what the de-cloud unlocks)
Now that we own the downchannel, we can emit the cloud's own asset directives. From the
captured protocol (`observed_protocol.md`): `FileManager` / `UpdateDefaultAssets` directives
carry a **versioned** file list + URLs; the device re-syncs when the version is bumped past
what it holds. Recipe:
1. From `orb_server.py`, send a `$START_JSON`-framed `FileManager/UpdateDefaultAssets`
   directive on the downchannel with a **bumped version** and file URLs pointing at our box
   (not CloudFront).
2. Device pulls our zip, treats it as an official asset update.

**Nuance / open work:** the OTA directive is the *trigger*; the actual bins reach the spinning
blade over the **fan TCP-4800 sync**, gated by the version compare. Both are mapped but not yet
driven end-to-end. Fill the exact directive fields from a BACKUP capture (item 3) rather than
inferring. Keystone deliverable: a working `FileManager` directive emitter in `orb_server.py`.

**Update 2026-06-19 — we now know the exact version the device checks.** The `cm2err`
forensics ([`nvs_keys.md`](nvs_keys.md)) recovered the full `my-app` NVS map, including the
animation-version keys the `Force get latest anims` boot path compares against `version: latest`:
`AN_VER_STR` (`1.0`), `EMBER_VER` (`1.0`), and the per-mode `eb_*_ver` (several at `0.0`). So the
directive's `version` just has to exceed these. This also confirms the device **asks for the update
itself on boot** — the no-SD delivery is exactly this hook: answer the request with files served
over plain HTTP from the Mac (`:9001`) and it downloads + fan-syncs on its own, no SD handling.
(The `Force` variant is also what wedged the unit when the de-cloud server stayed silent — so the
emitter must *answer*, not ignore, the boot-time asset request.)

## 3. BACKUP ORB → recon rig pointed at the REAL cloud — 🟢 to set up, high payoff
> **UPDATE 2026-06-18: NOT needed for the conversation turn (item 5).** The entire voice-turn
> contract — Text-Finish token, ExpectSpeech routing, target-device check, the plain-HTTP audio
> requirement — was derived from the decomp and confirmed on hardware without a capture. A real-cloud
> capture is now only a *nice-to-have* for item 2 (the exact `FileManager` asset-update directive
> fields + live CloudFront URLs). Keep this rig in the back pocket for item 2; it's off the critical
> path for everything else.

The authmode=NONE patch makes MITM capture trivial now (it explicitly wasn't before — the cert
wasn't trusted). On the **spare** unit only:
1. Flash `app_authmode_only.bin` (or `app_final.bin`).
2. Point its `ENDPOINT_STR` back at `https://chat-buddyos-us.iviet.com` (or DNS-redirect it).
3. Stand mitmproxy in the middle — the orb accepts mitmproxy's cert (verification off).

Capture, in plaintext, the things we still infer: exact `/connect` request framing, the
`Authorization`/JWT header shape, a live `FileManager` directive with real CloudFront URLs, and
a full conversation turn (`SpeechRecognizer/ExpectSpeech` → `/api/audio` opus). **This capture
is the spec sheet for items 2 and 5.** One good real-cloud session ends the guessing.

> Division of labor going forward: **BACKUP** = recon rig at the real cloud; **primary** =
> de-clouded daily that talks only to our box. (If both share the Flint and BACKUP still has
> the iviet hostname in NVS, a chat-buddyos DNS redirect will pull BACKUP to the box too —
> manage that when setting up the recon unit.)

## 4. Repoint the endpoint without a reflash — 🟢 (do the cheap version)
Goal: change where the orb points without touching the device. Options, by effort:

- **NOW — DHCP reservation + hardcoded IP (works behind a locked ISP gateway):** many ISP
  gateways don't expose LAN DNS or the DHCP-advertised resolver, which blocks the hostname
  trick *at the gateway*. But you can still reserve the server box's LAN IP (MAC → fixed IP) in
  the gateway admin, then hardcode that IP in `ENDPOINT_STR` (e.g. `https://10.0.0.26:9000`).
  With the reservation the IP never drifts, so a hardcoded IP **is** a permanent config — zero
  DNS needed. This is the current setup.

- **UPGRADE (once you own the router) — hostname in NVS + local DNS (the winner):** behind a
  router you control (your own router behind the ISP box = double-NAT but fine since orb↔server
  is local; or ISP bridge mode for a clean single-router setup), you own DHCP + DNS. Then set
  `ENDPOINT_STR` *once* to a name (`https://orb.home.lan:9000`) and add one local DNS record
  (`orb.home.lan → <box-ip>`) in dnsmasq / Pi-hole / AdGuard / Technitium (Windows). Repoint
  later by editing that one record — no orb touch, no reflash. **Cert-hostname mismatch is a
  non-issue under VERIFY_NONE**, which is what makes this painless. Use a dotted name (≥2
  labels) and avoid `.local` (mDNS); `.lan` / `.home` / `.internal` are safe.
  The switch from IP to hostname is a one-line `ENDPOINT_STR` change — no reflash drama.

- **Fixed endpoint → reverse proxy:** NVS points at one always-on box; swap the backend by
  editing the proxy config.
- **❌ Read endpoint from an SD file (heavy, deprioritized):** the URL is read from NVS by
  compiled code; redirecting it to `/sdcard/endpoint.txt` means injecting a file-read at that
  site, and seg3 has no free splice space — so it's the *append-a-new-IROM-segment* restructure
  (64 KB-aligned + integrity fixup), not a byte patch. Only worth it for true router-independence
  (the off-grid endgame). Hostname + local DNS gets ~95% of the benefit for ~2 min of work.

## 5. Conversation layer — local LLM persona — 🔴 (the big one) — ✅ TRANSPORT DONE (2026-06-18)
**The full voice-turn reply path works end-to-end on hardware** (wake → capture → endpoint →
`finish` → ExpectSpeech → the orb fetches & plays our audio → re-listens). Verified in
`server/orb_server.py --reply` with a placeholder beep; see the "WORKING local reply path" section
in [`observed_protocol.md`](observed_protocol.md) for the verified contract. The whole thing was
derived from the decomp — **no mitmproxy capture was needed** (item 3 not required for this).

What got built (all in `orb_server.py`):
- Silero-VAD endpointing (the device has no end-of-speech VAD, so the server owns it).
- **Text-Finish**: answer the `Recognize` POST with a body of `finish` to clear the device's
  "interacting" guard (`+0x5d`) → `listening`→`thinking`.
- **ExpectSpeech** push on the held-open downchannel, auto-targeted at the device's real id
  (learned from upload headers; satisfies `olli_data_check_target_device`).
- **Plain-HTTP audio** on `--port+1`: the audio fetch is a separate TLS-verifying client, so the
  `/api/audio` URL is `http://` (avoids `mbedtls -0x2700`).

**Remaining = the actual AI**, not transport: swap the placeholder opus for
STT (whisper) → local LLM (Qwen, persona/system prompt) → TTS → ogg/opus. The wire contract that
chain must satisfy is now fully nailed down. (Minor follow-ups: a "play-without-relisten" terminal
directive for graceful conversation-end, and the device idle-timer value — neither blocks the build.)

## 6. Change the wake word — 🟢/🟡 (CORRECTED 2026-06-18: the live engine is micro_wake_word) — low priority
> **CORRECTION 2026-06-18 (from live boot logs):** the *running* wake engine is **`micro_wake_word`**,
> not WakeNet — the boot log shows `AFE_VC: wakenet_init: 0` and every detection is logged by
> `Component micro_wake_word` (`WakeWord 'Ember' Detected`). This is the **good** outcome: micro_wake_word
> is the **open, self-trainable** runtime, so a genuinely custom wake word ("Hey Orb", "Robo-Triy", …)
> is a **train-it-yourself** job (esphome/micro_wake_word tooling) rather than Espressif's paid WakeNet
> service. The SD `ww_model/*.bin` notes below describe the WakeNet assets that are present but not the
> active path; treat micro_wake_word as the route for a custom word.

The wake word is **ESP-SR / WakeNet** (`wakenet8`/`wakenet9` quantized = the *engine* in seg3;
`wakeword_load_model_with_id`, `model_num`). **Correction to the earlier note:** the keyword
models are NOT baked into the app — they're plain files on the SD card:

```
/sdcard/ww_model/heyember.bin     <- stock word: "Hey Ember"
/sdcard/ww_model/hiellie.bin      <- stock word: "Hi Ellie"
```

So the stock wake words are literally **"Hey Ember"** and **"Hi Ellie,"** and swapping the word is
an **SD file drop** into `/sdcard/ww_model/`, not an image rebuild. (Engine stays in seg3; only the
keyword model changes.) Options, easiest → hardest:
- **Push-to-talk (🟢, sidestep):** button path (`eBUTTON_ACTIONS function 1` → listening) +
  `afe_disable_wakenet`/`enable_wakeword`. Avoids the word entirely, zero model work.
- **Swap to another *official* WakeNet word (🟢 now):** drop a different official WakeNet `.bin`
  into `/sdcard/ww_model/` and point the loader at it (`wakeword_load_model_with_id`). The hard
  part is just *obtaining* an official model `.bin` for the phrase you want.
- **Custom word, e.g. "Hey Orb" (🟡):** still requires *training* a WakeNet model (Espressif's
  paid service) OR using the open, self-trainable **`micro_wake_word`** (the firmware references it!
  — a different runtime than WakeNet, so an engine swap, but it's the genuinely self-hosted route).
  Integration is now a file drop, not a rebuild.

Verdict: minor want, but the SD-model finding drops it from "🔴 rebuild the image" to "🟢 swap a
file" for the easy cases. Open question: confirm whether the loader will accept a `.bin` not in its
known `model_num` table (i.e. add a 3rd word vs. only replace the two stock ones).

---

## 7. Steady-state hardening — hold the downchannel open + RAM watch — 🟢 (pairs with item 5)
**✅ Hold-open DONE (2026-06-18):** `orb_server.py` now tracks the `/connect` stream and pushes
directives on it just-in-time (that's how the ExpectSpeech reply lands), so the push path required
by items 2 and 5 is in place. RAM watch below still open (low priority).

Observed at idle (capture 2026-06-17, see `docs/observed_protocol.md`): the orb does **not** hold a
persistent push channel. `downchannel_mon_task` fires every ~12 s, tears the session down
(`downchannel_clean_up` + `free_ssl_session_data`) and reopens a fresh `/connect`. Two follow-ups:

- **Hold the stream open server-side.** `orb_server.py` currently lets each `/connect` recycle.
  Keeping the h2 stream open (plus a periodic framed keepalive) stops the 12 s churn and is a
  **prerequisite for pushing anything to the device** — TTS replies, directives, asset OTAs (items 2
  and 5 all need the server to *initiate*, not just answer). Do this first when starting item 5.
- **RAM watch.** Free heap (`free_ssl_session_data: RAM left N`) declines ~2 KB per reconnect cycle
  in the firmware's **own** teardown/reopen path — vendor code the de-cloud never touched, exercised
  more often here only because the bare server lets the channel recycle. ~2.6 MB free -> ~600 KB/hr
  worst-case extrapolated; not urgent and may well plateau. Confirm cheaply: idle ~1 hr and watch
  whether `RAM left` flattens. Holding the stream open (above) sidesteps the churn entirely.

---

### Suggested order
2-recon-first: do **item 3** (one BACKUP capture) → it de-risks items 2 and 5. **Item 1**
(anims) anytime, needs nothing. **Item 4** is a 2-minute router tweak whenever the IP churn
gets annoying. **Item 5** is the marquee project for when there's a free weekend and patience. **Item 7** (hold the stream open + RAM watch) rides along with item 5 — and stops the 12 s reconnect churn in the meantime.
