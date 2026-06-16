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

Pure SD swap, independent of the cloud work. **custom character on the blade is a weekend task.**
The de-cloud doesn't enable this — but it means no cloud persona-check second-guesses the swap.

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

## 3. BACKUP ORB → recon rig pointed at the REAL cloud — 🟢 to set up, high payoff
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
- **DHCP reservation (zero work):** pin the server box's LAN IP on the Flint so the IP already
  in NVS stays correct. Fixes the "IP moved → had to reflash" pain outright.
- **Hostname in NVS + local DNS (the winner):** set `ENDPOINT_STR` *once* to a name
  (`https://orb.home.lan:9000`); repoint by editing one router record. No orb touch, no
  reflash. Cert-hostname mismatch is a non-issue under VERIFY_NONE.
- **Fixed endpoint → reverse proxy:** NVS points at one always-on box (N150?); swap the backend
  by editing the proxy config.
- **❌ Read endpoint from an SD file (heavy, deprioritized):** the URL is read from NVS by
  compiled code; redirecting it to `/sdcard/endpoint.txt` means injecting a file-read at that
  site, and seg3 has no free splice space — so it's the *append-a-new-IROM-segment* restructure
  (64 KB-aligned + integrity fixup), not a byte patch. Adds boot-failure surface to a path
  that's currently a trivial string fetch. Only worth it for true router-independence (the
  off-grid/rural endgame), not on the bench. Hostname + local DNS gets ~95% of the benefit for
  ~2 min of work and zero firmware risk.

## 5. Conversation layer — local LLM persona — 🔴 (the big one)
Replace the cloud AI with the local stack (LM Studio/Qwen + Chatterbox TTS + an STT front-end).
From `observed_protocol.md` the contract is:
1. session-start on `/connect` (done).
2. Receive the device's STT audio upload (device→server h2 stream).
3. STT (whisper) → local LLM (Qwen, Ember persona/system prompt) → TTS → opus.
4. Reply with a `SpeechRecognizer/ExpectSpeech` directive (`$START_JSON`-framed) carrying
   `payload.urls:[<local opus URL>]`.
5. Serve the opus at that URL with `content-type: audio/opus`.

The `captures/events.log` from a normal boot already banks the device→server event shapes;
a BACKUP capture (item 3) of a real voice turn locks down the upload + directive framing.

---

### Suggested order
2-recon-first: do **item 3** (one BACKUP capture) → it de-risks items 2 and 5. **Item 1**
(anims) anytime, needs nothing. **Item 4** is a 2-minute router tweak whenever the IP churn
gets annoying. **Item 5** is the marquee project for when there's a free weekend and patience.
