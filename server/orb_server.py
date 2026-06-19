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
        trimmed utterance (what STT would receive). Acting on it (closing the stream
        or pushing a reply) is gated behind ENDPOINT_ACTION="reply" -- the device advances
        on an app-level Text Finish + ExpectSpeech, not on an h2 close, so that path
        is the next experiment, not a freebie."""
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
            # EXPERIMENT: (1) close the upstream stream [candidate 'done'/Text-Finish],
            # then (2) push ExpectSpeech on the downchannel with our /api/audio URL.
            # The device should GET that URL and play the opus. If it stays in
            # 'listening' instead, the app-level Text Finish is required first.
            try:
                self.conn.send_headers(sid, [(":status", "200")], end_stream=True)
                self.responded.add(sid)
            except Exception as e:
                log(f"stream {sid}: upstream close failed ({e})")
            aid = uuid.uuid4().hex
            url = f"https://{PUBLIC_IP}:{PORT}/api/audio?id={aid}"
            dlg = self.dialog.get(sid)
            target_id = self.dev_id or DEVICE_ID   # learned real id beats the typed one
            ok = self.push_directive(expect_speech_directive(target_id, url, dlg))
            out = self.conn.data_to_send()
            if out: self.transport.write(out)
            log(f"stream {sid}: REPLY -> closed upstream + "
                f"{'pushed' if ok else 'FAILED to push'} ExpectSpeech (id={aid}, "
                f"target={target_id}, dlg={dlg}); watch for [432] user directive / GET /api/audio")

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
    global PORT, PUBLIC_IP, PLACEHOLDER_OPUS
    PORT = port
    if ENDPOINT_ACTION == "reply":
        PLACEHOLDER_OPUS = make_placeholder_opus()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["h2"])               # device negotiates h2 via ALPN
    loop = asyncio.get_running_loop()
    server = await loop.create_server(Orb, "0.0.0.0", port, ssl=ctx)
    ip, cands = host_lan_ips()
    if ip_override:
        ip = ip_override
    PUBLIC_IP = ip
    log(f"listening on 0.0.0.0:{port} (h2/TLS)")
    log(f"set the orb's ENDPOINT_STR to:  https://{ip}:{port}")
    if ENDPOINT_ACTION == "reply":
        log(f"[reply] EXPERIMENT MODE: on endpoint -> close upstream + push ExpectSpeech "
            f"-> https://{ip}:{port}/api/audio  (device_id={DEVICE_ID}, "
            f"opus={'ready' if PLACEHOLDER_OPUS else 'MISSING'})")
        if DEVICE_ID == "AIMWLXXXXXXXXXXX":
            log("[reply] WARNING: DEVICE_ID is the placeholder -- set the real id via "
                "--device-id / ORB_DEVICE_ID or the directive's target may be rejected.")
    others = ", ".join(f"{n}={a}" for n, a in cands if a != ip)
    if others:
        log(f"(other local IPs seen: {others}  -- wrong one? use --ip <addr>)")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--ip", help="override the LAN IP printed for ENDPOINT_STR "
                                 "(use if auto-detect picks a VPN/wrong adapter)")
    ap.add_argument("--device-id", help="real DEVICE_ID for directive targets "
                                        "(else ORB_DEVICE_ID env, else repo placeholder)")
    ap.add_argument("--reply", action="store_true",
                    help="run the stop-signal experiment: on endpoint, close the upstream "
                         "stream and push ExpectSpeech -> /api/audio (default: observe-only)")
    ap.add_argument("--logfile", default="auto",
                    help="tee server output to a file so it's never confused with the UART. "
                         "'auto' (default) = orb_server_<timestamp>.log; a path overrides; "
                         "'none' disables.")
    a = ap.parse_args()
    if a.device_id:
        DEVICE_ID = a.device_id
    if a.reply:
        ENDPOINT_ACTION = "reply"
    logfile = a.logfile
    if logfile == "auto":
        logfile = time.strftime("orb_server_%Y%m%d_%H%M%S.log")
    elif logfile == "none":
        logfile = None
    try:
        asyncio.run(main(a.port, a.ip, logfile))
    except KeyboardInterrupt:
        print("\nbye")
