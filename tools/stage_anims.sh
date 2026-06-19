#!/usr/bin/env bash
# stage_anims.sh — build a push payload for `orb_server.py --push-anims`.
#
# Zips one or more POV .bin frame files into a single zip and writes a matching
# "<zip>.manifest.json" sidecar (per-file duration / is_bootup / order) so the directive
# carries explicit values instead of the server's defaults.
#
# Usage:
#   tools/stage_anims.sh [-o OUT.zip] [-d DUR_MS] [-b] [-n NAME] FILE.bin [FILE2.bin ...]
#
#     -o OUT.zip   output zip path           (default: eb_anims.zip)
#     -d DUR_MS    per-file duration in ms    (default: 4000; idle is 4000, idle_02 is 6000)
#     -b           mark the FIRST file is_bootup:true (boot animation)
#     -n NAME      override the in-zip filename — only valid with a SINGLE input.
#                  Use the display name (e.g. eb_idle_02_eb1_64.bin) to make it VISIBLE;
#                  use any other name to push a file the device stores but won't display
#                  (pure mechanism test).
#
# Then push it:
#   python3 server/orb_server.py --push-anims OUT.zip --anim-character Ember --anim-version 2.0
#
# Uses only python3 (stdlib zipfile/json) — no `zip` binary needed.
set -euo pipefail

OUT="eb_anims.zip"; DUR=4000; BOOTUP=0; NAME=""
while getopts ":o:d:bn:" opt; do
  case "$opt" in
    o) OUT="$OPTARG" ;;
    d) DUR="$OPTARG" ;;
    b) BOOTUP=1 ;;
    n) NAME="$OPTARG" ;;
    \?) echo "unknown option -$OPTARG" >&2; exit 2 ;;
    :)  echo "option -$OPTARG needs an argument" >&2; exit 2 ;;
  esac
done
shift $((OPTIND - 1))

if [ "$#" -lt 1 ]; then
  sed -n '2,30p' "$0"; exit 2
fi
if [ -n "$NAME" ] && [ "$#" -ne 1 ]; then
  echo "-n NAME is only valid with exactly one input file" >&2; exit 2
fi
for f in "$@"; do
  [ -f "$f" ] || { echo "no such file: $f" >&2; exit 2; }
done

OUT="$OUT" DUR="$DUR" BOOTUP="$BOOTUP" NAME="$NAME" python3 - "$@" <<'PY'
import os, sys, json, zipfile
out   = os.environ["OUT"]
dur   = int(os.environ["DUR"])
boot  = os.environ["BOOTUP"] == "1"
name  = os.environ["NAME"]
files = sys.argv[1:]

manifest = {}
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for i, path in enumerate(files):
        arc = name if name else os.path.basename(path)
        z.write(path, arcname=arc)
        manifest[arc] = {
            "origin_name": arc,
            "duration": dur,
            "is_bootup": bool(boot and i == 0),
            "order": i + 1,
        }
side = out + ".manifest.json"
json.dump(manifest, open(side, "w"), indent=2)

total = sum(os.path.getsize(p) for p in files)
print(f"staged {out}  ({len(files)} file(s), {total} B uncompressed)")
for arc, m in manifest.items():
    print(f"  {arc:<28} order={m['order']} dur={m['duration']}ms is_bootup={m['is_bootup']}")
print(f"sidecar {side}")
print()
print("push it with:")
print(f"  python3 server/orb_server.py --push-anims {out} --anim-character Ember --anim-version 2.0")
PY
