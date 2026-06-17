# WiFi provisioning without the app (+ full from-factory de-cloud)

The orb does **not** store WiFi in NVS. It keeps a single plaintext file, `/wifi.txt`,
in the `spiffs` partition (flash `0xA20000`, size `0x500000`) — and that's the *only*
file in the partition. The app reads it at boot and pushes the creds to esp_wifi at
runtime (which is why `wifi:` log lines show "Stored Wi-Fi" but `nvs.net80211` is empty).
This is also why the NVS de-cloud never disturbed WiFi: **different partition entirely.**

## /wifi.txt format (verified against real dumps)

```
01 00 00 00 7c            # u32 version=1, then '|' (0x7c) delimiter
SSID=<name>\n
PASSWORD=<pass>\n
FAVOURITE=<true|false>\n
\n                        # blank line; repeat block per remembered network
```
The `FAVOURITE=true` network is the one the device auto-connects to.

## spiffs geometry (derived from the device image)
page 256, block 4096, obj-name-len 32, meta-len 4 (esp-idf defaults, magic on).
Confirmed by regenerating with esp-idf `spiffsgen.py` and matching the device's index
header byte-for-byte (`type=0x01` file, name `/wifi.txt`).

## Set WiFi with no app, no cloud

`tools/make_wifi_spiffs.py` (wraps the vendored esp-idf `tools/spiffsgen.py`, Apache-2.0):

```
python3 tools/make_wifi_spiffs.py --ssid "MyNet" --password "secret" -o wifi_spiffs.bin
esptool.py --chip esp32s3 write_flash 0xA20000 wifi_spiffs.bin
```
Flashing only `0xA20000` leaves the app and NVS untouched. Add `--also "SSID:PASS"` to
keep extra remembered networks (the `--ssid` one is the favourite/auto-connect).

## Full factory -> de-cloud, zero app, zero cloud

A factory reset clears three NVS strings + deletes `/wifi.txt`, and leaves the app intact
(`ota_0` byte-identical). To go straight from a factory device to de-clouded, with no app:

1. **App** (de-cloud patches): `esptool ... write_flash 0x20000 app_final.bin`
2. **WiFi** (this tool): `make_wifi_spiffs.py ...` -> `write_flash 0xA20000 wifi_spiffs.bin`
3. **NVS** — three string flips on the factory NVS (identity/JWT/RF-cal all survive the reset):
   ```
   python3 tools/nvs_set.py nvs_factory.bin n1.bin REGISTER_STR  1
   python3 tools/nvs_set.py n1.bin          n2.bin CHARACTER_STR Ember
   python3 tools/nvs_set.py n2.bin          nvs_out.bin ENDPOINT_STR https://<box-ip>:9000
   esptool ... write_flash 0x9000 nvs_out.bin
   ```
   `REGISTER_STR=1` is the gate: at `0`, the boot logs `Setup = 1`, starts BLE onboarding,
   and never dials `/connect` (verified on hardware). At `1` it re-arms the downchannel.
4. Run the local server, boot. No app, no cloud registration ever required.

## One-shot: `tools/reprovision.py` (WiFi + NVS + app together)

Orchestrates `make_wifi_spiffs.py` + `nvs_set.py` and prints a single combined flash
command, touching only what changed:

```
# new WiFi password only (NVS untouched):
python3 tools/reprovision.py --ssid MyNet --password newpw -o out

# moved to a new subnet (WiFi + new box IP):
python3 tools/reprovision.py --nvs-in nvs_current.bin \
    --ssid MyNet --password pw --endpoint https://10.0.0.5:9000 -o out

# straight from a factory NVS dump, no app ever used:
python3 tools/reprovision.py --nvs-in nvs_factory.bin --app app_final.bin \
    --ssid MyNet --password pw --endpoint https://10.0.0.5:9000 \
    --character Ember -o out      # REGISTER_STR=1 is automatic
```
It emits `wifi_spiffs.bin` (always rebuilt from scratch, so old creds are zeroed) and,
if any NVS flag is given, `nvs_out.bin` — then one `esptool write_flash` line with the
right offsets (`0x9000` nvs, `0x20000` app, `0xA20000` wifi). WiFi-only changes don't
touch NVS at all.

## PRIVACY WARNING

Full-flash dumps (`orb_full.bin`, any `fw_*.bin`) and generated `wifi_spiffs.bin` contain
WiFi **SSIDs and passwords in plaintext** (in spiffs). The repo scrub never covered these
because they don't live in the repo. **Never publish a flash dump or a wifi image.** If a
dump has already been shared, treat those WiFi passwords as compromised and rotate them.
