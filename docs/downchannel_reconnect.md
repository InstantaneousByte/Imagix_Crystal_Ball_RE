# Idle downchannel reconnect / base-LED flicker

**Status: characterized, low priority, left alone by decision (2026-06-19).** Both mitigation
options below are on the table; neither is implemented. This is cosmetic — the reconnects are
clean (flat RAM, full function, and our anim push rides on them).

## Symptom
On the de-cloud server the orb opens a fresh TCP downchannel to `:9000` every ~6–12 s (new source
port each time: `53544 → 53545 → 53546 …`), and the base LED flickers once per cycle. It does **not**
happen during sleep. Server log shows `=== ORB disconnected ===` / `=== ORB connected ===` pairs;
UART shows `downchannel_mon_task [1615] Need to check now` → `downchannel_clean_up` →
`nghttp_new_session`.

## Mechanism (fully traced)
The downchannel monitor `FUN_42022208` runs two checks per wakeup:

- **Check 1 (registration timeout, 10 s):** fires only while the session flags say "registration
  pending" (`FUN_42000498(sess,0) & 4` and `& 2`) and conn flag `+0x60 & 0x40` is set. Resends
  `send_register_device` up to 3×, else logs `Register_Not_Respond`. Separate from the flicker.
- **Check 2 (idle refresh, 6 s) — this is the reconnect:** if the downchannel is established
  (`FUN_42000498(sess,0) & 8`), the idle timer (`*DAT_42002174 + DAT_42002180(6000ms) <= now`) has
  elapsed, and the device is **not interacting** (`conn+0x5d == 0`), it recycles the channel —
  **unless** the "fresh" bit `conn+0x60 & 0x08` is set. It then clears that bit. So the bit buys
  exactly one cycle.

`FUN_42000498` (real addr `0x421c858c`) is an atomic read/clear-bits on the session flags
(`*p = *p & ~mask`, returns old) — `mask 0` = pure read.

### Why a server keepalive can't stop it
The only writer of the "fresh" bit (`conn+0x60 |= 8`) is `FUN_42021100`, which the firmware's
handler table at `0x42001f80` names **`send_initial_state`** — it runs **once per (re)connect** and
is then cleared by the monitor. The idle timer `*DAT_42002174` is reset **only** by the device's own
`send_register_device` (`FUN_420221b8`). Received frames / PINGs / pushed data touch **neither** the
fresh bit nor the timer. So no server-sent frame keeps the channel "fresh." The only state that
suppresses Check 2 on an established channel is `conn+0x5d != 0` (**interacting**) — which we can't
hold, because an interacting device rejects pushed background directives ("User interacting").

### Why stock (probably) didn't flicker
Not a keepalive frame — the real cloud kept the device **busy**: a live registered session with
ongoing services/directives, so it rarely sat fully idle for 6 s and rarely hit Check 2. Our minimal
server registers nothing and goes silent between the device's own events, so it drops straight into
the idle-refresh path.

## Registration handshake (found while tracing; relevant to option 1)
The device registers each session by sending **`Activation/GetDeviceInfo`** (outbound event code 2,
built by the same builder as all directives). The success path is `RegisterNotifySuccess`
(`FUN_42005ec4` @ `0x42005ec4`): it sets the confirmed flags (`+0x62 = 1`, `+100 = 1`), writes
`user_id`/`token`/`endpoint`/`REGISTER_STR=1`, and contains the `set_endpoint` call our endpoint-pin
patch NOPs (`0x42005f06`). Note Check 2 does **not** read these flags, so registration may quiet the
churn without fully killing it.

## Options on the table (both LOW priority, not implemented)
1. **Complete the registration handshake.** Respond to `Activation/GetDeviceInfo` with a `DeviceInfo`
   response that triggers `RegisterNotifySuccess`, so the device enters a fully-registered/active
   session. Correct behavior to implement regardless; measure whether it settles the reconnect.
   Risk: Check 2 is idle-gated and doesn't check the registration flags, so it may only reduce, not
   eliminate, the churn.
2. **LED-on-reconnect firmware patch.** Leave the reconnect alone and NOP/condition whatever drives
   the base LED off the downchannel connection state, so the cosmetic flicker stops. More invasive
   (touches firmware), but the only *sure* kill if option 1 doesn't fully settle it.

## Decision
Leave it for now. Revisit only if the flicker becomes annoying or if the registration response is
built for other reasons (then test option 1's effect on the reconnect for free).
