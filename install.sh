#!/usr/bin/env bash
# Reinstall script for the custom touchpad gesture setup.
# Run as the normal user (not root). Will prompt for sudo where needed.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Installing packages (ydotool, python3-evdev)"
sudo dnf install -y ydotool python3-evdev

echo "==> Adding $USER to the 'input' group (needed to read the touchpad device)"
sudo usermod -aG input "$USER"
echo "    NOTE: group membership only applies to NEW sessions. Either log out/in now,"
echo "    or keep using 'sg input -c ...' (already baked into the service files) until you do."

echo "==> Detecting touchpad event device"
TOUCHPAD_EVENT=$(grep -B5 'Handlers=.*mouse' /proc/bus/input/devices | grep -B5 -i touchpad | true)
DEV_PATH=""
for evdir in /sys/class/input/event*; do
    name_file="$evdir/device/name"
    [ -f "$name_file" ] || continue
    if grep -qi "touchpad" "$name_file"; then
        DEV_PATH="/dev/input/$(basename "$evdir")"
        break
    fi
done
if [ -z "$DEV_PATH" ]; then
    echo "    Could not auto-detect the touchpad device. Edit DEVICE= manually in:"
    echo "    ~/.local/bin/touchpad-gestures.py and ~/.local/bin/touchpad-doubletap.py"
    DEV_PATH="/dev/input/event5"
else
    echo "    Found touchpad at $DEV_PATH"
fi

echo "==> Installing the gesture daemons to ~/.local/bin"
mkdir -p "$HOME/.local/bin"
sed "s#DEVICE = \".*\"#DEVICE = \"$DEV_PATH\"#" bin/touchpad-gestures.py > "$HOME/.local/bin/touchpad-gestures.py"
sed "s#DEVICE = \".*\"#DEVICE = \"$DEV_PATH\"#" bin/touchpad-doubletap.py > "$HOME/.local/bin/touchpad-doubletap.py"
chmod +x "$HOME/.local/bin/touchpad-gestures.py" "$HOME/.local/bin/touchpad-doubletap.py"

echo "==> Installing the KWin script (gesture-helper: minimize/restore shortcuts)"
kpackagetool6 --type KWin/Script --install kwin-script/gesture-helper || \
    kpackagetool6 --type KWin/Script --upgrade kwin-script/gesture-helper
kwriteconfig6 --file kwinrc --group Plugins --key gesture-helperEnabled true

echo "==> Setting up ydotoold (types keys / clicks on Wayland)"
sudo mkdir -p /etc/systemd/system/ydotool.service.d
sudo cp systemd-ydotool-override/override.conf /etc/systemd/system/ydotool.service.d/override.conf
sudo cp udev/99-ydotool-mouse.rules /etc/udev/rules.d/99-ydotool-mouse.rules
sudo udevadm control --reload-rules
sudo systemctl daemon-reload
sudo systemctl enable --now ydotool.service

echo "==> Installing systemd --user services for the gesture daemons"
mkdir -p "$HOME/.config/systemd/user"
cp systemd/touchpad-gestures.service "$HOME/.config/systemd/user/"
cp systemd/touchpad-doubletap.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now touchpad-gestures.service
systemctl --user enable --now touchpad-doubletap.service

echo "==> Installing kwin-script-loader (works around gesture-helper not always"
echo "    auto-loading from kwinrc on a fresh boot -- see README Troubleshooting)"
cp bin/kwin-script-loader.sh "$HOME/.local/bin/"
chmod +x "$HOME/.local/bin/kwin-script-loader.sh"
cp systemd/kwin-script-loader.service "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now kwin-script-loader.service

echo "==> Disabling tap-to-click (must press physically to click)"
echo "    This step needs your touchpad's exact kcminputrc group name, which is"
echo "    machine-specific. Open System Settings > Touchpad once (so KDE creates the"
echo "    entry in ~/.config/kcminputrc), then run this and re-run this block:"
echo
echo "    kwriteconfig6 --file kcminputrc --group Libinput --group <vendor> --group <product> --group \"<device name>\" --key TapToClick false"
echo
echo "    Then push it live without logging out:"
echo "    gdbus call --session --dest org.kde.KWin --object-path /org/kde/KWin/InputDevice/<eventN> \\"
echo "      --method org.freedesktop.DBus.Properties.Set 'org.kde.KWin.InputDevice' 'tapToClick' '<false>'"

echo "==> Setting 4 virtual desktops"
gdbus call --session --dest org.kde.KWin --object-path /VirtualDesktopManager \
    --method org.kde.KWin.VirtualDesktopManager.createDesktop 1 "Desktop 2" || true
gdbus call --session --dest org.kde.KWin --object-path /VirtualDesktopManager \
    --method org.kde.KWin.VirtualDesktopManager.createDesktop 2 "Desktop 3" || true
gdbus call --session --dest org.kde.KWin --object-path /VirtualDesktopManager \
    --method org.kde.KWin.VirtualDesktopManager.createDesktop 3 "Desktop 4" || true

echo "==> Done. See README.md for what each piece does and how to verify it."
