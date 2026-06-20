#!/usr/bin/env python3
"""
orb_server.py -- minimal local stand-in for the Imagix cloud (boot-gate MVP).

Goal: get the ORB past the "Artificial Imagery" logo to Ember, with NO cloud.
It speaks HTTP/2 over TLS (self-signed; the orb's authmode=NONE firmware patch makes it
accept any cert) and answers the downchannel `/connect` with the one message that
flips the gate:

    {"connected": <unix_ms>, "session_id": "<device_id>"}

Everything else (events the device pushes) is just logged and ACKed. This is a
STARTING POINT -- watch the ORB's serial log and iterate. The conversational
layer (SpeechRecognizer/ExpectSpeech + /api/audio + STT/LLM/TTS) is TODO.

Requires:  pip install h2 cryptography
Optional:  pip install psutil   (cleanest interface enumeration; without it the
           startup IP is found by parsing ipconfig/ip, and a --ip override exists)
Generates: cert.pem / key.pem (self-signed) on first run -- pure-Python via the
           cryptography lib (no openssl needed); falls back to openssl if present.

Run:       sudo python3 orb_server.py        # :9000 (sudo only if port<1024; 9000 is fine without)
           python3 orb_server.py --port 9000
"""
import asyncio, ssl, json, time, os, subprocess, sys, argparse, wave, uuid, base64

from h2.connection import H2Connection
from h2.config import H2Configuration
from h2.events import RequestReceived, DataReceived, StreamEnded, StreamReset, WindowUpdated

# Real device id is needed for a directive's target.deviceIDs/sessionId (the boot
# gate doesn't validate it, but a user directive likely does). Repo keeps the
# placeholder; set the real one via env ORB_DEVICE_ID or --device-id.
DEVICE_ID = os.environ.get("ORB_DEVICE_ID", "AIMWLXXXXXXXXXXX")
CERT, KEY = "cert.pem", "key.pem"

# --- server-side endpointing (Silero VAD) ---------------------------------------
# The device never signals end-of-speech (AFE vad_init:0 -- its only VAD is the
# onset gate that STARTS capture). So the server decides when the user stopped, by
# running Silero over the inbound 16k PCM and ending the turn after a stretch of
# trailing silence. Needs:  pip install onnxruntime numpy   + the silero_vad.onnx
# model next to this file (grab it from the snakers4/silero-vad repo, or point
# SILERO_ONNX at the copy bundled in the pip `silero-vad` package). If the model or
# deps are missing the server still runs -- it just falls back to capture-only.
SILERO_ONNX     = os.environ.get("SILERO_ONNX",
                    os.path.join(os.path.dirname(os.path.abspath(__file__)), "silero_vad.onnx"))
VAD_THRESHOLD   = 0.5      # speech probability over which a frame counts as speech
VAD_HANGOVER_MS = 700      # trailing silence after speech that ends the turn
VAD_MAX_S       = 15.0     # hard cap on one utterance
VAD_INIT_BAIL_S = 6.0      # if no speech by here, give up on the turn
# --- conversation reply (the stop-signal experiment) ----------------------------
# What to do when the endpointer fires:
#   "observe" (default): log + save the utterance, let the stream run out.
#   "reply"            : run the experiment -- close the upstream stream AND push a
#       SpeechRecognizer/ExpectSpeech directive on the held-open downchannel pointing
#       at our /api/audio, then serve a placeholder opus. Watch the serial for
#       listening->thinking->responding + the GET on /api/audio. This probes whether
#       the device advances on (close + ExpectSpeech) alone or also needs the unknown
#       app-level "Text Finish" first. Enable with --reply or ORB_ENDPOINT_ACTION=reply.
ENDPOINT_ACTION  = os.environ.get("ORB_ENDPOINT_ACTION", "observe")
PLACEHOLDER_OPUS = b""                  # ogg-opus bytes, built at startup in reply mode
PUBLIC_IP, PORT  = "127.0.0.1", 9000    # set in main(); used to build the /api/audio URL
# The audio fetch is a SEPARATE client on the device (ESP-ADF http_stream/esp_http_client,
# HTTP/1.1) -- NOT the nghttp2 control channel. It verifies TLS against the ESP cert bundle
# and rejects our self-signed cert (mbedtls_ssl_handshake -0x2700 = X509_CERT_VERIFY_FAILED),
# so it never even GETs the URL. We serve the audio over PLAIN HTTP/1.1 on this port instead
# (no TLS to verify, and HTTP/1.1 to match the client) and hand the device an http:// URL.
AUDIO_PORT       = 0                     # set in main() (default PORT+1)
# --- asset-update push (no-SD anim delivery) ---
# Set via --push-anims <zip>. When armed, the server answers the device's
# FileManager/GetDefaultAssets request with a media_type:"video" directive pointing at
# the RAW .bin(s) served on the audio port. The device does NOT unzip: dwload_file GETs
# each file's url and writes the response body straight to <name>_<code>.bin, then checks
# the byte count against the manifest `size`. So we serve the uncompressed .bin per file.
ASSET_ZIP        = None                  # source zip of .bin frames (None = disabled)
ASSET_ANIMS      = []                    # per-file manifest list, built from the zip at startup
ASSET_FILE_BYTES = {}                    # {bin_name: raw uncompressed bytes} served per-file
ASSET_CHARACTER  = "Ember"
ASSET_VERSION    = "2.0"                 # must exceed NVS EMBER_VER (1.0)
ASSET_MEDIA_FUNCTION = "sd"              # GATE 7: reload_with_new_persona (FUN_4202dc00) rejects
                                         #   media_function "system" unless the persona's internal
                                         #   fw_code field == "eb1" (Ember) / "el1" (Ellie) -- a value
                                         #   the manifest can't set -> MEDIA_FUNC_SYSTEM character ERROR
                                         #   -> reload returns false -> finalize (FUN_42032c2c) skipped
                                         #   -> persona_force_reboot. The "sd" branch (table idx 5) takes
                                         #   no fw_code strcmp, so reload succeeds + finalizes. Download
                                         #   validator accepts any index != 0 ("A"), so "sd" passes both.
ASSET_FILE_ROUTE = "/persona/bin/"       # GET /persona/bin/<name> -> raw .bin (octet-stream)
ASSET_ZIP_ROUTE  = "/persona/anims.zip"  # legacy zip route (kept; device wants raw .bin instead)
ASSET_SERVED     = False                 # set once the device GETs a .bin -> stop re-pushing

def _gen_cert_python():
    """Self-signed cert+key via the cryptography lib -- no external tools, all-OS."""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"orb-local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(u"orb-local")]), critical=False)
            .sign(key, hashes.SHA256()))
    with open(KEY, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    with open(CERT, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))

def _gen_cert_openssl():
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", KEY, "-out", CERT, "-days", "3650", "-subj", "/CN=orb-local",
    ], check=True)

