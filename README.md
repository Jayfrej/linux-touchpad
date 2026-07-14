# Custom touchpad gestures (KDE Plasma 6, Wayland)

Built because Plasma's built-in gesture support is limited (only 4-finger swipe up/down
are configurable out of the box) and Chromium/Opera has a long-standing unfixed bug that
prevents native 2-finger back/forward navigation on Linux Wayland. Everything here reads
raw touchpad signals ourselves and drives KDE/Opera through D-Bus and synthetic key
events instead of depending on those broken/missing native paths.

Machine this was built on: Fedora 44, KDE Plasma 6.6/6.7, touchpad `SYNA32AA:00 06CB:CE17`
at `/dev/input/event5`. **The event number is machine-specific and can change on a fresh
install or after a kernel/driver update** — always re-detect it (see `install.sh`).

## What's active right now

| # | Piece | Does what | Status |
|---|-------|-----------|--------|
| 1 | `kcminputrc`: `TapToClick=false` | Disables tap-to-click; only a real physical press registers as a click | ✅ working |
| 2 | `kcminputrc`: `NaturalScroll=true` | Mac-style natural scroll direction | ✅ was already on by default |
| 3 | 4 virtual desktops (`kwinrc [Desktops]`) | Gives the native 3-finger left/right swipe (desktop switch) something to do | ✅ working, **but has reset itself to 1 desktop at least once this session for no clear reason — check `Number=` in `kwinrc [Desktops]` after every reboot** |
| 4 | `gesture-helper` KWin script | Registers two custom global shortcuts (`GestureMinimizeActive`, `GestureRestoreLastMinimized`) that our daemon calls | ✅ working, **but does not reliably auto-load from `kwinrc` on a fresh boot — see "Troubleshooting: 3-finger swipe stops working after a reboot" below. `kwin-script-loader.service` force-loads it every session as a workaround.** |
| 5 | `touchpad-gestures.service` | 3-finger up/down → minimize/restore window · pinch → switch app (Alt+Tab) · fast 2-finger horizontal scroll → Alt+Left/Right (Opera back/forward) | ✅ working |
| 6 | `touchpad-doubletap.service` | Two quick light taps = one click (single light taps still do nothing) | ✅ confirmed working end-to-end |
| 7 | `ydotool` + `ydotoold` | Lets our daemons send real key/click events on Wayland | ✅ working, **but see the "ydotool gotchas" section — the obvious commands don't work** |
| 8 | `99-ydotool-mouse.rules` (udev) | Tags ydotool's virtual input device as a mouse | ✅ applied. Note: even with this, synthetic **mouse motion** (`ydotool mousemove`) still does not move the cursor — see below. Only needed for the click path; may not even be required for that (never isolated/retested without it). |

## Gesture map

