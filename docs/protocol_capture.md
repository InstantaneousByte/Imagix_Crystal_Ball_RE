# Capturing the Cloud Protocol (mitmproxy) — no firmware patch needed

Goal: record the full ORB ↔ cloud conversation in plaintext so the local server can be built
to match it. **Do this while Imagix's servers are still up** — once they're dark there's
nothing to forward to.

**Key fact:** the ORB does **not** enforce TLS cert verification. `open_ssl_connection`
(`FUN_4201f8f0`) logs a warning on a bad cert and connects anyway. So mitmproxy's default
cert is accepted — **no CA injection, no firmware flashing for the capture.**

## Gear
- The ORB on the GL-AXT1800's WiFi.
- A Linux box on the same LAN as the capture host (e.g. `192.168.8.245`). `pipx install mitmproxy`.

## Step 1 — confirm the EXACT hostname the ORB uses
Don't assume `chat-buddyos.iviet.com` vs `chat-buddyos-us.iviet.com`. Boot the ORB and watch
the GL-iNet DNS queries (LuCI → Network → DNS, or `logread`/dnsmasq query log on the router).
Whatever host the ORB looks up at boot is the one to redirect. Call it `$HOST`.

## Step 2 — get the real upstream IP (so the proxy has somewhere to forward)
Query a resolver that is NOT overridden:
```
dig $HOST @1.1.1.1
```
It will CNAME to an AWS ELB (observed: `…elb.ap-southeast-1.amazonaws.com` →
`18.139.188.150` / `18.142.15.210`). Note an A record as `$REAL_IP`.

## Step 3 — redirect the ORB to the capture box (GL-iNet DNS)
Add a hostname override: `$HOST` → `<capture-box-LAN-IP>` (you've done this for
`192.168.8.245`; just make sure it's on the host from Step 1). dnsmasq form:
```
address=/$HOST/192.168.8.245
```

## Step 4 — run mitmproxy as a recording reverse proxy
```
mitmdump --mode reverse:https://$REAL_IP:443 \
         --set keep_host_header=true \
         --set connection_strategy=lazy \
         -w orb.flows
```
Notes:
- `keep_host_header` makes the upstream request carry the real `$HOST` (so the ELB/Istio
  ingress routes correctly and presents the right cert).
- ALPN `h2` is negotiated automatically; mitmproxy speaks HTTP/2.
- If the upstream TLS SNI needs to be `$HOST` (Istio is SNI-routed), add
  `--set upstream_cert=true` (default) and, if needed, force SNI via the host header path.
  If the handshake to upstream fails, that's the knob to tweak.

## Step 5 — boot the ORB and capture
The ORB connects to mitmproxy believing it's the cloud. On its serial log you'll see the
harmless `Failed to verify certificate` warning — expected, it proceeds. Let it fully come up
("Ember here…") and interact a bit so the capture includes session/persona/TTS traffic.

## Step 6 — read the spec you just captured
```
mitmweb -r orb.flows
```
Look for, in order:
1. The **registration POST** to `$HOST/connect` (device → server): JSON with `device_type`,
   `device_id`, versions, under a `device_registration` wrapper.
2. The **registration response** (server → device): the fields that become NVS
   `TOKEN_STR`/`USER_ID`/`PROFILE_STR`/`ENDPOINT_STR` and flip `REGISTER_STR=1`
   (this is what `RegisterNotifySuccess`/`FUN_42005ec4` consumes).
3. The **StartSession** + persona directives that swap logo → Ember and trigger the greeting.
4. Keepalive `{"payload":{"msg":"Ping"}}` and any heartbeat cadence.

Those four are the minimum the local server must reproduce. Everything else (wakeword, button
events, TTS streaming) is layered on after the device boots to Ember.

## Troubleshooting
- **Nothing hits mitmproxy:** wrong `$HOST` (Step 1) or the override isn't applied to the ORB's
  DNS (confirm the ORB uses the GL-iNet for DNS, not a hardcoded resolver).
- **Upstream handshake fails:** SNI/Host mismatch to the Istio ingress — try toggling
  `keep_host_header` / explicit SNI.
- **h2 errors:** ensure a recent mitmproxy (HTTP/2 reverse mode); older builds were flaky.
