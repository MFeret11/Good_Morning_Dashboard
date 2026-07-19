# Home Assistant Scripts — Deployment Process

## Setup context
- HA runs as a Docker container on Raspberry Pi 4 (Raspberry Pi OS, not HAOS/Supervised)
- Config lives in a **named Docker volume**: `septa-project_ha_config`
- Real path on disk: `/var/lib/docker/volumes/septa-project_ha_config/_data`
- This path is **not directly accessible** to the regular Pi user — `/var/lib/docker/` is root-only (`drwx--x---`), so no chmod/permission fix on the volume itself is advisable (it would loosen permissions for every container on the Pi, not just HA).
- No Add-on Store available (that's HAOS/Supervised only) — so no File Editor or Studio Code Server add-on option here.

## Editing workflow (copy-out / edit / copy-back)

1. **Copy the file out of the Docker volume into your home directory:**
   ```bash
   sudo cp /var/lib/docker/volumes/septa-project_ha_config/_data/scripts.yaml ~/scripts.yaml
   sudo chown mferet11:mferet11 ~/scripts.yaml
   ```

2. **Edit `~/scripts.yaml`** — via VS Code Remote-SSH (File → Open Folder → `/home/mferet11`), or any editor. This file is a normal user-owned file at this point, fully editable without sudo.

3. **Push the edited file back into the Docker volume:**
   ```bash
   sudo cp ~/scripts.yaml /var/lib/docker/volumes/septa-project_ha_config/_data/scripts.yaml
   ```

4. **Reload scripts in HA** (no container restart needed):
   - Home Assistant UI → **Developer Tools → YAML → Scripts** → click reload icon
   - Or via a service call: `script.reload`

Same pattern applies to `automations.yaml`, `configuration.yaml`, `scenes.yaml`, etc. — just swap the filename.

## VS Code Remote-SSH connection
- Extension: **Remote - SSH** (Microsoft)
- Connect via: `ssh mferet11@192.168.1.203`
- Once connected, open folders under `/home/mferet11` normally — no permission issues there, since it's outside Docker's root-owned tree.

## Current scripts (as of this doc)

| Script entity | Purpose |
|---|---|
| `script.theater_lights_toggle` | Toggles bias lights (switch) + recessed lights (3% dim) on/off together, based on current bias light state |
| `script.all_theater_on` | Bias lights on + recessed lights 3% + Onn 4K Pro on (cascades to AVR/projector via CEC) |
| `script.all_theater_off` | Onn 4K Pro off (cascades via CEC) + bias lights off + recessed lights off |
| `script.post_movie_lights` | Onn off → 3s delay (let CEC settle) → lights on at 3% → 2 min delay → lights off (path-to-bed light) |

## Known issues / open items
- CEC power-off doesn't cascade to AVR/projector 100% reliably (source: Onn 4K Pro → AVR/projector chain). IR blaster was tried as a backup but also unreliable — sticking with CEC as the primary approach for now.
- `remote.theater_onn_pro` entity exists alongside `media_player.onn_pro_2` (same physical device, different integration) — not currently used in scripts; revisit if `media_player` control proves insufficient on its own.

## Dashboard
- Dedicated "Theater" dashboard, set as default view
- Card type: `vertical-stack` of `button` cards, one per script, in the section
- Also present as: Android home screen widgets + Quick Settings tiles (via HA Companion app) for `all_theater_on` / `all_theater_off`