def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    print("[*] generating self-signed cert (cert.pem/key.pem)...")
    # Pure-Python first: no openssl binary / no openssl.cnf needed (Windows-friendly).
    # Cert contents don't matter -- the orb's authmode=NONE patch accepts any cert.
    try:
        _gen_cert_python(); return
    except ImportError:
        pass
    try:
        _gen_cert_openssl(); return
    except Exception as e:
        sys.exit("[!] Could not generate a TLS cert.\n"
                 "    Easiest fix:  pip install cryptography\n"
                 "    (or install OpenSSL and put it on PATH).\n"
                 f"    detail: {e}")

LOGFH = None     # optional server-log file handle; log() tees here when set
def log(*a):
    line = "[" + time.strftime('%H:%M:%S') + "] " + " ".join(str(x) for x in a)
    print(line, flush=True)
    if LOGFH is not None:
        try:
            LOGFH.write(line + "\n"); LOGFH.flush()
        except Exception:
            pass

# Downchannel framing the firmware requires: $START_JSON<json>$END_JSON (no separators).
START_JSON, END_JSON = b"$START_JSON", b"$END_JSON"
def frame_json(obj) -> bytes:
    return START_JSON + json.dumps(obj).encode() + END_JSON

# End-of-turn signal on the SpeechRecognizer/Recognize (upstream) stream. The device's
# nghttp2 on_data_chunk_recv (fw FUN_420211c8) gates on the recognize stream: a chunk of
# len==6 equal to b"finish" -> clears the "interacting" byte (struct+0x5d), logs
# "Text Finish", transitions listening->thinking, and fires dispatch (FUN_42022ec0).
# Until it sees this, the device stays "interacting" and the background-directive handler
# rejects any pushed ExpectSpeech at [883] "Reject this message. User interacting".
# So we answer the Recognize POST with this 6-byte body instead of a bare-200-close.
TEXT_FINISH = b"finish"

CAPTURE = "captures"   # raw device->server event bodies are appended here

def unwrap_frames(buf: bytes):
    """Yield payload(s) from a body, unwrapping $START_JSON..$END_JSON if the device
    framed its upload the same way the downchannel is framed; else yield the body as-is."""
    if START_JSON in buf:
        i = 0
        while True:
            s = buf.find(START_JSON, i)
            if s < 0:
                break
            e = buf.find(END_JSON, s)
            if e < 0:
                break
            yield buf[s + len(START_JSON):e]
            i = e + len(END_JSON)
    elif buf:
        yield buf

def pretty(payload: bytes) -> str:
    try:
        return json.dumps(json.loads(payload), indent=2)
    except Exception:
        return payload.decode("utf-8", "replace")

# --- recon capture helpers (upstream mic audio -> file) -------------------------
# INFERRED uplink format: 16 kHz, 16-bit, mono PCM (LE). Derived from 960640 B over
# ~30 s = 32 KB/s, consistent with the AFE block (single mic, 16k). This is a guess
# until verified BY EAR from the .wav -- the raw .pcm is also saved so it can be
# re-wrapped at other params if the .wav sounds wrong (chipmunk / slow / static).
ASSUMED_RATE, ASSUMED_WIDTH, ASSUMED_CH = 16000, 2, 1

def looks_like_json(buf: bytes) -> bool:
    """True if buf parses as JSON (after unwrapping $START_JSON framing if present)."""
    for payload in unwrap_frames(buf):
        try:
            json.loads(payload); return True
        except Exception:
            return False
    return False

def write_wav(path, pcm: bytes, rate=ASSUMED_RATE, width=ASSUMED_WIDTH, ch=ASSUMED_CH):
    """Wrap raw PCM bytes in a WAV header so the capture is instantly playable."""
    with wave.open(path, "wb") as w:
        w.setnchannels(ch); w.setsampwidth(width); w.setframerate(rate)
        w.writeframes(pcm)

# --- reply path helpers ---------------------------------------------------------
def decode_meta(hdrs):
    """Decode the base64 'meta' request header (the AVS event envelope the device
    attaches to every POST /stream) -> dict, or {} on absence/parse error."""
    m = hdrs.get("meta")
    if not m:
        return {}
    try:
        return json.loads(base64.b64decode(m))
    except Exception:
        return {}

def expect_speech_directive(device_id, url, dialog_request_id=None):
    """SpeechRecognizer/ExpectSpeech (AVS-shaped) carrying the TTS audio URL.
    dialogRequestId MUST match the device's Recognize request or the device can't
    correlate the directive to the active dialog and silently drops it."""
    header = {"namespace": "SpeechRecognizer", "name": "ExpectSpeech",
              "messageId": str(uuid.uuid4()), "sessionId": device_id,
              "target": {"deviceIDs": [device_id]}}
    if dialog_request_id:
        header["dialogRequestId"] = dialog_request_id
    return {"header": header, "payload": {"urls": [url]}}

# --- asset-update (no-SD anim push) emitter ------------------------------------
# Firmware path (see docs/asset_update_protocol.md):
#   device boots -> sends FileManager/GetDefaultAssets (outbound event 0x24)
#   server answers with the directive below. olli_persona_server_event_handle
#   (0x420320ec) -> is_video_type_check_msg (0x42031ffc) routes on payload.media_type:
#       "video" -> type 1 -> parse_new_persona (0x4202ff68) + spawn dwload_file thread
#   -> download_file_handler (0x42029b18) HTTP-GETs payload.url (the zip; client verifies
#      TLS so it MUST be plain http://) -> zip_extract to /sdcard/<Char>/<code>_<profile>/
#   -> writes /sdcard/syncing_tracking.info -> olli_read_persona_from_tracking
#      (0x42031db4) consumes it -> start_sync_data_to_fan_via_tcp (0x4202d7f0) -> blade.
# No SD handling by the user: Mac -> device -> fan.
#
# [U] to confirm by trial (NVS reflash = clean undo):
#   - RESOLVED 2026-06-19: the response header name is "UpdateDefaultAssets" (inbound name
#     enum 0x35). "GetDefaultAssets" is outbound-only and gets dropped. Fixed below.
#   - update_type / media_function exact values, and the version-compare key. version
#     just has to exceed the NVS value (EMBER_VER=1.0); we default high.
#   - per-file compatible_versions shape (string vs array).

