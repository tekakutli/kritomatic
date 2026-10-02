#!/usr/bin/env python3
"""
Toggle default image viewer between Krita and imv for PNG and JPG files
"""

import os
import subprocess
import sys
from pathlib import Path

def run_command(cmd):
    """Run a shell command and return output"""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1

def show_notification(message, is_error=True):
    """Show desktop notification"""
    try:
        icon = 'dialog-error' if is_error else 'dialog-information'
        subprocess.Popen([
            'notify-send',
            'Toggle Image App' if is_error else 'Success',
            message,
            '-i', icon,
            '-t', '3000'
        ])
    except FileNotFoundError:
        print(message)

def get_current_app():
    """Get current default app for PNG files"""
    stdout, stderr, code = run_command("xdg-mime query default image/png")
    if code == 0:
        return stdout
    return None

def create_krita_desktop_file():
    """Create custom .desktop file for Krita"""
    desktop_dir = Path.home() / ".local/share/applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = desktop_dir / "userapp-krita-custom.desktop"

    content = """[Desktop Entry]
Type=Application
Name=Krita (Custom)
Exec=/usr/local/bin/krita %F
Icon=krita
MimeType=image/png;image/jpeg;
Categories=Graphics;2DGraphics;RasterGraphics;
Terminal=false
StartupNotify=true
"""

    desktop_file.write_text(content)
    os.chmod(desktop_file, 0o644)
    return str(desktop_file)

def create_imv_desktop_file():
    """Create custom .desktop file for imv if needed"""
    standard_paths = [
        Path("/usr/share/applications/imv.desktop"),
        Path.home() / ".local/share/applications/imv.desktop"
    ]

    for path in standard_paths:
        if path.exists():
            return str(path)

    desktop_dir = Path.home() / ".local/share/applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    desktop_file = desktop_dir / "imv.desktop"

    content = """[Desktop Entry]
Type=Application
Name=imv
Exec=imv %F
Icon=imv
MimeType=image/png;image/jpeg;image/gif;
Categories=Graphics;Viewer;
Terminal=false
StartupNotify=false
"""

    desktop_file.write_text(content)
    os.chmod(desktop_file, 0o644)
    return str(desktop_file)

def set_default_app(desktop_file, mime_types):
    """Set default app for given MIME types"""
    for mime in mime_types:
        cmd = f'xdg-mime default "{desktop_file}" "{mime}"'
        print(f"  Setting {mime} to {desktop_file}")
        _, stderr, code = run_command(cmd)
        if code != 0:
            print(f"  Warning: {stderr}")

    run_command("update-desktop-database ~/.local/share/applications/ 2>/dev/null")

def main():
    mime_types = ["image/png", "image/jpeg"]

    current_app = get_current_app()
    print(f"Current default for PNG: {current_app}")

    if current_app and "krita" in current_app.lower():
        print("Switching from Krita to imv...")
        desktop_file = create_imv_desktop_file()
        action = "imv"
        notification_msg = "Switched to imv"
    elif current_app and "imv" in current_app.lower():
        print("Switching from imv to Krita...")
        desktop_file = create_krita_desktop_file()
        action = "Krita"
        notification_msg = "Switched to Krita"
    else:
        print("Current default is not Krita or imv (or not set)")
        print("Options:")
        print("  1) Set to Krita (/usr/local/bin/krita)")
        print("  2) Set to imv")

        try:
            choice = input("Choose (1/2): ").strip()
        except KeyboardInterrupt:
            print("\nCancelled")
            show_notification("Cancelled by user")
            sys.exit(0)

        if choice == "1":
            desktop_file = create_krita_desktop_file()
            action = "Krita"
            notification_msg = "Set to Krita"
        elif choice == "2":
            desktop_file = create_imv_desktop_file()
            action = "imv"
            notification_msg = "Set to imv"
        else:
            print("Invalid choice")
            show_notification("Invalid choice")
            sys.exit(1)

    desktop_filename = Path(desktop_file).name

    print(f"\nSetting default to {action}...")
    set_default_app(desktop_filename, mime_types)

    print(f"\n✓ Default image viewer switched to {action}")
    new_app = get_current_app()
    print(f"Verification: {new_app}")

    show_notification(f"{notification_msg} successfully", is_error=False)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelled")
        show_notification("Cancelled by user")
        sys.exit(0)
    except Exception as e:
        error_msg = f"Error: {e}"
        print(error_msg, file=sys.stderr)
        show_notification(error_msg)
        sys.exit(1)