| Gesture | Action |
|---|---|
| Physical press (not a light tap) | Click |
| Two quick light taps | Click *(unverified, see #6 above)* |
| 3-finger swipe down | Minimize active window |
| 3-finger swipe up | Restore the window minimized by the swipe above |
| 3-finger swipe left/right | Switch virtual desktop (native Plasma default) |
| Pinch in / out | Switch to previous / next app (`Walk Through Windows (Reverse)` / `Walk Through Windows`) |
| 4-finger swipe up/down | Overview / Grid view (native Plasma default) |
| Fast 2-finger swipe left/right (in-app, e.g. Opera) | Back / Forward (sends Alt+Left / Alt+Right) |
| Two quick light taps | Click |

Things intentionally **not** bound: 4-finger swipe left/right (tried it for app-switching,
reverted — see "Rejected approaches" below).

## Why this needed custom daemons instead of just KDE settings

- **Tap-to-click / natural scroll**: plain `kcminputrc` settings, no daemon needed. The
  tricky part is that editing `kcminputrc` by hand and calling `qdbus .../KWin
  reconfigure` does **not** apply live — you have to set the property directly on
  `org.kde.KWin.InputDevice.<eventN>` over D-Bus (or use the System Settings GUI, which
  does this for you). `reconfigure` only affects compositor/effects config, not per-device
  libinput settings.
- **Minimize/restore, pinch-to-switch-app, 3-finger swipes**: Plasma 6 has no UI to bind
  touchpad gestures to arbitrary actions — only the four hardcoded defaults exist
  (3-finger L/R = desktop switch, 4-finger up = Overview, 4-finger down = Grid), compiled
  into KWin's C++ (`overvieweffect.cpp` etc.), not stored in any config file. The one
  third-party plugin that adds this (`kwin-gestures`, github.com/gbytedev/kwin-gestures)
  **fails to build** on this system: Fedora's `kwin-devel` package is already at 6.7.2
  while the running compositor was 6.6.4 at the time, and KWin changed its internal
  gesture-filter API (individual `fingerCount`/`time` args → an `event` struct object)
  between those versions in a way the plugin's source hasn't caught up with. Patching a
  compositor plugin against undocumented internal APIs was judged too risky (crashes
  there can crash the whole session), so we built the gesture recognition ourselves
  instead, entirely outside KWin: `libinput debug-events` gives us parsed
  `GESTURE_SWIPE_*` / `GESTURE_PINCH_*` events, we do the finger-count/direction logic in
  Python, and we trigger actions through existing KDE global shortcuts over D-Bus
  (`org.kde.kglobalaccel`), which is fully public/stable API.
- **Opera 2-finger back/forward**: this is a genuine, longstanding Chromium bug, not a
  KDE misconfiguration. Chromium's touchpad "fling"/kinetic-scroll handling (needed to
  detect an overscroll-navigation swipe) was written for ChromeOS only; a 2022 patch that
  adds Linux support was only ever applied to one Flatpak build, never merged upstream.
  So on Wayland, official Chromium/Opera builds correctly *receive* the horizontal scroll
  (confirmed empirically — `libinput debug-events` shows clean horizontal deltas up to 30+
  units per event when swiping) but never convert it into a back/forward action, and no
  `--enable-features=...` flag fixes it because the feature's supporting code simply isn't
  there for non-ChromeOS Linux. Our fix bypasses Opera's overscroll feature entirely:
  detect a fast, clearly-horizontal 2-finger scroll burst ourselves, then send a plain
  Alt+Left / Alt+Right keypress, which Opera (and most apps, e.g. Dolphin) already treats
  as its native back/forward keyboard shortcut. (We did add
  `--enable-features=TouchpadOverscrollHistoryNavigation` to Opera's launcher at one
  point while investigating — **that override has since been removed**, it turned out to
  be unnecessary once the daemon-based fix was working, and the flag doesn't do anything
  for us since we don't rely on Opera's native gesture handling at all.)
- **Tap-to-click stays off, but two light taps should still click**: libinput simply
  does not emit *any* event for a light tap once tap-to-click is disabled at the libinput
  level — confirmed by capturing `libinput debug-events` during light taps and seeing
  nothing at all, not even with `--verbose`. So detecting a tap has to happen **below**
  libinput, reading the kernel's raw multitouch protocol B events directly
  (`ABS_MT_TRACKING_ID`, `ABS_MT_POSITION_X/Y`) via `python-evdev`, tracking one contact
  per touch (down → up, short duration, little movement, no other finger down at the same
  time) and pairing up two of those within ~400ms.

## ydotool gotchas (spent a while on these — don't repeat the debugging)

1. **Default socket path is wrong for this ydotool version.** `ydotoold` here listens on
   `/tmp/.ydotool_socket`, but the `ydotool` client's built-in default is
   `/run/user/<uid>/.ydotool_socket`. Every invocation needs
   `YDOTOOL_SOCKET=/tmp/.ydotool_socket` set explicitly (both scripts do this).
