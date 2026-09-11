# Xiaowu Hermes Wallpaper + Rosé Theme SOP

**Status:** Known-good production baseline
**Date established:** 2026-09-11
**Scope:** Xiaowu / 银月2号 Hermes Dashboard visual layer only

## 1. Purpose

This SOP records the working Xiaowu Hermes Dashboard visual configuration:

- `xiaowu-visual` owns the character wallpaper overlay.
- `xiaowu.yaml` owns the Xiaowu Rosé-derived theme.
- The wallpaper is rendered as an overlay above the Hermes/xterm black visual layer.
- Wallpaper replacement must not require Hermes Core changes.

The important historical lesson from the original Yinyue implementation is that the character layer must sit above the Hermes/xterm black layer. The working overlay uses `z-index: 50`.

## 2. Known-good files

```text
~/.hermes/plugins/xiaowu-visual/
├── plugin.yaml
└── dashboard/
    ├── manifest.json
    ├── dist/
    │   ├── index.js
    │   └── style.css
    └── assets/
        └── xiaowu.png

~/.hermes/dashboard-themes/xiaowu.yaml
~/.hermes/config.yaml
```

Current wallpaper source:

```text
$HOME/xiaowu.png
```

Known image baseline:

```text
PNG
1328 x 1776
RGB
SHA256:
925fc091a90fc121bbfb8ed5719de09be0be405e0f82892d00b822948dda65f5
```

## 3. Critical wallpaper contract

### Plugin identity

The Dashboard manifest must identify the plugin as:

```json
"name": "xiaowu-visual"
```

The plugin must be enabled in `~/.hermes/config.yaml`.

### Direct wallpaper URL

The working implementation directly loads:

```text
/dashboard-plugins/xiaowu-visual/assets/xiaowu.png
```

Do not make wallpaper visibility depend on a theme-generated asset variable unless that change is separately tested.

### Plugin registration

The working JavaScript registration must use:

```javascript
window.__HERMES_PLUGINS__.register(
  "xiaowu-visual",
  function () { return null; }
);

window.__HERMES_PLUGINS__.registerSlot(
  "xiaowu-visual",
  "backdrop",
  YinyueBackdrop
);
```

`YinyueBackdrop` is a historical compatibility function name. It does not need cosmetic renaming.

### Historical CSS compatibility names

The working overlay keeps these classes:

```css
.yinyue-backdrop-layer
.yinyue-character
```

Do not rename them merely to clean up the namespace.

The critical layer rule is:

```css
.yinyue-backdrop-layer {
  position: fixed;
  inset: 0;
  overflow: hidden;
  pointer-events: none;
  user-select: none;
  z-index: 50;
}
```

`z-index: 50` is the key rule that places the image above the Hermes/xterm black visual layer.

## 4. Xiaowu Rosé theme baseline

The Xiaowu theme derives its main color system from Hermes' built-in `roseTheme`.

Rosé core palette:

```text
background:   #1a0f15
midground:    #ffd4e1
foreground:   #ffffff / alpha 0
warmGlow:     rgba(249, 168, 212, 0.3)
noiseOpacity: 0.9
```

The Xiaowu theme:

- keeps the working wallpaper overlay untouched;
- keeps transparent chat behavior where already configured;
- keeps the terminal foreground readable;
- removes old explicit blue semantic `colorOverrides`;
- uses Rosé-derived component styling for cards, header, sidebar and tabs.

For color-only work, modify only:

```text
~/.hermes/dashboard-themes/xiaowu.yaml
```

Do not modify the wallpaper plugin.

## 5. Create a known-good backup

Run this immediately after confirming the wallpaper and Rosé theme both look correct:

