#!/usr/bin/env python3
"""
make_wifi_spiffs.py - build a spiffs image that sets the orb's WiFi WITHOUT the app.

The orb stores WiFi as a single plaintext file, /wifi.txt, in the `spiffs` partition
(flash 0xA20000, size 0x500000). It is the ONLY file in that partition, so we can
regenerate the whole image cleanly with esp-idf's spiffsgen.py (vendored alongside).

/wifi.txt format (reverse-engineered, verified byte-for-byte against live firmware-written
pages in real dumps):
    The file is ONE OR MORE records, back to back, and nothing else:
        SSID=<name>\n
        PASSWORD=<pass>\n
        FAVOURITE=<true|false>\n
        \n                        # blank line terminates each record
    The FAVOURITE=true network is the one the device auto-connects to.

    NOTE: earlier revisions prepended a bogus 5-byte "version header" (01 00 00 00 7c).
    That was a misread: in a raw hex view those bytes are the SPIFFS *page header*
    (obj_id=0x0001, span=0, flags) of a deleted page copy -- flags 0x7c just happens to
    print as '|'. The real file content begins directly at "SSID=". Writing the prefix
    into the body makes the firmware's line parser miss the SSID= key, yielding an empty
    WiFi config and an instant esp_wifi_connect failure on every network.

spiffs geometry (derived from the device image): page 256, block 4096,
obj-name-len 32, meta-len 4  (esp-idf defaults; magic on).

USAGE:
    python3 make_wifi_spiffs.py --ssid "MyNet" --password "secret" -o wifi_spiffs.bin
    # keep extra remembered networks (first one is the favourite/auto-connect):
    python3 make_wifi_spiffs.py --ssid "MyNet" --password "secret" \
            --also "OtherNet:otherpass" -o wifi_spiffs.bin

Then flash JUST the spiffs partition (leaves app + nvs untouched):
    esptool.py --chip esp32s3 write_flash 0xA20000 wifi_spiffs.bin

NOTE: full-flash dumps contain these creds in PLAINTEXT. Never publish a dump or a
generated wifi_spiffs.bin; treat any leaked WiFi password as compromised.
"""
import argparse, os, sys, subprocess, tempfile

PART_SIZE = 0x500000
HERE = os.path.dirname(os.path.abspath(__file__))
SPIFFSGEN = os.path.join(HERE, "spiffsgen.py")

def record(ssid, pw, fav):
    return f"SSID={ssid}\nPASSWORD={pw}\nFAVOURITE={'true' if fav else 'false'}\n\n"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ssid", required=True, help="WiFi SSID to auto-connect to")
    ap.add_argument("--password", required=True, help="WiFi password")
    ap.add_argument("--also", action="append", default=[],
                    metavar="SSID:PASS", help="extra remembered network (not favourite); repeatable")
    ap.add_argument("-o", "--out", default="wifi_spiffs.bin")
    ap.add_argument("--size", default=hex(PART_SIZE), help="spiffs partition size (default 0x500000)")
    a = ap.parse_args()

    body = record(a.ssid, a.password, True)
    for extra in a.also:
        if ":" not in extra:
            sys.exit(f"--also expects SSID:PASS, got '{extra}'")
        s, p = extra.split(":", 1)
        body += record(s, p, False)

    size = int(a.size, 0)
    with tempfile.TemporaryDirectory() as d:
        # spiffsgen stores the file under its basename as /wifi.txt
        with open(os.path.join(d, "wifi.txt"), "w", newline="") as f:
            f.write(body)
        cmd = [sys.executable, SPIFFSGEN, str(size), d, a.out,
               "--page-size", "256", "--block-size", "4096",
               "--obj-name-len", "32", "--meta-len", "4"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"spiffsgen failed:\n{r.stdout}\n{r.stderr}")

    print(f"wrote {a.out} ({os.path.getsize(a.out)} bytes)")
    print(f"  favourite (auto-connect): {a.ssid}")
    for extra in a.also:
        print(f"  also remembered:          {extra.split(':',1)[0]}")
    print(f"\nflash it (leaves app + nvs untouched):")
    print(f"  esptool.py --chip esp32s3 write_flash 0xA20000 {a.out}")

if __name__ == "__main__":
    main()
