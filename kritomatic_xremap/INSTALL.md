

# Installing kritomatic-xremap

Prerequisites, one-time setup, and how to verify the install works.

## Prerequisites

You need:

- **Linux** with either X11 or Wayland. xremap supports both.
- **xremap** on `PATH`. On Arch: `pacman -S xremap` (or
  `xremap-gnome-git` / `xremap-kde-git` for the DE-specific variants).
- **python3** with **PyYAML**. On Arch: `pacman -S python-yaml`.
- **kanata**, or any tool that emits a virtual input device (keyd,
  interception-tools, kmonad). This project does not require kanata
  specifically — anything that shows up in `xremap --list-devices`
  works.
- Your user in the **`input`** group, or xremap configured to use
  `uinput` with the necessary permissions. Confirm with
  `xremap --list-devices` — if it lists devices without `sudo`,
  you're set.

## Install

### 1. Place the repository

Anywhere under your home directory. Paths below assume a checkout at
`~/kritomatic-xremap/`; adjust to taste.

```bash
git clone <repo-url> ~/kritomatic-xremap
cd ~/kritomatic-xremap
```

### 2. Make the helpers executable

```bash
chmod +x kritomatic-xremap.py setup_links.py
```

### 3. Seed your bindings file

```bash
./kritomatic-xremap.py init
```

This copies `bindings.example.yaml` to
`~/.config/kritomatic/bindings.yaml`. It will not overwrite an
existing file unless you pass `--force`.

### 4. Edit your bindings file

Open `~/.config/kritomatic/bindings.yaml` and change two things:

- **`devices.listen`** — every device xremap should react to. These
  are the exact names printed by `xremap --list-devices`. For a
  kanata-driven setup they look like `"kanata macropad"`,
  `"kanata standard"`, etc. — whatever you set as
  `linux-output-device-name` in the `.kbd` file.
- **`devices.default`** — which device blocks inherit when they do not
  name one themselves. Usually the same as the first entry in `listen`.

The `keymap:` section starts empty. Add blocks to bind capabilities.
Example:

```yaml
devices:
  listen:
    - kanata macropad
    - kanata standard
  default: kanata macropad

keymap:
  - name: My Krita shortcuts
    devices: [kanata macropad]
    application:
      only: krita
    remap:
      n: clipboard2krita
      m: clipboard2caption
```

Capability names come from `capabilities.yaml`. Open that file to see
what's available.

To bind your own script, add a `user_capabilities:` block first:

```yaml
user_capabilities:
  my_script:
    command: ["/home/me/bin/my_script.sh"]

keymap:
  - name: Custom
    remap:
      X: my_script
```

### 5. Validate

```bash
./kritomatic-xremap.py validate
```

You should see `✓ bindings valid: N block(s), M binding(s)`. If not,
the error names the specific problem — a capability that does not
exist, a script file that is not on disk, or a device that is not in
`devices.listen`.

### 6. Install the systemd units

```bash
./setup_links.py
```

Safe to re-run any time. It:

- re-validates your bindings,
- installs (or reinstalls) `xremap-kritomatic.service` and
  `xremap-kritomatic.path`,
- enables both for the current user,
- restarts the service, and
- polls for up to 15 seconds confirming the service came up.

### 7. Verify

```bash
systemctl --user is-active xremap-kritomatic.service
# expect: active

pgrep -af xremap
# expect: xremap /tmp/tmpXXXX.yaml --device <each device in listen>
```

If the service is active but xremap reports `Selected devices matching
[]`, edit `devices.listen` — device names must match exactly what
`xremap --list-devices` reports.

## Day-to-day workflow

### Adding a binding

1. Open `~/.config/kritomatic/bindings.yaml`.
2. Add the key under the appropriate `remap:` block (or start a new
   block).
3. Save. The path unit (`.path`) watches this file and restarts the
   service automatically.

### Adding a capability (maintainers)

1. Drop the script into `scripts/`.
2. Add one entry to `capabilities.yaml`.
3. Users get it on their next `bindings.yaml` edit or service restart.

### Adding a device

Add its name to `devices.listen` in `bindings.yaml`.

## Troubleshooting

**"Service not active" after setup** —
`journalctl --user -u xremap-kritomatic -n 50 --no-pager`. The
launcher prints its own errors there.

**Bindings don't fire** — `validate` first; then `render` to see the
actual YAML; then `pgrep -af xremap` to confirm the device is being
listened to. If the device is there but the key does not fire, the
device's own emulator (your kanata config) is not emitting the
keycode the binding expects. That is outside this project's scope.

**"Device not found" at startup** — one of the names in
`devices.listen` is not visible to xremap. Check
`xremap --list-devices`; if the device is genuinely not there, the
kanata (or other) instance that emits it is not running.

**"unknown capability"** — a `keymap:` entry names a capability that
is not in `capabilities.yaml` and is not in your `user_capabilities:`.

## Uninstalling

```bash
systemctl --user disable --now \
    xremap-kritomatic.service xremap-kritomatic.path
rm ~/.config/systemd/user/xremap-kritomatic.service
rm ~/.config/systemd/user/xremap-kritomatic.path
rm ~/.config/systemd/user/xremap-kritomatic-restart.service
rm ~/.config/kritomatic/bindings.yaml
systemctl --user daemon-reload
```

Your repository checkout can be deleted separately.
