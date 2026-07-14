#!/usr/bin/env bash
# Force-loads the gesture-helper KWin script over D-Bus at session start.
#
# Why this exists: `kwriteconfig6 --file kwinrc --group Plugins --key
# gesture-helperEnabled true` (what install.sh does) is supposed to make KWin
# autoload the script on every session start. In practice, on this machine,
# a fresh reboot sometimes leaves the script unloaded anyway -- kwinrc still
# says Enabled=true, `kpackagetool6 --type KWin/Script --list` still shows it
# installed, the global shortcuts (GestureMinimizeActive /
# GestureRestoreLastMinimized) even still show up in `kglobalaccel`'s
# component list (stale registration from a previous load) -- but
# `org.kde.kwin.Scripting.isScriptLoaded("gesture-helper")` over D-Bus
# returns false, and invoking either shortcut is a silent no-op. Calling
# `org.kde.KWin.reconfigure` does NOT fix this (confirmed: it reloads
# compositor/effects config, not the script loader). The only reliable fix
# found is to load the script directly by file path via
# `org.kde.kwin.Scripting.loadScript` + `.start()`, which is what this
# script does. See README.md "Troubleshooting" for the full diagnosis.
set -euo pipefail

SCRIPT_ID="gesture-helper"
SCRIPT_PATH="$HOME/.local/share/kwin/scripts/gesture-helper/contents/code/main.js"

is_loaded() {
    gdbus call --session --dest org.kde.KWin --object-path /Scripting \
        --method org.kde.kwin.Scripting.isScriptLoaded "$SCRIPT_ID" 2>/dev/null | grep -q true
}

# KWin's D-Bus scripting interface isn't necessarily up the instant
# graphical-session.target is reached, so poll for it briefly.
for _ in $(seq 1 30); do
    gdbus introspect --session --dest org.kde.KWin --object-path /Scripting >/dev/null 2>&1 && break
    sleep 1
done

if is_loaded; then
    echo "gesture-helper: already loaded, nothing to do"
    exit 0
fi

gdbus call --session --dest org.kde.KWin --object-path /Scripting \
    --method org.kde.kwin.Scripting.loadScript "$SCRIPT_PATH" "$SCRIPT_ID" >/dev/null
gdbus call --session --dest org.kde.KWin --object-path /Scripting \
    --method org.kde.kwin.Scripting.start >/dev/null

sleep 1
if is_loaded; then
    echo "gesture-helper: force-loaded successfully"
else
    echo "gesture-helper: force-load failed, isScriptLoaded still false" >&2
    exit 1
fi
