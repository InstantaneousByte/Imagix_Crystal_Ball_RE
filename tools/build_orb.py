#!/usr/bin/env python3
"""
build_orb.py - one command: stock firmware dump + your settings -> a complete,
flash-ready de-clouded build (patched app + NVS + WiFi), with a single esptool line.

PIPELINE (each stage runs only if you give it the inputs):
  APP   fw_main.bin --[authmode bypass]--[endpoint pin]--[optional audio swaps]--> app_final.bin   (0x20000)
  NVS   <base nvs> --[ENDPOINT_STR / REGISTER_STR / CHARACTER_STR / --set]------> nvs_out.bin      (0x9000)
  WIFI  --ssid/--password -----------------------------------------------------> wifi_spiffs.bin   (0xA20000)

EXAMPLES
  # full from-factory de-cloud, custom boot sound, new network, all in one:
  python3 tools/build_orb.py \
      --fw-in fw_main.bin --nvs-in nvs_factory.bin \
      --endpoint https://10.0.0.5:9000 --register --character Ember \
      --ssid MyNet --password pw \
      --audio bootup=creepy.ogg --audio poweron=horn.ogg \
      -o build

  # rebuild just the patched app (no nvs/wifi):
  python3 tools/build_orb.py --fw-in fw_main.bin -o build

Then flash the printed esptool line. (NOTE: outputs contain WiFi creds / endpoint in the
clear - they're gitignored; never publish build artifacts or dumps.)
"""
import argparse, os, sys, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

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
    ap.add_argument("--endpoint"); ap.add_argument("--register", action="store_true")
    ap.add_argument("--character")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    # WIFI
    ap.add_argument("--ssid"); ap.add_argument("--password")
    ap.add_argument("--also", action="append", default=[], metavar="SSID:PASS")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    flash = []
    summary = []

    # ---------- APP ----------
    if a.fw_in:
        cur = a.fw_in
        steps = []
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
    edits = []
    if a.register:  edits.append(("REGISTER_STR", "1"))
    if a.character: edits.append(("CHARACTER_STR", a.character))
    if a.endpoint:  edits.append(("ENDPOINT_STR", a.endpoint))
    for s in a.set:
        if "=" not in s: sys.exit(f"--set expects KEY=VALUE, got '{s}'")
        k, v = s.split("=", 1); edits.append((k, v))
    if edits:
        if not a.nvs_in: sys.exit("NVS edits requested but no --nvs-in")
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
        sys.exit("nothing to build - give --fw-in and/or NVS flags and/or --ssid")

    # sanity nudge
    if any(o == 0x20000 for o, _ in flash) and not a.endpoint and not any(o == 0x9000 for o, _ in flash):
        print("NOTE: built a patched app but set no ENDPOINT_STR - the orb will use whatever\n"
              "      endpoint is already in its NVS. Pass --nvs-in ... --endpoint ... if needed.\n")

    flash.sort()
    print("built:")
    for s in summary: print("  " + s)
    print("\nflash it all in one shot:")
    print("  esptool.py --chip esp32s3 --baud 460800 write_flash \\")
    print(" \\\n".join(f"    0x{o:x} {p}" for o, p in flash))

if __name__ == "__main__":
    main()
