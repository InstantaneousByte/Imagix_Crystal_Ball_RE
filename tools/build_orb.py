#!/usr/bin/env python3
"""
build_orb.py - one command: stock firmware dump + your settings -> a complete,
flash-ready de-clouded build (patched app + NVS + WiFi), with a single esptool line
and a preflight check so you never ship a half-configured orb.

PIPELINE (each stage runs only if you give it the inputs):
  APP   fw_main.bin --[authmode bypass]--[endpoint pin]--[optional audio swaps]--> app_final.bin   (0x20000)
  NVS   <base nvs> --[REGISTER_STR=1 + ENDPOINT_STR + CHARACTER_STR + --set]-----> nvs_out.bin      (0x9000)
  WIFI  --ssid/--password -----------------------------------------------------> wifi_spiffs.bin   (0xA20000)

REGISTER_STR=1 is set AUTOMATICALLY whenever an NVS is built -- it's the connect gate;
without it the orb stays on the Setup screen and never dials your server. Use --no-register
only if you actually want the orb in Setup/BLE onboarding mode.

EXAMPLES
  # full from-factory de-cloud, custom boot sound, new network, all in one:
  python3 tools/build_orb.py \
      --fw-in fw_main.bin --nvs-in nvs_factory.bin \
      --endpoint https://10.0.0.26:9000 --character Ember \
      --ssid MyNet --password pw \
      --audio bootup=creepy.ogg \
      -o build

  # rebuild just the patched app (no nvs/wifi):
  python3 tools/build_orb.py --fw-in fw_main.bin -o build

Then flash the printed esptool line. (NOTE: outputs contain WiFi creds / endpoint in the
clear - they're gitignored; never publish build artifacts or dumps.)
"""
import argparse, os, sys, subprocess, shutil, re

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
AUTHMODE_OK = bytes.fromhex("0c0b")     # mbedtls authmode NONE
PIN_OK      = bytes.fromhex("f02000")   # set_endpoint NOP
A_AUTH, A_PIN = 0x24fa6a, 0x235f06      # file offsets within the app image