def build_animations_manifest(zip_path, sidecar=None):
    """Build the payload.animations[] list from a zip of .bin frames.

    Each entry mirrors the character.info file schema the firmware parses
    (name/origin_name/size/compatible_versions/order/duration/is_bootup). size is the
    UNCOMPRESSED .bin size. order is the zip listing order unless a sidecar overrides.
    An optional sidecar JSON ('<zip>.manifest.json') may set per-file
    {duration, is_bootup, order, compatible_versions, origin_name}; sane defaults otherwise.
    """
    import zipfile
    meta = {}
    side = sidecar or (zip_path + ".manifest.json")
    if os.path.exists(side):
        try:
            meta = json.load(open(side))
        except Exception as e:
            log(f"[anims] sidecar {side} parse failed ({e}); using defaults")
    anims = []
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".bin")]
        for i, n in enumerate(names):
            base = os.path.basename(n)
            info = z.getinfo(n)
            m = (meta.get(base) or meta.get(n) or {})
            # compatible_versions MUST be a STRING (the device reads it with a string-getter and
            # check_and_mirage_video_bin tokenizes it on ',' parsing each as "%d.%d"). An array
            # ["1.0"] hands the string-getter nothing -> stored empty -> the mirage SKIPS the file
            # ("filenode->compatible_version empty") -> never aliased into the playlist -> not shown.
            # Accept a list/str from the sidecar; always emit a comma-joined string. Default "1.0".
            _cv = m.get("compatible_versions", "1.0")
            if isinstance(_cv, (list, tuple)):
                _cv = ",".join(str(x) for x in _cv)
            anims.append({
                "name": base,
                "origin_name": m.get("origin_name", base),
                "size": m.get("size", info.file_size),
                "compatible_versions": _cv,
                "order": m.get("order", i + 1),
                "duration": m.get("duration", 4000),   # ms; [U] semantic per-file
                "is_bootup": bool(m.get("is_bootup", False)),
            })
    return anims

# Firmware character table (PTR_DAT_42002e24): the manifest "persona" field is matched by
# persona_compare_local_data against DISPLAY names {"Ellie the fairy","Ember the Baby Dragon","both"}
# (-> codes Ellie/Ember/both). The buddyos_official_* identifier does NOT match and aborts the sync
# (RET_UNKNOW). So the manifest persona must be the display name, not the identifier.
CHARACTER_DISPLAY = {"Ember": "Ember the Baby Dragon", "Ellie": "Ellie the fairy"}

def file_manager_video_directive(device_id, zip_url, files, *,
                                 persona=None, character="Ember", persona_name=None, version="2.0",
                                 update_type="incremental", media_function="sd",
                                 root_path=None, name="UpdateDefaultAssets"):
    """The inbound directive that makes the device download + fan-sync anims (no SD).

    header.name MUST be "UpdateDefaultAssets" -> inbound name enum 0x35 -> the asset
    dispatcher's video path (olli_background_directive_handle -> FUN_420320ec(.,1)).
    NOTE: "GetDefaultAssets" is the OUTBOUND request name only; it is not in the inbound
    name table, so the device drops it after logging "background directive" (verified
    on hardware 2026-06-19). namespace "FileManager" -> enum 0x11.

    PAYLOAD SHAPE (hardware-reverse 2026-06-19; FUN_42030d84 + parse_new_persona_from_server
    FUN_420302e8): the video path (param_2!=0) needs BOTH:
      - payload.dependencies : a JSON array (even empty). If missing/not-an-array the parser
        logs 'new_persona_not_the_same' and returns before touching animations.
      - payload.animations[] : each entry is itself a FULL persona manifest
        {name, persona, character, version, update_type, total_files, media_type:'video',
         media_function, files:[ {name, url, size, compatible_versions, order, duration,
         is_bootup} ]}. Each FILE carries its OWN `url` (the zip; the device GETs it over
         plain HTTP and zip-extracts the named file). Only when a file in files[] needs
         updating does the parser bump +0x78 (needs-download) and the dwload_file thread spawns.
    A flat animations[] of bare file entries with one top-level url parses fine but never
    sets +0x78, so nothing downloads (verified on hardware 2026-06-19).
    """
    persona = persona or f"buddyos_official_{character.lower()}"
    persona_name = persona_name or CHARACTER_DISPLAY.get(character, "both")
    build_date = time.strftime("%Y-%m-%d %H:%M:%S")
    # each file should carry its own raw-.bin url; fall back to the passed url only if absent
    mfiles = [f if f.get("url") else dict(f, url=zip_url) for f in files]
    manifest = {                                # one persona manifest = one animations[] entry
        "name": character,                      # 2nd %s of /sdcard/<code>/<name>_<ver>/character.info
        "persona": persona_name,                # DISPLAY name -> firmware character table (NOT the id)
        "character": character,
        "version": version,
        "update_type": update_type,
        "total_files": len(mfiles),
        "build_date": build_date,
        "is_new_version": True,
        "is_new_character": False,
        "is_factory_update": False,
        "media_type": "video",                  # FUN_420302e8 requires == "video"
        "media_function": media_function,        # valid: system|riddle|music|story|sd (NOT "both";
                                                 #   parser rejects table idx 0 "A" -> media_type_MEDIA_FUNC).
                                                 #   GATE 7: use "sd" for custom single-file pushes -- the
                                                 #   "system" reload path demands an internal fw_code ("eb1")
                                                 #   the manifest can't carry; "sd" skips that strcmp.
        "files": mfiles,
    }
    header = {"namespace": "FileManager", "name": name,
              "messageId": str(uuid.uuid4()), "sessionId": device_id,
              "target": {"deviceIDs": [device_id]}}
    payload = {
        "persona": persona,
        "character": character,
        "media_type": "video",                  # <- routing trigger (is_video_type_check_msg)
        "media_function": media_function,
        "version": version,                     # must exceed NVS EMBER_VER (1.0)
        "update_type": update_type,
        "total_files": len(mfiles),
        "build_date": build_date,
        "is_new_version": True,
        "is_new_character": False,
        "is_factory_update": False,
        "root_path": root_path or f"/sdcard/{character}",
        "dependencies": [],                     # MUST be a JSON array or the video path bails
        "animations": [manifest],               # each entry = full persona manifest w/ files[]
    }
    return {"header": header, "payload": payload}

def make_placeholder_opus():
    """Build a short ogg-opus tone via ffmpeg so /api/audio has something to serve in
    the reply experiment (the device's decoder sniffs container -> 'OPUS'). Empty if
    ffmpeg is missing."""
    import shutil, tempfile
    if not shutil.which("ffmpeg"):
        log("[reply] ffmpeg not found -- /api/audio will 503; the reply experiment needs ffmpeg.")
        return b""
    try:
        p = os.path.join(tempfile.gettempdir(), "orb_reply.opus")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                        "-i", "sine=frequency=660:duration=1.2:sample_rate=48000",
                        "-ac", "1", "-c:a", "libopus", "-b:a", "48k", "-f", "ogg", p],
                       check=True)
        with open(p, "rb") as f:
            return f.read()
    except Exception as e:
        log(f"[reply] placeholder opus build failed ({e})")
        return b""

