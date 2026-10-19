KRITOMATIC XREMAP — CHEAT SHEET
===============================

ARCHITECTURE
------------
    ~/.config/xremap/xremap_kritomatic.env   ← settings
                    │
                    ▼
    merger.py  →  /tmp/tmpXXXX.yaml  →  xremap --device A --device B
                    ▲
                    │
    kritomatic_xremap.yaml + ~/xremap_config.yaml   ← bindings

merger.py: reads env + both YAMLs, applies default device: filter,
           writes merged YAML to /tmp, launches xremap.
xremap:    listens on --device keyboards, fires launch: on matching keys.

Two device concepts (the bit that confuses everyone):
    --device X          = xremap LISTENS to keyboard X
    device: {only: X}   = binding only FIRES from keyboard X
    Both required. One without the other = silent failure.

YOUR CURRENT SETUP
------------------
    TARGET_DEVICE    = kanata standard      ← main keyboard
    PERSONAL_DEVICES = (empty)

Effect:
    Bindings in kritomatic_xremap.yaml with device: {only: kanata ministeren}
    are DEAD (nothing listening there). Block 1 is intentionally dead.
    Block 2 (no device:) fires from kanata standard, in Krita only.
    ~/xremap_config.yaml (no device:) fires from kanata standard.

FOUR FILES YOU'LL TOUCH
-----------------------
    ~/.config/xremap/xremap_kritomatic.env   settings
    kritomatic_xremap.yaml (in repo)         kritomatic bindings, __TOKEN__s
    ~/xremap_config.yaml                     your personal bindings
    setup_links.py (in repo)                 installer / refresher

EVERYDAY COMMANDS
-----------------
    xreload                           restart xremap (after YAML/env edits)
    xsetup                            run setup_links.py (after .service/.path
                                      edits, or first install)
    xsetup --reconfigure              interactively pick devices
    xsetup --list-devices             print device names

DEBUGGING — IN THIS ORDER
------------------------
  1. systemctl --user status xremap-kritomatic.service
  2. ps -eo pid,cmd | grep '[x]remap /tmp'
     cat /tmp/<that-file>.yaml         ← this is the truth, not your YAMLs
  3. Does ps list the keyboard your key comes from?
     If not: add it to PERSONAL_DEVICES or set it as TARGET_DEVICE.
  4. Does the binding's device: block match that keyboard?
     No device: block → inherits TARGET_DEVICE.
     Mismatched device: → can't fire; fix or remove the block.
  5. Did kanata eat the combo?
     sudo libinput debug-events

CHANGE SOMETHING — QUICK RECIPES
--------------------------------
  New personal binding:
    Edit ~/xremap_config.yaml under keymap:. xreload.

  New device:
    xsetup --reconfigure → pick TARGET_DEVICE from list.
    Add device: {only: X} to any binding that must fire from X
    when X isn't the default.

  Move repo:
    Run xsetup from new location. REPODIR updates automatically.

  Switch back to two devices:
    xsetup --reconfigure → TARGET_DEVICE="kanata ministeren",
                           PERSONAL_DEVICES="kanata standard"
    Remove device: blocks from ~/xremap_config.yaml or set them to
    kanata standard. xreload.

MENTAL MODEL
------------
    env → merger.py → /tmp/merged.yaml → xremap

    TARGET_DEVICE     = default keyboard for bindings
    PERSONAL_DEVICES  = extra keyboards xremap listens to
    device: {only:X}  = binding only fires from X

    Ctrl+V needs: its device listed (TARGET_DEVICE or PERSONAL_DEVICES)
                  AND a matching device: block (or inheritance)