```bash
cd $HOME
set -euo pipefail

TS="$(date +%Y%m%d-%H%M%S)"
B="$HOME/.hermes/backups/xiaowu-wallpaper-rose-KNOWN-GOOD-$TS"

mkdir -p "$B"

cp -a \
  "$HOME/.hermes/plugins/xiaowu-visual" \
  "$B/"

cp -a \
  "$HOME/.hermes/dashboard-themes/xiaowu.yaml" \
  "$B/xiaowu.yaml"

cp -a \
  "$HOME/.hermes/config.yaml" \
  "$B/config.yaml"

sha256sum \
  "$HOME/.hermes/plugins/xiaowu-visual/dashboard/dist/index.js" \
  "$HOME/.hermes/plugins/xiaowu-visual/dashboard/dist/style.css" \
  "$HOME/.hermes/plugins/xiaowu-visual/dashboard/manifest.json" \
  "$HOME/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png" \
  "$HOME/.hermes/dashboard-themes/xiaowu.yaml" \
  "$HOME/.hermes/config.yaml" \
  > "$B/SHA256SUMS.txt"

printf '%s\n' \
  "Xiaowu wallpaper + Rosé known-good baseline" \
  "Created: $(date -Iseconds)" \
  "Hermes: 0.20.6" \
  "Wallpaper SHA256: 925fc091a90fc121bbfb8ed5719de09be0be405e0f82892d00b822948dda65f5" \
  > "$B/README.txt"

echo "KNOWN_GOOD=$B"
cat "$B/SHA256SUMS.txt"
```

## 6. Replace only the wallpaper image

```bash
cp -a \
  ~/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png \
  ~/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png.pre-change-$(date +%Y%m%d-%H%M%S)

cp -f \
  $HOME/xiaowu.png \
  ~/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png

chmod 644 \
  ~/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png

sha256sum \
  ~/.hermes/plugins/xiaowu-visual/dashboard/assets/xiaowu.png
```

Do not change plugin registration, CSS class names, z-index, or theme semantics just to replace the image.

## 7. Exact visual restore

Use a verified known-good visual backup only:

```bash
cd $HOME
set -euo pipefail

B="$HOME/.hermes/backups/<KNOWN-GOOD-BACKUP>"
PLUGIN="$HOME/.hermes/plugins/xiaowu-visual"
THEME="$HOME/.hermes/dashboard-themes/xiaowu.yaml"

hermes dashboard --stop || true

rm -rf "$PLUGIN"
cp -a "$B/xiaowu-visual" "$PLUGIN"

cp -a "$B/xiaowu.yaml" "$THEME"

chmod 644 "$PLUGIN/dashboard/assets/xiaowu.png"

cd $HOME/.hermes/hermes-agent
hermes dashboard --host 0.0.0.0 --port 9119
```

For a visual-only rollback, do not restore unrelated Avatar, Memory, MCP, identity-state, model, or workflow files.

## 8. Verification

### Plugin discovery

```bash
curl -sS http://127.0.0.1:9119/api/dashboard/plugins \
  | python3 -m json.tool
```

Expected plugin:

```text
name: xiaowu-visual
slots: backdrop
entry: dist/index.js
css: dist/style.css
```

### Asset endpoints

```bash
curl -sS -o /dev/null \
  -w 'JS=%{http_code}\n' \
  http://127.0.0.1:9119/dashboard-plugins/xiaowu-visual/dist/index.js

curl -sS -o /dev/null \
  -w 'CSS=%{http_code}\n' \
  http://127.0.0.1:9119/dashboard-plugins/xiaowu-visual/dist/style.css

curl -sS -o /dev/null \
  -w 'IMAGE=%{http_code}\n' \
  http://127.0.0.1:9119/dashboard-plugins/xiaowu-visual/assets/xiaowu.png
```

Expected:

```text
JS=200
CSS=200
IMAGE=200
```

### Browser runtime

Developer Console:

```javascript
document.querySelector(".yinyue-backdrop-layer")
```

Expected: non-null.

Then:

```javascript
document.querySelector(".yinyue-character")?.src
```

Expected URL contains:

```text
/dashboard-plugins/xiaowu-visual/assets/xiaowu.png
```

### Layering

The wallpaper must remain visible above the Hermes/xterm black layer.

Confirm:

```css
z-index: 50;
pointer-events: none;
```

