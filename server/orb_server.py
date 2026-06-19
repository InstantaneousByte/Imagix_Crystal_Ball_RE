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
import asyncio, ssl, json, time, os, subprocess, sys, argparse, wave

from h2.connection import H2Connection
from h2.config import H2Configuration
from h2.events import RequestReceived, DataReceived, StreamEnded, StreamReset, WindowUpdated

DEVICE_ID = "AIMWLXXXXXXXXXXX"     # your ORB's device id (DEVICE_ID_STR in NVS)
CERT, KEY = "cert.pem", "key.pem"

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

def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)

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

class Orb(asyncio.Protocol):
    def __init__(self):
        cfg = H2Configuration(client_side=False, header_encoding="utf-8")
        self.conn = H2Connection(config=cfg)
        self.bodies = {}            # stream_id -> bytes accumulated
        self.paths = {}             # stream_id -> :path
        self.headers = {}           # stream_id -> request headers dict

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
                log(f"stream {ev.stream_id}: {hdrs.get(':method','?')} {hdrs.get(':path','?')}")
            elif isinstance(ev, DataReceived):
                self.bodies[ev.stream_id] = self.bodies.get(ev.stream_id, b"") + ev.data
                self.conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
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

    def handle(self, sid):
        is_audio = self._persist(sid)
        path = self.paths.get(sid, "")
        if path.endswith("/connect"):
            # The downchannel. Send the session-start that opens the gate, and
            # keep the stream OPEN (don't end_stream) -- directives ride this later.
            #
            # WIRE FRAMING (verified in fw: FUN_42020050 -> FUN_4201ff70, strstr on
            # "$START_JSON"/"$END_JSON"): every downchannel message MUST be wrapped as
            #     $START_JSON<json>$END_JSON
            # concatenated with no separators/length fields. The device buffers the
            # stream and only hands a frame to data_json_handle when it finds BOTH
            # markers. Bare JSON is silently ignored (no "Got new session").
            msg = frame_json({"connected": int(time.time()*1000),
                              "session_id": DEVICE_ID})
            log(f"stream {sid}: -> session-start {msg!r} (keeping downchannel open)")
            self.conn.send_headers(sid, [(":status", "200"),
                                         ("content-type", "application/json")])
            self.conn.send_data(sid, msg, end_stream=False)
            # /connect is intentionally NOT _forget()'d -- we keep it open.
            # TODO (item 7): push directives with frame_json(...) on this same stream
            # (keepalive, then SpeechRecognizer/ExpectSpeech for the TTS reply).
            return
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

async def main(port, ip_override=None):
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["h2"])               # device negotiates h2 via ALPN
    loop = asyncio.get_running_loop()
    server = await loop.create_server(Orb, "0.0.0.0", port, ssl=ctx)
    ip, cands = host_lan_ips()
    if ip_override:
        ip = ip_override
    log(f"listening on 0.0.0.0:{port} (h2/TLS)")
    log(f"set the orb's ENDPOINT_STR to:  https://{ip}:{port}")
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
    a = ap.parse_args()
    try:
        asyncio.run(main(a.port, a.ip))
    except KeyboardInterrupt:
        print("\nbye")
