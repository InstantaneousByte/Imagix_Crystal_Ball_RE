#!/usr/bin/env python3
"""
reprovision.py - one-shot WiFi + NVS (+ app) reprovisioning for the de-clouded orb.

Orchestrates the existing tools so changing network/password/endpoint is a single
command that emits ready-to-flash artifacts plus the exact combined esptool line.

WHAT TO TOUCH (the tool figures this out from your flags):
  * new password / SSID, same box IP   -> WiFi only            (flash 0xA20000)
  * moved to a new network / subnet     -> WiFi + ENDPOINT_STR  (flash 0xA20000 + 0x9000)
  * from a factory dump (no app yet)    -> add --register --character Ember --app app_final.bin

EXAMPLES
  # just a new WiFi password (de-clouded orb already pointed at your box):
  python3 tools/reprovision.py --ssid MyNet --password newpass -o out

  # network move (new subnet => new box IP):
  python3 tools/reprovision.py --nvs-in nvs_current.bin \
      --ssid MyNet --password pass --endpoint https://10.0.0.5:9000 -o out

  # straight from a factory NVS dump, no app ever used:
  python3 tools/reprovision.py --nvs-in nvs_factory.bin --app app_final.bin \
      --ssid MyNet --password pass --endpoint https://10.0.0.5:9000 \
      --register --character Ember -o out

Artifacts land in the output dir; the tool prints ONE esptool command to flash them all.
NOTE: wifi_spiffs.bin holds the WiFi password in plaintext - don't publish it.
"""
import argparse, os, sys, subprocess, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{r.stdout}\n{r.stderr}")
    return r.stdout

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out-dir", default="reprovision_out")
    # wifi
    ap.add_argument("--ssid"); ap.add_argument("--password")
    ap.add_argument("--also", action="append", default=[], metavar="SSID:PASS")
    # nvs
    ap.add_argument("--nvs-in", help="base NVS image to patch (required for any NVS change)")
    ap.add_argument("--endpoint", help="set ENDPOINT_STR (e.g. https://10.0.0.5:9000)")
    ap.add_argument("--register", action="store_true", help="set REGISTER_STR=1 (from-factory)")
    ap.add_argument("--character", help="set CHARACTER_STR (e.g. Ember)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="set any other NVS string key; repeatable")
    # app (not modified, just included in the flash line)
    ap.add_argument("--app", help="path to app_final.bin to include in the flash command")
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    flash = []   # (offset, path)

    # --- WiFi -> spiffs ---
    if a.ssid:
        if not a.password:
            sys.exit("--ssid needs --password")
        wifi = os.path.join(a.out_dir, "wifi_spiffs.bin")
        cmd = [PY, os.path.join(HERE, "make_wifi_spiffs.py"),
               "--ssid", a.ssid, "--password", a.password, "-o", wifi]
        for x in a.also: cmd += ["--also", x]
        run(cmd)
        flash.append((0xA20000, wifi))
        print(f"WiFi -> {wifi}  (auto-connect: {a.ssid})")
    elif a.password or a.also:
        sys.exit("--password/--also given without --ssid")

    # --- NVS edits (chained through nvs_set.py) ---
    edits = []
    if a.register:  edits.append(("REGISTER_STR", "1"))
    if a.character: edits.append(("CHARACTER_STR", a.character))
    if a.endpoint:  edits.append(("ENDPOINT_STR", a.endpoint))
    for s in a.set:
        if "=" not in s: sys.exit(f"--set expects KEY=VALUE, got '{s}'")
        k, v = s.split("=", 1); edits.append((k, v))
    if edits:
        if not a.nvs_in:
            sys.exit("NVS changes requested but no --nvs-in base image given")
        cur = a.nvs_in
        nvs_out = os.path.join(a.out_dir, "nvs_out.bin")
        for i, (k, v) in enumerate(edits):
            dst = nvs_out if i == len(edits) - 1 else os.path.join(a.out_dir, f".nvs_step{i}.bin")
            run([PY, os.path.join(HERE, "nvs_set.py"), cur, dst, k, v])
            cur = dst
        for i in range(len(edits) - 1):
            p = os.path.join(a.out_dir, f".nvs_step{i}.bin")
            if os.path.exists(p): os.remove(p)
        flash.append((0x9000, nvs_out))
        print(f"NVS  -> {nvs_out}  ({', '.join(f'{k}={v}' for k,v in edits)})")

    # --- app (included in flash line only) ---
    if a.app:
        flash.append((0x20000, a.app))
        print(f"app  -> {a.app} (unchanged, included in flash)")

    if not flash:
        sys.exit("nothing to do - pass --ssid and/or NVS flags (and optionally --app)")

    flash.sort()
    print("\nflash everything in one shot:")
    line = "  esptool.py --chip esp32s3 --baud 460800 write_flash \\\n"
    line += " \\\n".join(f"    0x{off:x} {path}" for off, path in flash)
    print(line)

if __name__ == "__main__":
    main()
