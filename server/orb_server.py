#!/usr/bin/env python3
"""
orb_server.py -- minimal local stand-in for the Imagix cloud (boot-gate MVP).

Goal: get the ORB past the "Artificial Imagery" logo to Ember, with NO cloud.
It speaks HTTP/2 over TLS (self-signed; the cert-trust firmware patch makes the
ORB accept it) and answers the downchannel `/connect` with the one message that
flips the gate:

    {"connected": <unix_ms>, "session_id": "<device_id>"}

Everything else (events the device pushes) is just logged and ACKed. This is a
STARTING POINT -- watch the ORB's serial log and iterate. The conversational
layer (SpeechRecognizer/ExpectSpeech + /api/audio + STT/LLM/TTS) is TODO.

Requires:  pip install h2
Generates: cert.pem / key.pem (self-signed) on first run, via openssl.

Run:       sudo python3 orb_server.py        # :9000 (sudo only if port<1024; 9000 is fine without)
           python3 orb_server.py --port 9000
"""
import asyncio, ssl, json, time, os, subprocess, sys, argparse

from h2.connection import H2Connection
from h2.config import H2Configuration
from h2.events import RequestReceived, DataReceived, StreamEnded, WindowUpdated

DEVICE_ID = "AIMWLXXXXXXXXXXX"     # your ORB's device id (DEVICE_ID_STR in NVS)
CERT, KEY = "cert.pem", "key.pem"

def ensure_cert():
    if os.path.exists(CERT) and os.path.exists(KEY):
        return
    print("[*] generating self-signed cert (cert.pem/key.pem)...")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", KEY, "-out", CERT, "-days", "3650",
        "-subj", "/CN=orb-local", "-addext", "subjectAltName=IP:192.168.8.245",
    ], check=True)

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

async def main(port):
    ensure_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    ctx.set_alpn_protocols(["h2"])               # device negotiates h2 via ALPN
    loop = asyncio.get_running_loop()
    server = await loop.create_server(Orb, "0.0.0.0", port, ssl=ctx)
    log(f"listening on :{port} (h2/TLS). Point ENDPOINT_STR at https://<this-box-ip>:{port}")
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