## 9. LAN dashboard launch for testing

```bash
cd $HOME
hermes dashboard --stop || true

cd $HOME/.hermes/hermes-agent
hermes dashboard --host 0.0.0.0 --port 9119
```

Open from another LAN machine:

```text
http://<HERMES_HOST>:9119/chat
```

Return to loopback-only binding when LAN access is no longer required.

## 10. Troubleshooting order

If wallpaper disappears, check in this order:

1. `xiaowu-visual` appears in `/api/dashboard/plugins`.
2. `dist/index.js` returns HTTP 200.
3. `dist/style.css` returns HTTP 200.
4. `assets/xiaowu.png` returns HTTP 200.
5. JavaScript registers `xiaowu-visual`.
6. `registerSlot("xiaowu-visual", "backdrop", ...)` is present.
7. Browser DOM contains `.yinyue-backdrop-layer`.
8. Browser DOM contains `.yinyue-character`.
9. CSS still has `z-index: 50`.

Do not start by modifying Hermes Core or xterm.

If wallpaper works but colors are wrong, modify only:

```text
~/.hermes/dashboard-themes/xiaowu.yaml
```

## 11. Change discipline

For every future visual change:

```text
Known-good backup
        ↓
One isolated change
        ↓
Restart Dashboard
        ↓
Hard refresh
        ↓
Verify plugin API/assets
        ↓
Verify wallpaper
        ↓
Verify theme colors
        ↓
Create new known-good backup
```

Do not combine namespace migration, wallpaper replacement, theme recoloring, and Hermes Core changes in a single unverified step.

## 12. Current known-good architecture

```text
Hermes Dashboard
    │
    ├── Xiaowu theme
    │     └── Rosé-derived palette
    │
    └── xiaowu-visual
          │
          ├── plugin identity: xiaowu-visual
          ├── backdrop slot
          ├── direct image URL
          │     └── /dashboard-plugins/xiaowu-visual/assets/xiaowu.png
          │
          └── historical working overlay CSS
                ├── .yinyue-backdrop-layer
                ├── .yinyue-character
                └── z-index: 50
```

This is the Xiaowu visual baseline established on 2026-09-11.

---

## 13. Frozen known-good production snapshot

The first formally frozen known-good Xiaowu wallpaper + Rosé production snapshot is:

```text
$HOME/.hermes/backups/xiaowu-wallpaper-rose-KNOWN-GOOD-20260911-204827
```

This snapshot was created after the wallpaper overlay and Rosé theme were both visually confirmed working.

Recorded SHA-256 values:

```text
306326eb349e9d78fc5a8d4e352f65677ff8ca389509c832c57446182e888dd6  plugin.yaml
7d44a57964a5ce35d042dc347bbf5741e64406866efb651cc651fe23e9328fe1  dashboard/manifest.json
2afe05a4a0eca7d08e1718356305455dace014b1892940040aeea423d5bcf150  dashboard/dist/index.js
1d2a852d435186f26f815054acb2c29f6224730c513475973edf0ee1dbecd50b  dashboard/dist/style.css
925fc091a90fc121bbfb8ed5719de09be0be405e0f82892d00b822948dda65f5  dashboard/assets/xiaowu.png
9d839fea269606f3b618165338a3d8ae00349c79eec23c99fe424f4cbc17939e  dashboard-themes/xiaowu.yaml
1e4b8099eb6dd77986f5efdbdf6aad2614a701718cdbaf315a1122ba708e9062  config.yaml
925fc091a90fc121bbfb8ed5719de09be0be405e0f82892d00b822948dda65f5  $HOME/xiaowu.png
```

The backup contains:

```text
BASELINE.txt
SHA256SUMS.txt
config.yaml
xiaowu-source.png
xiaowu.yaml
xiaowu-visual/
```

For a future visual rollback, prefer this snapshot over older migration/theme backups unless there is a specific reason to recover an earlier historical state.

Do not restore unrelated older namespace-migration backups for a wallpaper/theme-only problem.