2. **No symbolic key names.** `ydotool key alt+Left` silently does nothing useful (it's
   "non-interpretable" per ydotool's own help text — just causes a delay). You must use
   raw Linux keycodes with explicit press(`:1`)/release(`:0`) pairs, e.g. Alt+Left is
   `ydotool key 56:1 105:1 105:0 56:0` (56=KEY_LEFTALT, 105=KEY_LEFT, 106=KEY_RIGHT).
3. **`ydotool click 0x00` is broken** on this version (1.0.4-8.fc44) — it exits 0 and
   prints output but **never emits an actual event** (confirmed: capturing
   `libinput debug-events` on the virtual device while running `ydotool click 0x00`
   shows literally nothing arriving). Use the same raw-keycode trick instead, treating
   the mouse button as a plain `EV_KEY` code: `ydotool key 272:1 272:0` (272 = BTN_LEFT).
   This one *does* show up correctly as a `POINTER_BUTTON` event in libinput.
4. **Synthetic mouse motion doesn't work at all in this KWin/Wayland session**, even
   though `libinput debug-events` confirms the `POINTER_MOTION` event is generated and
   processed correctly by libinput (`ydotool mousemove -- 100 100` → libinput logs a
   clean `+50.00/+50.00` accelerated motion) — the cursor still never visibly moves.
   This looks like a KWin-side restriction on synthetic/uinput-sourced pointer motion
   specifically (Wayland's security model is generally stricter about letting arbitrary
   processes warp the cursor than about letting them press keys). We never found a fix
   for this and don't need one — clicking (the raw `key 272:1 272:0` trick above) works
   fine on its own at wherever the real cursor already is, no motion required. If a
   future feature needs synthetic *movement*, look into the XDG desktop portal's
   `org.freedesktop.portal.RemoteDesktop` + libei instead of ydotool/uinput — that's the
   properly-supported, compositor-aware way to do this on modern Wayland, ydotool
   predates it.

## Troubleshooting: 3-finger swipe stops working after a reboot

**Symptom:** everything else still works (tap-to-click, natural scroll, native
3-finger left/right desktop switch, native 4-finger up/down Overview/Grid,
double-tap-to-click) but 3-finger swipe up/down (minimize/restore) silently
does nothing.

This happened on 2026-07-14 after a plain reboot (no package updates
involved) and was root-caused end-to-end as follows — check these in order,
they're listed from "most likely" to "least likely" based on that
investigation:

1. **Check the KWin script is actually loaded (this was the actual cause
   last time):**
   ```
   gdbus call --session --dest org.kde.KWin --object-path /Scripting \
     --method org.kde.kwin.Scripting.isScriptLoaded "gesture-helper"
   ```
   If this prints `(false,)`, that's it — `gesture-helper` is installed
   (`kpackagetool6 --type KWin/Script --list` shows it) and `kwinrc`
   still has `[Plugins] gesture-helperEnabled=true`, but KWin did not
   actually autoload it this session. Confusingly, `kglobalaccel` will
   still list `GestureMinimizeActive` / `GestureRestoreLastMinimized` as
   registered shortcuts (stale from a previous load) and invoking them
   (`gdbus call --session --dest org.kde.kglobalaccel --object-path
   /component/kwin --method org.kde.kglobalaccel.Component.invokeShortcut
   "GestureMinimizeActive"`) returns success with **no error and no
   effect** — it's a no-op because nothing live is bound to that shortcut
   name anymore. `org.kde.KWin.reconfigure` does **not** fix this; it
   reloads compositor/effects config, not the script loader (confirmed by
   testing). The only thing that reliably re-loads it is calling
   `org.kde.kwin.Scripting.loadScript` directly with the script's file
   path, then `.start()`:
   ```
   gdbus call --session --dest org.kde.KWin --object-path /Scripting \
     --method org.kde.kwin.Scripting.loadScript \
     "$HOME/.local/share/kwin/scripts/gesture-helper/contents/code/main.js" \
     "gesture-helper"
   gdbus call --session --dest org.kde.KWin --object-path /Scripting \
     --method org.kde.kwin.Scripting.start
   ```
   `kwin-script-loader.service` (installed by `install.sh`, see
   `bin/kwin-script-loader.sh`) runs exactly this at every session start so
   you shouldn't have to do it by hand — but if the gesture still doesn't
   work after `install.sh`, check `systemctl --user status
   kwin-script-loader.service` first.

2. **If `isScriptLoaded` returns `true`**, the script itself is fine and the
   problem is further down the chain. Confirm the daemon is actually seeing
   3-finger swipes at the libinput level:
   ```
   libinput debug-events --device /dev/input/eventN
   ```
   (swap in the current touchpad event number — see the top of this file)
   and do a real 3-finger swipe. You should see `GESTURE_SWIPE_BEGIN` /
   `_UPDATE` / `_END` lines with a finger count of `3`. If you see
   `GESTURE_HOLD` or 2-finger `POINTER_SCROLL_FINGER` instead and never
   `GESTURE_SWIPE` with `3`, the touchpad isn't reporting a 3-finger swipe
   at all — check `DEVICE=` in the installed
   `~/.local/bin/touchpad-gestures.py` still points at the right event
   node (**re-detect it, it can change on reboot** — see the top of this
   file) and confirm `systemctl --user status touchpad-gestures.service`
   is actually running.
3. **If libinput sees the 3-finger swipe fine and the KWin script is
   loaded**, test the D-Bus path in isolation with a real window open and
   focused (an empty desktop with no windows means `workspace.activeWindow`
   is null and `gestureMinimizeActive()` does nothing — not a bug, just an
   invalid test):
   ```
   gdbus call --session --dest org.kde.kglobalaccel --object-path \
     /component/kwin --method org.kde.kglobalaccel.Component.invokeShortcut \
     "GestureMinimizeActive"
   ```
   If the focused window minimizes, the whole chain works end-to-end and
   the daemon should too.

## Known quirks / things to keep an eye on

- This touchpad reports **every pinch gesture as `fingers=4`**, regardless of how many
  fingers you actually pinch with. There's no way to distinguish a "zoom" pinch from an
  "app-switch" pinch by finger count on this hardware, so they *will* occasionally fire
  together (confirmed happens sometimes, not every time — probably depends on how
  reliably the browser's own native pinch-zoom fires on Wayland, which is itself spotty).
  We accepted this trade-off rather than dropping pinch-to-switch-app entirely.
- Finger-count detection itself (3 vs 4 for swipes) tested very reliably in a ~20-swipe
  test — no misdetections. But note this can never fully be "fixed" by anything in this
  repo even if it did misfire sometimes: KWin's native 3-finger-left/right desktop switch
  reads the same raw libinput finger count independently of our daemon, so if the
  hardware ever misreports 4 as 3, KWin will switch desktops no matter what our code is
  told to do with "4". This is why 4-finger left/right app-switching was tried and then
  reverted (see below) — not because our code was wrong, but because there's structurally
  no way to prevent KWin's own native gesture from firing on a misdetected finger count.

## Rejected approaches (don't re-try these without a reason)

- **4-finger swipe left/right for app-switching.** Implemented and worked, but reverted
  per the finger-misdetection concern above — shares an axis with the native 3-finger
  desktop-switch gesture, so a hardware misdetection there is unfixable from userspace.
  Pinch was brought back instead.
- **`kwin-gestures` (third-party KWin plugin).** See "Why this needed custom daemons"
  above — fails to compile against this system's `kwin-devel` (6.7.2) due to an internal
  KWin gesture-filter API change. Do not retry without checking upstream
  (github.com/gbytedev/kwin-gestures) for a version that supports the API change first.
- **Opera launch flag `--enable-features=TouchpadOverscrollHistoryNavigation`.** Doesn't
  do anything useful here — removed. See the Opera section above for why.

## Reinstalling on a fresh machine

Run `./install.sh`. It will:
1. `dnf install ydotool python3-evdev`
2. Add you to the `input` group (needed to read `/dev/input/eventN` — **log out and back
   in afterward**, or the daemons will only work via the `sg input -c` wrapper already
   baked into the systemd units, which is enough for the *first* run without logging out)
3. Auto-detect the touchpad's event device and patch it into both scripts
4. Install the KWin script, the three systemd `--user` services (the two gesture
   daemons plus `kwin-script-loader`, which works around the KWin script not
   reliably auto-loading on its own — see Troubleshooting), `ydotoold`'s systemd
   override + udev rule, and enable everything
5. Print the manual command you need for step 1 (`TapToClick=false`) because it requires
   a machine-specific `kcminputrc` group name (vendor/product/device-name triplet) that
   only exists after KDE has seen the touchpad at least once
6. Create 4 virtual desktops

Everything in the status table above is confirmed working as of this writing — if
something regresses after a reinstall, check the "ydotool gotchas" and "Known quirks"
sections first, they cover everything that wasn't obvious the first time around.

## Files in this repo

```
bin/touchpad-gestures.py       3-finger swipes, pinch app-switch, 2-finger back/forward
bin/touchpad-doubletap.py      double-tap-to-click
bin/kwin-script-loader.sh      force-loads gesture-helper over D-Bus (see Troubleshooting)
systemd/*.service              systemd --user units for the daemons + kwin-script-loader above
kwin-script/gesture-helper/    KWin script providing the minimize/restore shortcuts
udev/99-ydotool-mouse.rules    tags ydotoold's virtual device as a mouse
systemd-ydotool-override/      makes ydotoold's socket usable by a normal user
install.sh                     reinstall automation, see above
```