# --- Silero VAD over onnxruntime ------------------------------------------------
# Speech endpointing uses Silero VAD (https://github.com/snakers4/silero-vad),
# (c) Silero Team, MIT License. The silero_vad.onnx model is fetched separately
# (gitignored, not vendored) and run unmodified via onnxruntime. See ../CREDITS.md.
class SileroVAD:
    """Streaming Silero v5 VAD. Feed 512-sample (16k) float32 frames -> speech prob.
    v5 REQUIRES a 64-sample context prepended to each frame (576 samples in); feeding
    a bare 512 returns a flat ~0 for everything (the failure mode that cost us an hour).
    State + context are per-stream (returned/threaded by the caller)."""
    SR, FRAME, CTX = 16000, 512, 64
    def __init__(self, path):
        import onnxruntime as ort               # lazy: only needed when VAD is on
        self.np = __import__("numpy")
        self.sess = ort.InferenceSession(path, providers=["CPUExecutionProvider"])
    def new_state(self):
        np = self.np
        return [np.zeros((2, 1, 128), np.float32), np.zeros((1, self.CTX), np.float32)]
    def prob(self, frame_f32, st):
        np = self.np
        state, ctx = st
        inp = np.concatenate([ctx, frame_f32.reshape(1, -1)], axis=1)   # 64 ctx + 512
        out, state = self.sess.run(None, {"input": inp, "state": state,
                                          "sr": np.array(self.SR, np.int64)})
        st[0] = state; st[1] = inp[:, -self.CTX:]
        return float(out[0, 0])

class Endpointer:
    """Per-stream VAD gate. Feed raw PCM16LE bytes as they stream in; it tells you
    once when the user stopped talking (or the turn capped / never started)."""
    FB = SileroVAD.FRAME * ASSUMED_WIDTH        # bytes per 512-sample frame
    def __init__(self, vad):
        self.vad = vad; self.st = vad.new_state(); self.np = vad.np
        self.buf = b""; self.started = False; self.silence = 0
        self.frames = 0; self.maxprob = 0.0; self.fired = False
        self.hang      = max(1, round(VAD_HANGOVER_MS / 1000 * SileroVAD.SR / SileroVAD.FRAME))
        self.max_fr    = round(VAD_MAX_S      * SileroVAD.SR / SileroVAD.FRAME)
        self.bail_fr   = round(VAD_INIT_BAIL_S * SileroVAD.SR / SileroVAD.FRAME)
    def feed(self, data):
        """-> (kind, seconds) exactly once: 'endpoint' | 'maxcap' | 'initbail'; else (None, None)."""
        if self.fired:
            return (None, None)
        self.buf += data
        while len(self.buf) >= self.FB:
            raw, self.buf = self.buf[:self.FB], self.buf[self.FB:]
            frame = self.np.frombuffer(raw, dtype="<i2").astype(self.np.float32) / 32768.0
            p = self.vad.prob(frame, self.st)
            self.frames += 1; self.maxprob = max(self.maxprob, p)
            secs = self.frames * SileroVAD.FRAME / SileroVAD.SR
            if p >= VAD_THRESHOLD:
                self.started = True; self.silence = 0
            elif self.started:
                self.silence += 1
                if self.silence >= self.hang:
                    self.fired = True
                    return ("endpoint", secs - VAD_HANGOVER_MS / 1000)
            elif self.frames >= self.bail_fr:
                self.fired = True
                return ("initbail", secs)
            if self.started and self.frames >= self.max_fr:
                self.fired = True
                return ("maxcap", secs)
        return (None, None)

VAD = None      # shared, loaded once at startup (None => capture-only fallback)
def load_vad():
    global VAD
    try:
        if not os.path.exists(SILERO_ONNX):
            log(f"[vad] no model at {SILERO_ONNX} -- capture-only (no endpointing).")
            return
        VAD = SileroVAD(SILERO_ONNX)
        log(f"[vad] Silero loaded ({os.path.basename(SILERO_ONNX)}); "
            f"thr={VAD_THRESHOLD} hang={VAD_HANGOVER_MS}ms cap={VAD_MAX_S}s "
            f"act={ENDPOINT_ACTION}")
    except Exception as e:
        log(f"[vad] disabled ({type(e).__name__}: {e}) -- capture-only. "
            f"`pip install onnxruntime numpy` to enable.")
        VAD = None

