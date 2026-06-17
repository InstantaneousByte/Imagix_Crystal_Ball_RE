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
Optional:  pip install psutil   (explicit Ethernet-over-Wi-Fi IP detection on startup;
           without it the printed IP is the default-route address)
Generates: cert.pem / key.pem (self-signed) on first run -- pure-Python via the
           cryptography lib (no openssl needed); falls back to openssl if present.

Run:       sudo python3 orb_server.py        # :9000 (sudo only if port<1024; 9000 is fine without)
           python3 orb_server.py --port 9000
"""
import asyncio, ssl, json, time, os, subprocess, sys, argparse

from h2.connection import H2Connection
from h2.config import H2Configuration
from h2.events import RequestReceived, DataReceived, StreamEnded, WindowUpdated

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

class Orb(asyncio.Protocol):
    def __init__(self):
        cfg = H2Configuration(client_side=False, header_encoding="utf-8")
        self.conn = H2Connection(config=cfg)
        self.bodies = {}            # stream_id -> bytes accumulated
        self.paths = {}             # stream_id -> :path

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
                self.bodies[ev.stream_id] = b""
                log(f"stream {ev.stream_id}: {hdrs.get(':method','?')} {hdrs.get(':path','?')}")
            elif isinstance(ev, DataReceived):
                self.bodies[ev.stream_id] = self.bodies.get(ev.stream_id, b"") + ev.data
                self.conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
            elif isinstance(ev, StreamEnded):
                self.handle(ev.stream_id)
        out = self.conn.data_to_send()
        if out: self.transport.write(out)

    def handle(self, sid):
        path = self.paths.get(sid, "")
        body = self.bodies.get(sid, b"")
        if body:
            # Persist the raw body (these device->server events are the spec for the
            # conversation layer: profile, user info, persona, user-state, STT, etc.)
            try:
                os.makedirs(CAPTURE, exist_ok=True)
                with open(os.path.join(CAPTURE, "events.log"), "ab") as f:
                    f.write(f"# {time.strftime('%H:%M:%S')} stream {sid} {path}\n".encode())
                    f.write(body + b"\n")
            except Exception as e:
                log("capture write failed:", e)
            # Log it readably -- unwrap $START_JSON framing if the device used it, pretty JSON.
            for payload in unwrap_frames(body):
                log(f"stream {sid} body[{path}]:\n{pretty(payload)}")

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
            # TODO: push further directives with frame_json(...) on this same stream
            # if the gate needs more (e.g. SpeechRecognizer/ExpectSpeech for TTS).
        else:
            # An event/upchannel stream -- just ACK it so the device is happy.
            log(f"stream {sid}: -> 200 ACK (event)")
            self.conn.send_headers(sid, [(":status", "200")], end_stream=True)
        out = self.conn.data_to_send()
        if out: self.transport.write(out)

    def connection_lost(self, exc):
        log("=== ORB disconnected ===")

def host_lan_ips():
    """Return (best_ip, [(iface, ip), ...]), preferring Ethernet over Wi-Fi.

    The server binds 0.0.0.0 so it doesn't NEED this -- it's only to print the right
    ENDPOINT_STR. With psutil (optional) we rank by real interface name (Ethernet first,
    Wi-Fi last, virtual/VPN adapters skipped). Without it we fall back to the default-route
    source IP, which the OS already sends over Ethernet when a cable is connected.
    """
    import socket as S
    SKIP = ("virtual", "vethernet", "vmware", "vbox", "virtualbox", "hyper-v", "loopback",
            "docker", "wsl", "tailscale", "zerotier", "tap", "tun", "bluetooth", "ppp")
    try:
        import psutil
        stats = psutil.net_if_stats()
        def rank(name):
            n = name.lower()
            if any(k in n for k in ("wi-fi", "wifi", "wlan", "wireless")) or n.startswith("wl"):
                return 2                                   # Wi-Fi -> lowest priority
            if "ethernet" in n or "lan" in n or n.startswith(("eth", "en", "em")):
                return 0                                   # Ethernet -> highest priority
            return 1                                       # unknown -> middle
        cands = []
        for name, addrs in psutil.net_if_addrs().items():
            st = stats.get(name)
            if not st or not st.isup or any(s in name.lower() for s in SKIP):
                continue
            for a in addrs:
                if a.family == S.AF_INET and not a.address.startswith("127."):
                    cands.append((rank(name), name, a.address))
        if cands:
            cands.sort(key=lambda c: (c[0], c[1]))
            return cands[0][2], [(c[1], c[2]) for c in cands]
    except Exception:
        pass
    s = S.socket(S.AF_INET, S.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip, [("default-route", ip)]

async def main(port):
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["h2"])               # device negotiates h2 via ALPN
    loop = asyncio.get_running_loop()
    server = await loop.create_server(Orb, "0.0.0.0", port, ssl=ctx)
    ip, cands = host_lan_ips()
    log(f"listening on 0.0.0.0:{port} (h2/TLS)")
    log(f"set the orb's ENDPOINT_STR to:  https://{ip}:{port}")
    others = ", ".join(f"{n}={a}" for n, a in cands if a != ip)
    if others:
        log(f"(other local IPs seen: {others})")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9000)
    a = ap.parse_args()
    try:
        asyncio.run(main(a.port))
    except KeyboardInterrupt:
        print("\nbye")