def tool(name): return os.path.join(HERE, name)
def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(str(c) for c in cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out-dir", default="orb_build")
    # APP
    ap.add_argument("--fw-in", help="stock app dump (fw_main.bin) -> patched app_final.bin")
    ap.add_argument("--no-authmode", action="store_true", help="skip the TLS authmode bypass")
    ap.add_argument("--no-pin", action="store_true", help="skip the endpoint pin patch")
    ap.add_argument("--audio", action="append", default=[], metavar="NAME=FILE",
                    help="swap an embedded system sound (e.g. bootup=creepy.ogg); repeatable")
    # NVS
    ap.add_argument("--nvs-in", help="base NVS image to patch")
    ap.add_argument("--endpoint", help="ENDPOINT_STR, e.g. https://10.0.0.26:9000")
    ap.add_argument("--character", help="CHARACTER_STR, e.g. Ember / Ellie")
    ap.add_argument("--register", action="store_true",
                    help="(on by default when building NVS) set REGISTER_STR=1")
    ap.add_argument("--no-register", action="store_true",
                    help="do NOT set REGISTER_STR=1 (leave the orb in Setup/BLE mode)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="set any extra NVS STR key; repeatable")
    # WIFI
    ap.add_argument("--ssid"); ap.add_argument("--password")
    ap.add_argument("--also", action="append", default=[], metavar="SSID:PASS")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    flash, summary = [], []

    # ---------- APP ----------
    if a.fw_in:
        cur, steps = a.fw_in, []
        def step(out_name, cmd, label):
            nonlocal cur
            dst = os.path.join(a.out_dir, out_name)
            run(cmd(cur, dst)); cur = dst; steps.append(label)
        if not a.no_authmode:
            step(".app_authmode.bin",
                 lambda s, d: [PY, tool("patch_authmode_none.py"), s, d], "authmode=NONE")
        if not a.no_pin:
            step(".app_pin.bin",
                 lambda s, d: [PY, tool("patch_pin_endpoint.py"), s, d], "endpoint pinned")
        if a.audio:
            sets = []
            for sp in a.audio: sets += ["--set", sp]
            step(".app_audio.bin",
                 lambda s, d: [PY, tool("patch_system_audio.py"), s, d] + sets,
                 "audio: " + ", ".join(x.split("=")[0] for x in a.audio))
        app_final = os.path.join(a.out_dir, "app_final.bin")
        shutil.move(cur, app_final)
        for p in (".app_authmode.bin", ".app_pin.bin", ".app_audio.bin"):
            fp = os.path.join(a.out_dir, p)
            if os.path.exists(fp): os.remove(fp)
        flash.append((0x20000, app_final))
        summary.append(f"app_final.bin   <- {os.path.basename(a.fw_in)} + [{'; '.join(steps) or 'no patches'}]")
    elif a.audio or a.no_authmode or a.no_pin:
        sys.exit("app flags given but no --fw-in")

    # ---------- NVS ----------
    if (a.endpoint or a.character or a.set) and not a.nvs_in:
        sys.exit("NVS changes (--endpoint/--character/--set) requested but no --nvs-in given")
    edits = []
    if a.nvs_in:
        if not a.no_register:
            edits.append(("REGISTER_STR", "1"))     # connect gate - de-cloud needs this
        if a.character: edits.append(("CHARACTER_STR", a.character))
        if a.endpoint:  edits.append(("ENDPOINT_STR", a.endpoint))
        for s in a.set:
            if "=" not in s: sys.exit(f"--set expects KEY=VALUE, got '{s}'")
            k, v = s.split("=", 1); edits.append((k, v))
    if edits:
        cur = a.nvs_in
        nvs_out = os.path.join(a.out_dir, "nvs_out.bin")
        for i, (k, v) in enumerate(edits):
            dst = nvs_out if i == len(edits)-1 else os.path.join(a.out_dir, f".nvs{i}.bin")
            run([PY, tool("nvs_set.py"), cur, dst, k, v]); cur = dst
        for i in range(len(edits)-1):
            fp = os.path.join(a.out_dir, f".nvs{i}.bin")
            if os.path.exists(fp): os.remove(fp)
        flash.append((0x9000, nvs_out))
        summary.append("nvs_out.bin     <- " + ", ".join(f"{k}={v}" for k, v in edits))

    # ---------- WIFI ----------
    if a.ssid:
        if not a.password: sys.exit("--ssid needs --password")
        wifi = os.path.join(a.out_dir, "wifi_spiffs.bin")
        cmd = [PY, tool("make_wifi_spiffs.py"), "--ssid", a.ssid, "--password", a.password, "-o", wifi]
        for x in a.also: cmd += ["--also", x]
        run(cmd)
        flash.append((0xA20000, wifi))
        summary.append(f"wifi_spiffs.bin <- auto-connect {a.ssid}" +
                       (f" (+{len(a.also)} more)" if a.also else ""))

    if not flash:
        sys.exit("nothing to build - give --fw-in and/or --nvs-in and/or --ssid")

    print("built:")
    for s in summary: print("  " + s)

    # ---------- PREFLIGHT (re-read what we just built; catch silent misconfig) ----------
    ok = True
    print("\npreflight:")
    for off, path in flash:
        if off == 0x20000:
            b = open(path, "rb").read()
            if not a.no_authmode:
                g = b[A_AUTH:A_AUTH+2] == AUTHMODE_OK; ok &= g
                print(f"  app authmode=NONE   : {'OK' if g else 'MISSING ('+b[A_AUTH:A_AUTH+2].hex()+')'}")
            if not a.no_pin:
                g = b[A_PIN:A_PIN+3] == PIN_OK; ok &= g
                print(f"  app endpoint pin    : {'OK' if g else 'MISSING ('+b[A_PIN:A_PIN+3].hex()+')'}")
        if off == 0x9000:
            out = run([PY, tool("nvs_parse.py"), path])
            def val(k):
                m = re.search(rf"{k}\s*=\s*'([^']*)'", out); return m.group(1) if m else None
            reg, ep, ch = val("REGISTER_STR"), val("ENDPOINT_STR"), val("CHARACTER_STR")
            if not a.no_register:
                g = reg == "1"; ok &= g
                print(f"  REGISTER_STR=1      : {'OK' if g else 'NOT SET (='+str(reg)+')  -> orb stays in SETUP mode!'}")
            print(f"  ENDPOINT_STR        : {ep or '(none - orb wont know where to connect)'}")
            if ch: print(f"  CHARACTER_STR       : {ch}")
    # de-cloud sanity: app patched but no endpoint anywhere
    if any(o == 0x20000 for o, _ in flash) and not a.endpoint and not any(o == 0x9000 for o, _ in flash):
        print("  NOTE: patched app but no NVS/ENDPOINT built - orb will use its existing NVS endpoint.")
    print("  " + ("all good - safe to flash." if ok else
                   "!! PREFLIGHT FAILED - flashing this will NOT fully de-cloud (see above)."))

    flash.sort()
    print("\nflash it all in one shot:")
    print("  esptool.py --chip esp32s3 --baud 460800 write_flash \\")
    print(" \\\n".join(f"    0x{o:x} {p}" for o, p in flash))

if __name__ == "__main__":
    main()
