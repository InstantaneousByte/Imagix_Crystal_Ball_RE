# syncing_tracking.info Format

The main board firmware reads `/sdcard/syncing_tracking.info` on a sync trigger (the persona reader `olli_read_persona_from_tracking_`/`FUN_42031db4`, gated by a flag at `ctx+0x80`), executes the described sync, and **deletes the file** — so it does not persist at rest. Dropping the file manually is intended to be equivalent to a cloud-initiated sync. **Verified:** every field name below is a real string and `cJSON` is present (see AUDIT.md). **Not yet verified:** the exact field-value routing to local-SD vs cloud download — confirm against `FUN_420317d4`/`FUN_42032308` or a real capture.

---

## Trigger condition

`FUN_42032308` (`olli_persona_start_sync_new_persona`) checks `FUN_420317d4` which returns:
- `2` (`RET_SDCARD_EXISTING`) → use local files from `root_path`, no cloud download
- `1` → needs cloud download
- `0` → other

Setting `is_factory_update: true` in the JSON sets `ctx[0x45]` which influences this check. The exact routing depends on both `is_factory_update` and whether the described files actually exist at `root_path`.

---

## JSON format

```json
{
  "name":              "my_character",
  "persona":           "buddyos_my_character",
  "version":           "1.0",
  "root_path":         "/sdcard/my_character/my_character_01",
  "character":         "MyCharacter",
  "update_type":       "is_factory_update",
  "total_files":       1,
  "build_date":        "2024-01-01",
  "is_new_version":    true,
  "is_new_character":  false,
  "is_factory_update": true,
  "origin_name":       "",
  "compatible_versions": "",
  "animation_array": [
    {
      "name":               "my_idle_64.bin",
      "origin_name":        "my_idle_64.bin",
      "size":               6854400,
      "order":              0,
      "duration":           4,
      "has_sd":             true,
      "has_fan":            false,
      "is_bootup":          false,
      "compatible_versions": ""
    }
  ]
}
```

---

## Field notes

**`name`** — character/persona identifier, used to build the SD path.

**`root_path`** — where the firmware looks for files on `/sdcard`. Built from the format string `/sdcard/%s/%s_%02x` (character, name, version_hex). Files in `animation_array` are expected at `root_path/[name]`.

**`origin_name`** — the **destination filename on the fan** (what the file is called after upload). `name` is the SD-side source filename. They can differ, allowing a rename on upload. Leave `origin_name` the same as `name` to use the same filename on both sides.

**`is_factory_update`** — set `true` to use local SD files rather than triggering a cloud download.

**`is_new_character`** — set `true` when registering a completely new character. The firmware will look for `character.info` at `root_path/character.info` and register the new character in its persona list.

**`has_sd`** — set `true` if the file exists on the main board SD at `root_path`. `has_fan` — set `true` if the file is already on the fan (skip upload). For a fresh push: `has_sd: true, has_fan: false`.

---

## character.info (new character registration)

When `is_new_character: true`, place a `character.info` JSON at `root_path/character.info`:

```json
{
  "persona":    "buddyos_my_character",
  "character":  "MyCharacter",
  "version":    "1.0",
  "media_type": "animation",
  "animation_array": [
    {
      "name":               "my_idle_64.bin",
      "origin_name":        "my_idle_64.bin",
      "size":               6854400,
      "order":              0,
      "duration":           4,
      "has_sd":             false,
      "has_fan":            false,
      "is_bootup":          false,
      "compatible_versions": ""
    }
  ]
}
```

Known character codes: `"Ember"` (Ember the dragon), `"Ellie"` (Ellie the fairy). New characters add their own code.

---

## File layout on main board SD

```
/sdcard/
  syncing_tracking.info        ← drop this to trigger sync
  my_character/
    my_character_01/           ← root_path (name_versionhex)
      character.info           ← if is_new_character
      my_idle_64.bin           ← animation file(s)
      my_listening_64.bin
      ...
```

---

## Validation status

The `syncing_tracking.info` mechanism is documented from Ghidra analysis of `FUN_42032308`, `FUN_4202ee18`, `FUN_4202dc00`, and `FUN_4202dfb0`. The exact JSON field routing to `RET_SDCARD_EXISTING` vs cloud-download path has not been validated against a captured real sync. A GL-AXT1800 MITM capture of a legitimate cloud sync would confirm the exact field values used in practice.