class Orb(asyncio.Protocol):
    def __init__(self):
        cfg = H2Configuration(client_side=False, header_encoding="utf-8")
        self.conn = H2Connection(config=cfg)
        self.bodies = {}            # stream_id -> bytes accumulated
        self.paths = {}             # stream_id -> :path
        self.headers = {}           # stream_id -> request headers dict
        self.endpointers = {}       # stream_id -> Endpointer (audio streams only)
        self.responded = set()      # stream_ids we've already sent a final response on
        self.downchannel_sid = None # the open /connect stream we can push directives on
        self.dialog = {}            # stream_id -> dialogRequestId (from the meta header)
        self.dev_id = None          # the device's real id, learned from upload headers
        self.pushed_anims = False   # one-shot: anim-update directive sent this connection

    def connection_made(self, transport):
        self.transport = transport
        peer = transport.get_extra_info("peername")
        log(f"=== ORB connected from {peer} ===")
        self.conn.initiate_connection()
        transport.write(self.conn.data_to_send())

    def data_received(self, data):
        try:
            events = self.conn.receive_data(data)
        except Exception as e:
            log("h2 error:", e); self.transport.close(); return
        for ev in events:
            if isinstance(ev, RequestReceived):
                hdrs = dict(ev.headers)
                self.paths[ev.stream_id] = hdrs.get(":path", "")
                self.headers[ev.stream_id] = hdrs
                self.bodies[ev.stream_id] = b""
                method = hdrs.get(":method", "").upper()
                # The device attaches an AVS envelope (base64) in the 'meta' header on
                # every POST /stream, and tags the actual speech upload with
                # is-record: true. Pull the dialogRequestId (our ExpectSpeech must echo
                # it) and the namespace/name; do NOT log the payload (it can carry WiFi
                # creds / profile id).
                ehdr = (decode_meta(hdrs).get("event") or {}).get("header") or {}
                dlg = ehdr.get("dialogRequestId")
                self.dialog[ev.stream_id] = dlg
                is_speech = hdrs.get("is-record", "").lower() == "true"
                # Learn the device's REAL id from its headers. Directives are gated by
                # olli_data_check_target_device: the device drops anything whose sessionId
                # AND target.deviceIDs don't match its own id (logs [426] is_approved, no
                # dispatch). So target directives at the learned id, not a typed --device-id.
                did = hdrs.get("device-id") or hdrs.get("olli-session-id")
                if did and did != self.dev_id:
                    self.dev_id = did
                    note = ("" if DEVICE_ID == did else
                            f"  (configured id differs -> using learned id for directive targets)")
                    log(f"[reply] learned device-id from headers: {did}{note}")
                if VAD is not None and is_speech:
                    self.endpointers[ev.stream_id] = Endpointer(VAD)
                summary = (f"  [{ehdr.get('namespace')}/{ehdr.get('name')}"
                           f"{' is-record' if is_speech else ''} dlg={dlg}]" if ehdr else "")
                log(f"stream {ev.stream_id}: {method or '?'} {hdrs.get(':path','?')}{summary}")
                # --- no-SD anim push: answer the device's own asset request ----------
                # The boot-time "Force get latest anims" arrives as FileManager/
                # GetDefaultAssets (or GetLocalAudios). If armed, push the video directive.
                if (ASSET_ZIP and not self.pushed_anims and not ASSET_SERVED
                        and ehdr.get("namespace") == "FileManager"
                        and ehdr.get("name") in ("GetDefaultAssets", "GetLocalAudios")):
                    self._push_anims()
            elif isinstance(ev, DataReceived):
                if ev.stream_id in self.responded:
                    # We already closed this stream early (reply experiment); the device
                    # may still be flushing DATA. Ack for flow control, otherwise ignore.
                    try:
                        self.conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                    except Exception:
                        pass
                    continue
                self.bodies[ev.stream_id] = self.bodies.get(ev.stream_id, b"") + ev.data
                self.conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
                epr = self.endpointers.get(ev.stream_id)
                if epr is not None:
                    kind, secs = epr.feed(ev.data)
                    if kind:
                        self.on_endpoint(ev.stream_id, kind, secs, epr.maxprob)
            elif isinstance(ev, StreamEnded):
                self.handle(ev.stream_id)
            elif isinstance(ev, StreamReset):
                # Device RST'd the stream -- it re-triggered (wake/button) before the
                # turn finished, so the prior capture is abandoned mid-upload. Flush
                # whatever PCM arrived (tagged _partial) instead of dropping it, then
                # forget the stream. No h2 response -- the stream is already dead.
                if self.bodies.get(ev.stream_id):
                    log(f"stream {ev.stream_id}: RST by device (superseded) -- flushing partial")
                    self._persist(ev.stream_id, reset=True)
                self._forget(ev.stream_id)
        out = self.conn.data_to_send()
        if out: self.transport.write(out)

    def on_endpoint(self, sid, kind, secs, maxprob):
        """VAD decided the turn is over. Observe-only by default: log + save the
        trimmed utterance (what STT would receive). In reply mode it ends the turn the
        way the firmware expects -- a b"finish" body on the Recognize stream (Text Finish)
        to clear the device's "interacting" guard, then an ExpectSpeech push carrying the
        /api/audio URL -- rather than relying on a bare h2 close (which the device ignores)."""
        body = self.bodies.get(sid, b"")
        dur = len(body) / (ASSUMED_RATE * ASSUMED_WIDTH * ASSUMED_CH)
        note = {"endpoint": "user stopped (trailing silence)",
                "maxcap":   "hit max-utterance cap",
                "initbail": "no speech detected -- bailed"}.get(kind, kind)
        log(f"stream {sid}: VAD {kind} @ {secs:.2f}s (maxprob={maxprob:.2f}) -- {note}; "
            f"buffered {dur:.2f}s")
        if kind != "initbail" and body:
            try:
                os.makedirs(CAPTURE, exist_ok=True)
                ub = os.path.join(CAPTURE, f"utt_{time.strftime('%Y%m%d_%H%M%S')}_s{sid}")
                with open(ub + ".pcm", "wb") as f:
                    f.write(body)
                frame = ASSUMED_WIDTH * ASSUMED_CH
                write_wav(ub + ".wav", body[: (len(body) // frame) * frame])
                log(f"stream {sid}: utterance -> {ub}.wav  (feed this to STT)")
            except Exception as e:
                log("utterance write failed:", e)
        if ENDPOINT_ACTION == "reply" and sid not in self.responded:
            # (1) End the user turn IN-BAND on the Recognize stream: answer the POST
            #     with a body of b"finish" (not a bare 200). fw FUN_420211c8 matches
            #     that 6-byte chunk on this stream -> clears "interacting" (struct+0x5d),
            #     "Text Finish", listening->thinking. THEN (2) push ExpectSpeech on the
            #     downchannel; with interacting now clear it passes the [883] guard ->
            #     olli_background_directive_handle -> "Has Url" -> GET /api/audio.
            try:
                self.conn.send_headers(sid, [(":status", "200"),
                                             ("content-type", "text/plain")])
                self.conn.send_data(sid, TEXT_FINISH, end_stream=True)
                self.responded.add(sid)
            except Exception as e:
                log(f"stream {sid}: Text-Finish send failed ({e})")
            aid = uuid.uuid4().hex
            # PLAIN HTTP (not https): the device's audio client rejects our self-signed
            # cert at the TLS handshake (-0x2700). http:// on AUDIO_PORT avoids TLS entirely.
            url = f"http://{PUBLIC_IP}:{AUDIO_PORT}/api/audio?id={aid}"
            dlg = self.dialog.get(sid)
            target_id = self.dev_id or DEVICE_ID   # learned real id beats the typed one
            ok = self.push_directive(expect_speech_directive(target_id, url, dlg))
            out = self.conn.data_to_send()
            if out: self.transport.write(out)
            log(f"stream {sid}: REPLY -> sent Text-Finish (b'finish') on recognize stream + "
                f"{'pushed' if ok else 'FAILED to push'} ExpectSpeech (id={aid}, "
                f"target={target_id}, dlg={dlg}); watch for [Text Finish] -> Has Url -> GET /api/audio")

    def push_directive(self, obj):
        """Frame + send a directive on the held-open downchannel (kept open).
        Returns True if a downchannel was available and the send didn't raise."""
        sid = self.downchannel_sid
        if sid is None:
            log("[reply] no open downchannel to push on (device between reconnects?)")
            return False
        try:
            self.conn.send_data(sid, frame_json(obj), end_stream=False)
            out = self.conn.data_to_send()
            if out: self.transport.write(out)
            return True
        except Exception as e:
            log(f"[reply] push failed on stream {sid}: {e}")
            return False

    def _push_anims(self):
        """Build + push the media_type:'video' asset-update directive on the downchannel,
        targeted at the learned device id, pointing each file at its raw .bin on the audio port."""
        target_id = self.dev_id or DEVICE_ID
        base = f"http://{PUBLIC_IP}:{AUDIO_PORT}{ASSET_FILE_ROUTE}"
        # per-file url -> the raw .bin (device writes the body straight to disk, no unzip)
        files = [dict(f, url=base + f["name"]) for f in ASSET_ANIMS]
        directive = file_manager_video_directive(
            target_id, base, files,
            character=ASSET_CHARACTER, version=ASSET_VERSION,
            media_function=ASSET_MEDIA_FUNCTION)
        ok = self.push_directive(directive)
        if ok:
            self.pushed_anims = True
            log(f"[anims] pushed {directive['header']['namespace']}/{directive['header']['name']} "
                f"-> {base}<name>  ({len(ASSET_ANIMS)} raw .bin file(s), media_function="
                f"{directive['payload']['media_function']}, version={ASSET_VERSION}, "
                f"target={target_id})")
        else:
            log("[anims] could not push (no open downchannel yet) -- will retry on next request")

    def _persist(self, sid, reset=False):
        """Route a finished (or reset) stream body to disk; return is_audio.
        reset=True => the device RST'd mid-upload; we still flush what arrived,
        tagged _partial, so re-trigger sessions don't silently lose audio."""
        path = self.paths.get(sid, "")
        body = self.bodies.get(sid, b"")
        hdrs = self.headers.get(sid, {})
        ctype = hdrs.get("content-type", "")
        # Is this the mic-audio upload? Every device->server upload is POST /stream
        # (events AND audio share the path), so path can't classify it -- content does:
        # anything that doesn't parse as JSON (framed or bare) is treated as binary
        # audio. We never decode/print binary (that's what flooded the terminal).
        is_audio = bool(body) and not path.endswith("/connect") and not looks_like_json(body)
        if is_audio:
            # Save raw .pcm (ground truth) + a .wav at 16k/16-bit/mono so it opens
            # instantly. (Format verified by analysis of the first real capture.)
            base = "?"
            try:
                os.makedirs(CAPTURE, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                base = os.path.join(CAPTURE, f"audio_{ts}_s{sid}{'_partial' if reset else ''}")
                with open(base + ".pcm", "wb") as f:
                    f.write(body)
                frame = ASSUMED_WIDTH * ASSUMED_CH
                write_wav(base + ".wav", body[: (len(body) // frame) * frame])
            except Exception as e:
                log("audio capture write failed:", e)
            n = len(body)
            secs = n / (ASSUMED_RATE * ASSUMED_WIDTH * ASSUMED_CH)
            log(f"stream {sid} AUDIO[{path}]{' RST' if reset else ''} ctype={ctype or '?'} "
                f"clen={hdrs.get('content-length','?')} {n} B "
                f"= {secs:.2f}s @16k/16-bit/mono -> {base}.wav  (.pcm saved)")
        elif body:
            # JSON event -- bank the raw shape (device->server event spec) and log it.
            try:
                os.makedirs(CAPTURE, exist_ok=True)
                with open(os.path.join(CAPTURE, "events.log"), "ab") as f:
                    f.write(f"# {time.strftime('%H:%M:%S')} stream {sid} {path} ctype={ctype}\n".encode())
                    f.write(body + b"\n")
            except Exception as e:
                log("capture write failed:", e)
            for payload in unwrap_frames(body):
                log(f"stream {sid} body[{path}]:\n{pretty(payload)}")
        return is_audio

    def _forget(self, sid):
        """Drop per-stream state so the dicts don't grow over a long session."""
        self.bodies.pop(sid, None)
        self.paths.pop(sid, None)
        self.headers.pop(sid, None)
        self.endpointers.pop(sid, None)
        self.responded.discard(sid)
        self.dialog.pop(sid, None)

    def handle(self, sid):
        if sid in self.responded:
            # We already closed this stream early (VAD action); just bank the full body.
            self._persist(sid); self._forget(sid); return
        is_audio = self._persist(sid)
        path = self.paths.get(sid, "")
        if path.startswith("/api/audio"):
            # TTS fetch: the device GETs the URL we placed in ExpectSpeech.payload.urls.
            if PLACEHOLDER_OPUS:
                log(f"stream {sid}: -> /api/audio  serving {len(PLACEHOLDER_OPUS)} B audio/opus")
                self.conn.send_headers(sid, [(":status", "200"),
                                             ("content-type", "audio/opus"),
                                             ("content-length", str(len(PLACEHOLDER_OPUS)))])
                self.conn.send_data(sid, PLACEHOLDER_OPUS, end_stream=True)
            else:
                log(f"stream {sid}: -> /api/audio 503 (no opus; need ffmpeg + --reply)")
                self.conn.send_headers(sid, [(":status", "503")], end_stream=True)
        elif path.endswith("/connect"):
            # The downchannel. Send the session-start that opens the gate, keep the
            # stream OPEN (don't end_stream), and TRACK it so on_endpoint can push a
            # directive on it (item 7 = hold open; directives ride this stream).
            #
            # WIRE FRAMING (verified in fw: FUN_42020050 -> FUN_4201ff70, strstr on
            # "$START_JSON"/"$END_JSON"): every downchannel message MUST be wrapped as
            #     $START_JSON<json>$END_JSON
            # concatenated with no separators/length fields. The device buffers the
            # stream and only hands a frame to data_json_handle when it finds BOTH
            # markers. Bare JSON is silently ignored (no "Got new session").
            self.downchannel_sid = sid
            msg = frame_json({"connected": int(time.time()*1000),
                              "session_id": DEVICE_ID})
            log(f"stream {sid}: -> session-start {msg!r} (downchannel open + tracked)")
            self.conn.send_headers(sid, [(":status", "200"),
                                         ("content-type", "application/json")])
            self.conn.send_data(sid, msg, end_stream=False)
            out = self.conn.data_to_send()
            if out: self.transport.write(out)
            # no-SD anim push: deliver on EVERY downchannel open (the device only sends
            # GetLocalAudios once, during busy boot; the keepalive reconnects every ~12s
            # are when it's idle and reliably reads the downchannel). Stop once it GETs the zip.
            if ASSET_ZIP and not self.pushed_anims and not ASSET_SERVED:
                self._push_anims()
            return   # keep it open -- do NOT _forget
        else:
            # Event OR the audio upload: close the stream cleanly with end_stream=True.
            # Leaving the audio turn half-open (server never closes it) is what left
            # telemetry riding the same stream id and made wake/VAD go squirrely.
            log(f"stream {sid}: -> 200 {'(audio turn closed)' if is_audio else 'ACK (event)'}")
            self.conn.send_headers(sid, [(":status", "200")], end_stream=True)
        out = self.conn.data_to_send()
        if out: self.transport.write(out)
        self._forget(sid)

    def connection_lost(self, exc):
        self.downchannel_sid = None
        log("=== ORB disconnected ===")

def host_lan_ips():
    """Return (best_ip, [(iface, ip), ...]), preferring physical Ethernet over Wi-Fi,
    and skipping VPN / virtual adapters (Mullvad, WireGuard, WSL/Hyper-V, VMware, ...).

    The server binds 0.0.0.0 so this is informational only -- it's just to print the
    right ENDPOINT_STR. We need interface NAMES to tell Ethernet from a VPN tunnel, so:
      1. psutil if installed (best),
      2. else parse `ipconfig` (Windows) / `ip -4 addr` (Linux) for name+IP,
      3. else the default-route socket trick (last resort; a VPN can fool this one,
         which is exactly why it's last).
    """
    import socket as S, subprocess, re, sys
    SKIP = ("virtual", "vethernet", "vmware", "vbox", "virtualbox", "hyper-v", "loopback",
            "docker", "wsl", "bluetooth", "ppp",
            "mullvad", "wireguard", "wintun", "nordlynx", "nordvpn", "proton",
            "openvpn", "vpn", "tailscale", "zerotier", "wg-", "utun", "tap", "tun")

    def rank(name):
        n = name.lower()
        if any(k in n for k in ("wi-fi", "wifi", "wlan", "wireless")) or n.startswith("wl"):
            return 2                                   # Wi-Fi
        if "ethernet" in n or n.startswith(("eth", "en", "em")) or "local area" in n:
            return 0                                   # physical Ethernet
        if "unknown" in n:
            return 3                                   # ipconfig "Unknown adapter" = usually VPN
        return 1

    def pick(pairs):
        scored = []
        for name, ip in pairs:
            n = name.lower()
            if ip.startswith("127.") or any(s in n for s in SKIP):
                continue
            scored.append((rank(name), name, ip))
        if not scored:
            return None
        scored.sort(key=lambda c: (c[0], c[1]))
        disp = [(nm.split("|")[-1].strip(), ip) for _, nm, ip in scored]
        return scored[0][2], disp

    # 1) psutil
    try:
        import psutil
        stats = psutil.net_if_stats()
        pairs = []
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup:
                continue
            for a in addrs:
                if a.family == S.AF_INET:
                    pairs.append((name, a.address))
        got = pick(pairs)
        if got:
            return got
    except Exception:
        pass

    # 2) OS network config (names available, no extra deps)
    try:
        if sys.platform.startswith("win"):
            txt = subprocess.run(["ipconfig"], capture_output=True, text=True).stdout
            pairs, cur = [], None
            for line in txt.splitlines():
                m = re.match(r"^(.+?) adapter (.+):\s*$", line)   # "Ethernet adapter Ethernet:"
                if m:
                    cur = f"{m.group(1)}|{m.group(2)}"            # keep "kind|name"
                    continue
                m = re.search(r"IPv4 Address[ .]*:\s*([0-9.]+)", line)
                if m and cur:
                    pairs.append((cur, m.group(1)))
        else:
            txt = subprocess.run(["ip", "-o", "-4", "addr", "show"],
                                 capture_output=True, text=True).stdout
            pairs = []
            for line in txt.splitlines():
                p = line.split()
                if len(p) >= 4 and p[2] == "inet":
                    pairs.append((p[1], p[3].split("/")[0]))
        got = pick(pairs)
        if got:
            return got
    except Exception:
        pass

    # 3) last resort: default-route source IP (a VPN can capture this)
    s = S.socket(S.AF_INET, S.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip, [("default-route", ip)]

async def _audio_http_client(reader, writer):
    """Plain HTTP/1.1 handler for the device's audio fetch. The ORB's http_stream opens
    a fresh HTTP/1.1 connection to the ExpectSpeech url; serving it here over plain HTTP
    (no TLS) sidesteps the cert-verify failure that blocks the https path. The decoder
    sniffs the OGG container, so content-type is advisory. We answer GET (and HEAD) for
    /api/audio with the placeholder opus; everything else 404s."""
    peer = writer.get_extra_info("peername")
    try:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except Exception:
            writer.close(); return
        reqline = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
        bits = reqline.split(" ")
        method = (bits[0] if bits else "").upper()
        target = bits[1] if len(bits) > 1 else ""
        if method in ("GET", "HEAD") and target.startswith("/api/audio"):
            if PLACEHOLDER_OPUS:
                hdr = (b"HTTP/1.1 200 OK\r\n"
                       b"Content-Type: audio/ogg\r\n"
                       b"Content-Length: " + str(len(PLACEHOLDER_OPUS)).encode() + b"\r\n"
                       b"Connection: close\r\n\r\n")
                writer.write(hdr if method == "HEAD" else hdr + PLACEHOLDER_OPUS)
                log(f"[audio] {peer} GET {target} -> 200 {len(PLACEHOLDER_OPUS)} B audio/ogg "
                    f"(device should decode + play)")
            else:
                writer.write(b"HTTP/1.1 503 Service Unavailable\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                log(f"[audio] {peer} GET {target} -> 503 (no opus; need ffmpeg + --reply)")
        elif method in ("GET", "HEAD") and ASSET_FILE_BYTES and target.startswith(ASSET_FILE_ROUTE):
            # no-SD anim push: the device's dwload_file thread GETs each raw .bin here and
            # writes the body straight to <name>_<code>.bin (no unzip), checking it vs `size`.
            name = target[len(ASSET_FILE_ROUTE):].split("?")[0]
            data = ASSET_FILE_BYTES.get(name)
            if data is None:
                writer.write(b"HTTP/1.1 404 Not Found\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                log(f"[anims] {peer} GET {target} -> 404 (no such .bin; have {list(ASSET_FILE_BYTES)})")
            else:
                hdr = (b"HTTP/1.1 200 OK\r\n"
                       b"Content-Type: application/octet-stream\r\n"
                       b"Content-Length: " + str(len(data)).encode() + b"\r\n"
                       b"Connection: close\r\n\r\n")
                writer.write(hdr if method == "HEAD" else hdr + data)
                globals()["ASSET_SERVED"] = True   # device pulled a .bin -> stop re-pushing
                log(f"[anims] {peer} GET {target} -> 200 {len(data)} B raw .bin "
                    f"(device fetched the frame -- download stage reached)")
        elif method in ("GET", "HEAD") and ASSET_ZIP and target.startswith(ASSET_ZIP_ROUTE):
            # legacy zip route (kept for manual inspection; the device wants the raw .bin instead)
            try:
                data = open(ASSET_ZIP, "rb").read()
            except Exception as e:
                writer.write(b"HTTP/1.1 500 Internal Server Error\r\n"
                             b"Content-Length: 0\r\nConnection: close\r\n\r\n")
                log(f"[anims] {peer} GET {target} -> 500 (read {ASSET_ZIP}: {e})")
            else:
                hdr = (b"HTTP/1.1 200 OK\r\n"
                       b"Content-Type: application/zip\r\n"
                       b"Content-Length: " + str(len(data)).encode() + b"\r\n"
                       b"Connection: close\r\n\r\n")
                writer.write(hdr if method == "HEAD" else hdr + data)
                log(f"[anims] {peer} GET {target} -> 200 {len(data)} B application/zip (legacy zip route)")
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\n"
                         b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            log(f"[audio] {peer} {method} {target} -> 404")
        await writer.drain()
    except Exception as e:
        log(f"[audio] handler error from {peer}: {e}")
    finally:
        try: writer.close()
        except Exception: pass

async def main(port, ip_override=None, logfile=None):
    global LOGFH
    if logfile:
        try:
            LOGFH = open(logfile, "a", buffering=1)
            log("=" * 64)
            log(f"orb_server.py PYTHON SERVER LOG (this is the server, NOT the device UART)")
            log(f"file: {logfile}")
            log("=" * 64)
        except Exception as e:
            print(f"[!] could not open logfile {logfile}: {e}")
    ensure_cert()
    load_vad()
    global PORT, PUBLIC_IP, PLACEHOLDER_OPUS, AUDIO_PORT
    PORT = port
    if not AUDIO_PORT:
        AUDIO_PORT = port + 1
    if ENDPOINT_ACTION == "reply":
        PLACEHOLDER_OPUS = make_placeholder_opus()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["h2"])               # device negotiates h2 via ALPN
    loop = asyncio.get_running_loop()
    server = await loop.create_server(Orb, "0.0.0.0", port, ssl=ctx)
    # Plain-HTTP/1.1 audio side-channel (the device's http_stream fetches the opus here).
    audio_server = await asyncio.start_server(_audio_http_client, "0.0.0.0", AUDIO_PORT)
    ip, cands = host_lan_ips()
    if ip_override:
        ip = ip_override
    PUBLIC_IP = ip
    log(f"listening on 0.0.0.0:{port} (h2/TLS)")
    log(f"set the orb's ENDPOINT_STR to:  https://{ip}:{port}")
    if ENDPOINT_ACTION == "reply":
        log(f"[audio] plain-HTTP audio on 0.0.0.0:{AUDIO_PORT}  -> http://{ip}:{AUDIO_PORT}/api/audio")
        log(f"[reply] REPLY MODE: on endpoint -> Text-Finish (b'finish') on recognize stream "
            f"+ push ExpectSpeech -> http://{ip}:{AUDIO_PORT}/api/audio  (device_id={DEVICE_ID}, "
            f"opus={'ready' if PLACEHOLDER_OPUS else 'MISSING'})")
        if DEVICE_ID == "AIMWLXXXXXXXXXXX":
            log("[reply] note: DEVICE_ID is the placeholder -- fine, the server auto-learns "
                "the real id from the device's upload headers and targets directives at it.")
    if ASSET_ZIP:
        log(f"[anims] ARMED: will push FileManager/UpdateDefaultAssets (media_type:'video') "
            f"on every downchannel open -> raw .bin at http://{ip}:{AUDIO_PORT}{ASSET_FILE_ROUTE}<name>")
        log(f"[anims]   zip={ASSET_ZIP}  character={ASSET_CHARACTER}  version={ASSET_VERSION}  "
            f"files={[(n, len(b)) for n, b in ASSET_FILE_BYTES.items()]}")
    others = ", ".join(f"{n}={a}" for n, a in cands if a != ip)
    if others:
        log(f"(other local IPs seen: {others}  -- wrong one? use --ip <addr>)")
    async with server, audio_server:
        await asyncio.gather(server.serve_forever(), audio_server.serve_forever())

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--audio-port", type=int, default=0,
                    help="plain-HTTP port for the device's audio fetch (default: --port+1). "
                         "Served over http:// because the device verifies TLS on this client.")
    ap.add_argument("--ip", help="override the LAN IP printed for ENDPOINT_STR "
                                 "(use if auto-detect picks a VPN/wrong adapter)")
    ap.add_argument("--device-id", help="real DEVICE_ID for directive targets "
                                        "(else ORB_DEVICE_ID env, else repo placeholder)")
    ap.add_argument("--reply", action="store_true",
                    help="run the stop-signal experiment: on endpoint, close the upstream "
                         "stream and push ExpectSpeech -> /api/audio (default: observe-only)")
    ap.add_argument("--push-anims", metavar="ZIP",
                    help="no-SD anim push: answer the device's FileManager/GetDefaultAssets "
                         "with a media_type:'video' directive pointing at ZIP (a zip of .bin "
                         "frames). Optional sidecar '<ZIP>.manifest.json' sets per-file "
                         "duration/is_bootup/order. Device downloads + fan-syncs it itself.")
    ap.add_argument("--anim-character", default="Ember", help="character for --push-anims")
    ap.add_argument("--anim-media-function", default="sd",
                    choices=["system", "riddle", "music", "story", "sd"],
                    help="media_function for --push-anims (default sd; 'system' triggers the gate-7 "
                         "fw_code reboot for manifest-pushed assets)")
    ap.add_argument("--anim-version", default="2.0",
                    help="version for --push-anims (must exceed NVS EMBER_VER=1.0)")
    ap.add_argument("--logfile", default="auto",
                    help="tee server output to a file so it's never confused with the UART. "
                         "'auto' (default) = orb_server_<timestamp>.log; a path overrides; "
                         "'none' disables.")
    a = ap.parse_args()
    if a.device_id:
        DEVICE_ID = a.device_id
    if a.audio_port:
        AUDIO_PORT = a.audio_port
    if a.reply:
        ENDPOINT_ACTION = "reply"
    if a.push_anims:
        ASSET_ZIP = a.push_anims
        ASSET_CHARACTER = a.anim_character
        ASSET_VERSION = a.anim_version
        ASSET_MEDIA_FUNCTION = a.anim_media_function
        try:
            ASSET_ANIMS = build_animations_manifest(ASSET_ZIP)
        except Exception as e:
            print(f"[!] --push-anims: could not read {ASSET_ZIP}: {e}"); sys.exit(2)
        if not ASSET_ANIMS:
            print(f"[!] --push-anims: no .bin entries in {ASSET_ZIP}"); sys.exit(2)
        # the device does NOT unzip -> extract each .bin's raw bytes to serve per-file
        try:
            import zipfile
            with zipfile.ZipFile(ASSET_ZIP) as z:
                for n in z.namelist():
                    if n.lower().endswith(".bin"):
                        ASSET_FILE_BYTES[os.path.basename(n)] = z.read(n)
        except Exception as e:
            print(f"[!] --push-anims: could not extract .bin bytes from {ASSET_ZIP}: {e}"); sys.exit(2)
        # The device names the on-fan file <name>_<character>_<verhex>.bin and the fan's
        # REQUEST_UPLOAD (0x31) is rejected device-side when that name is >= 31 chars
        # (FUN_420296e8 returns 2 -> fan_sync kret -2 -> reboot loop). Shorten overlong names so
        # the dest fits, remapping the manifest name + served byte key together.
        try:
            _mj, _mn = (ASSET_VERSION.split(".") + ["0"])[:2]
            _verhex = format(int(_mj) * 100 + int(_mn), "02x")
        except Exception:
            _verhex = "00"
        _budget = 30 - (1 + len(ASSET_CHARACTER) + 1 + len(_verhex) + 4)  # dest must be < 31 chars
        if _budget < 5:
            _budget = 5
        for _i, _a in enumerate(ASSET_ANIMS):
            _nm = _a["name"]
            if len(_nm) <= _budget:
                continue
            _ext = ".bin" if _nm.lower().endswith(".bin") else ""
            _idx = f"{_i:02d}" if len(ASSET_ANIMS) > 1 else ""
            _short = _nm[:len(_nm) - len(_ext)][:max(1, _budget - len(_ext) - len(_idx))] + _idx + _ext
            ASSET_FILE_BYTES[_short] = ASSET_FILE_BYTES.pop(_nm)
            _a["name"] = _short
            _dest = f"{_short}_{ASSET_CHARACTER}_{_verhex}.bin"
            print(f"[anims] name '{_nm}' -> '{_short}' (on-fan dest '{_dest}' = {len(_dest)} chars, "
                  f"under the device's 31-char REQUEST_UPLOAD limit)")
    logfile = a.logfile
    if logfile == "auto":
        logfile = time.strftime("orb_server_%Y%m%d_%H%M%S.log")
    elif logfile == "none":
        logfile = None
    try:
        asyncio.run(main(a.port, a.ip, logfile))
    except KeyboardInterrupt:
        print("\nbye")
