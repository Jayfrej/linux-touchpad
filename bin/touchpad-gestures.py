#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import threading
import time

from evdev import InputDevice, ecodes


def _find_device(name_substring, fallback):
    """Auto-detect an input device by matching a substring against
    /sys/class/input/eventN/device/name. Event numbers are machine-specific
    and can shift after a kernel/driver update or a fresh boot, so this runs
    fresh every time the daemon starts instead of trusting a hardcoded path.
    Falls back to the last known-good path if nothing matches."""
    base = "/sys/class/input"
    try:
        entries = sorted(
            (e for e in os.listdir(base) if e.startswith("event")),
            key=lambda e: int(e[len("event"):]),
        )
    except OSError:
        return fallback
    for entry in entries:
        try:
            with open(os.path.join(base, entry, "device", "name")) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name_substring.lower() in name.lower():
            return f"/dev/input/{entry}"
    return fallback


DEVICE = _find_device("touchpad", "/dev/input/event4")
KEYBOARD_DEVICE = _find_device("keyboard", "/dev/input/event2")
SWIPE_MIN_DISTANCE = 15.0

SCROLL_BURST_GAP = 0.25          # seconds of silence that ends a scroll burst
SCROLL_HORIZ_THRESHOLD = 150.0   # minimum accumulated horizontal delta to count as a deliberate swipe
SCROLL_FIRE_COOLDOWN = 0.6       # seconds between back/forward triggers

ZOOM_STEP_RATIO = 1.15           # cumulative pinch scale change needed to fire one zoom step

# 3-finger tap (Application Dashboard): same root problem as
# touchpad-doubletap.py's single-finger tap -- with TapToClick=false,
# libinput doesn't emit *any* gesture event for a light tap-and-lift, 3
# fingers included (confirmed: a real 3-finger tap produced zero output
# from `libinput debug-events`, not even a short GESTURE_SWIPE). So this
# has to be read the same way double-tap-to-click is: raw ABS_MT_* protocol
# B events straight from the kernel, below libinput entirely.
THREE_TAP_MAX_DURATION = 0.4   # seconds from first finger down to last finger up
THREE_TAP_MAX_MOVEMENT = 0.03  # fraction of pad width/height a finger may drift and still count as a tap
# Require two 3-finger taps in a row (like the existing single-finger
# double-tap-to-click) rather than firing on the first one -- a single
# 3-finger tap is too easy to trigger by accident while resting/repositioning
# fingers on the pad.
THREE_TAP_DOUBLE_WINDOW = 0.5   # max gap between the two taps

YDOTOOL_ENV = {**os.environ, "YDOTOOL_SOCKET": "/tmp/.ydotool_socket"}
KEY_LEFTCTRL = "29"
KEY_LEFTALT = "56"
KEY_LEFT = "105"
KEY_RIGHT = "106"
KEY_DOWN = "108"
KEY_D = "32"
KEY_C = "46"
KEY_TAB = "15"
KEY_LEFTSHIFT = "42"

# Application Dashboard (org.kde.plasma.kickerdash, pinned to the bottom
# panel) has no D-Bus/kglobalaccel toggle of its own -- kglobalaccel only
# grows a shortcut for it once one is set via its own right-click >
# "Configure Keyboard Shortcut" menu, and even then it's not obviously
# addressable by name over D-Bus. `plasmawindowed org.kde.plasma.kickerdash`
# looked like an easy substitute but opens a *decorated standalone window*
# running a fresh instance of the widget -- visually wrong, not the real
# fullscreen panel popup. Simplest reliable fix: a Ctrl+Alt+D keyboard
# shortcut was bound to the *real* panel applet by hand (see README), and we
# just replay that key combo via ydotool -- identical to how Opera
# back/forward is done below.
APP_DASHBOARD_SHORTCUT = [f"{KEY_LEFTCTRL}:1", f"{KEY_LEFTALT}:1", f"{KEY_D}:1", f"{KEY_D}:0", f"{KEY_LEFTALT}:0", f"{KEY_LEFTCTRL}:0"]

BEGIN_RE = re.compile(r"GESTURE_(SWIPE|PINCH)_BEGIN\s+\+[\d.]+s\s+(\d+)")
SWIPE_UPDATE_RE = re.compile(r"GESTURE_SWIPE_UPDATE.*?\+[\d.]+s\s+\d+\s+(-?[\d.]+)/\s*(-?[\d.]+)")
PINCH_UPDATE_RE = re.compile(r"GESTURE_PINCH_UPDATE.*?([\d.]+)\s+@")
END_RE = re.compile(r"GESTURE_(SWIPE|PINCH)_END\s+\+[\d.]+s\s+(\d+)")
SCROLL_RE = re.compile(
    r"POINTER_SCROLL_FINGER.*?\+([\d.]+)s\s+vert\s+(-?[\d.]+)/[\d.]+\*?\s+horiz\s+(-?[\d.]+)/[\d.]+\*?"
)


def invoke_shortcut(name, component="kwin"):
    subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", "org.kde.kglobalaccel",
            "--object-path", f"/component/{component}",
            "--method", "org.kde.kglobalaccel.Component.invokeShortcut",
            name,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def send_alt_arrow(direction):
    key = KEY_RIGHT if direction == "right" else KEY_LEFT
    subprocess.run(
        ["ydotool", "key", f"{KEY_LEFTALT}:1", f"{key}:1", f"{key}:0", f"{KEY_LEFTALT}:0"],
        env=YDOTOOL_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def handle_swipe(fingers, dx, dy):
    """3-finger swipe up/down (Cube). A dragged gesture like
    this reads fine straight from libinput, unlike the light tap handled
    by watch_raw_touchpad_events below."""
    if fingers != 3:
        return
    if abs(dx) < SWIPE_MIN_DISTANCE and abs(dy) < SWIPE_MIN_DISTANCE:
        return
    if abs(dy) > abs(dx):
        invoke_shortcut("Cube")
    # left/right 3-finger: leave to KWin's native virtual-desktop switch gesture


def watch_raw_touchpad_events():
    """Background thread: detects a light 3-finger tap (no dragging) by
    reading raw multitouch protocol B events directly. Also detects
    single-finger right-edge vertical swipes for volume control."""
    dev = InputDevice(DEVICE)
    x_info = dev.absinfo(ecodes.ABS_MT_POSITION_X)
    y_info = dev.absinfo(ecodes.ABS_MT_POSITION_Y)
    max_move_x = (x_info.max - x_info.min) * THREE_TAP_MAX_MOVEMENT
    max_move_y = (y_info.max - y_info.min) * THREE_TAP_MAX_MOVEMENT
    
    right_edge_x = x_info.max - (x_info.max - x_info.min) * 0.10
    left_edge_x = x_info.min + (x_info.max - x_info.min) * 0.10
    bottom_edge_y = y_info.max - (y_info.max - y_info.min) * 0.10
    vol_step_y = (y_info.max - y_info.min) * 0.015
    left_swipe_threshold_y = (y_info.max - y_info.min) * 0.05
    horiz_step_x = (x_info.max - x_info.min) * 0.30

    contacts = {}          # slot -> {"start_x", "start_y", "x", "y", "vol_y", "left_action_fired", "bottom_active", "last_tab_x"}
    current_slot = 0
    group_start_time = None
    peak_count = 0
    tainted = False
    last_tap_time = None

    for event in dev.read_loop():
        if event.type != ecodes.EV_ABS:
            continue

        if event.code == ecodes.ABS_MT_SLOT:
            current_slot = event.value

        elif event.code == ecodes.ABS_MT_TRACKING_ID:
            now = time.monotonic()
            if event.value != -1:
                if not contacts:
                    group_start_time = now
                    peak_count = 0
                    tainted = False
                contacts[current_slot] = {"start_x": None, "start_y": None, "x": None, "y": None, "vol_y": None, "left_action_fired": False, "bottom_active": False, "last_tab_x": None}
                peak_count = max(peak_count, len(contacts))
                if peak_count > 3:
                    tainted = True
            else:
                c = contacts.pop(current_slot, None)
                if not contacts and group_start_time is not None:
                    duration = now - group_start_time
                    is_tap = not tainted and peak_count == 3 and duration <= THREE_TAP_MAX_DURATION
                    group_start_time = None
                    if not is_tap:
                        continue
                    if last_tap_time is not None and now - last_tap_time <= THREE_TAP_DOUBLE_WINDOW:
                        open_app_dashboard()
                        last_tap_time = None
                    else:
                        last_tap_time = now

        elif event.code == ecodes.ABS_MT_POSITION_X:
            c = contacts.get(current_slot)
            if c is not None:
                c["x"] = event.value
                if c["start_x"] is None:
                    c["start_x"] = event.value
                    c["left_action_fired"] = False
                    c["bottom_active"] = False
                    c["last_tab_x"] = event.value
                elif abs(c["x"] - c["start_x"]) > max_move_x:
                    tainted = True
                
                if len(contacts) == 1 and c["start_y"] is not None and c["start_x"] is not None:
                    if c["start_y"] > bottom_edge_y and left_edge_x < c["start_x"] < right_edge_x:
                        dx = c["x"] - c["last_tab_x"]
                        if not c.get("bottom_active") and abs(dx) > horiz_step_x:
                            c["bottom_active"] = True
                        
                        if c.get("bottom_active"):
                            if dx > horiz_step_x:
                                invoke_shortcut("Walk Through Windows")
                                c["last_tab_x"] += horiz_step_x
                            elif dx < -horiz_step_x:
                                invoke_shortcut("Walk Through Windows (Reverse)")
                                c["last_tab_x"] -= horiz_step_x

        elif event.code == ecodes.ABS_MT_POSITION_Y:
            c = contacts.get(current_slot)
            if c is not None:
                c["y"] = event.value
                if c["start_y"] is None:
                    c["start_y"] = event.value
                    c["vol_y"] = event.value
                elif abs(c["y"] - c["start_y"]) > max_move_y:
                    tainted = True
                
                if len(contacts) == 1 and c["start_x"] is not None:
                    if c["start_x"] > right_edge_x:
                        if c["vol_y"] is not None:
                            dy = c["y"] - c["vol_y"]
                            if dy > vol_step_y:
                                print(f"Volume DOWN: dy={dy} > step={vol_step_y}", flush=True)
                                invoke_shortcut("decrease_volume", component="kmix")
                                c["vol_y"] += vol_step_y
                            elif dy < -vol_step_y:
                                print(f"Volume UP: dy={dy} < -step={-vol_step_y}", flush=True)
                                invoke_shortcut("increase_volume", component="kmix")
                                c["vol_y"] -= vol_step_y
                    elif c["start_x"] < left_edge_x and not c.get("left_action_fired"):
                        if c["start_y"] is not None:
                            dy = c["y"] - c["start_y"]
                            if dy > left_swipe_threshold_y:
                                invoke_shortcut("GestureMinimizeActive")
                                c["left_action_fired"] = True
                            elif dy < -left_swipe_threshold_y:
                                invoke_shortcut("GestureRestoreLastMinimized")
                                c["left_action_fired"] = True


def handle_pinch(scale, shift_held):
    # Shift+pinch is handled live, step-by-step, in main()'s PINCH_UPDATE
    # branch (see PinchZoomTracker) so zoom tracks finger spread in real
    # time instead of firing once at gesture end.
    pass


class PinchZoomTracker:
    """Fires a zoom-in/out step each time the pinch scale has moved
    ZOOM_STEP_RATIO since the last step, so a Shift+pinch gesture feels
    proportional instead of a single fixed jump per gesture."""

    def __init__(self):
        self.reference_scale = 1.0

    def reset(self):
        self.reference_scale = 1.0

    def feed(self, scale):
        ratio = scale / self.reference_scale
        if ratio >= ZOOM_STEP_RATIO:
            invoke_shortcut("view_zoom_in")
            self.reference_scale = scale
        elif ratio <= 1.0 / ZOOM_STEP_RATIO:
            invoke_shortcut("view_zoom_out")
            self.reference_scale = scale


def open_app_dashboard():
    subprocess.run(
        ["ydotool", "key", *APP_DASHBOARD_SHORTCUT],
        env=YDOTOOL_ENV,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def watch_shift_key(state):
    """Background thread: keeps state['shift'] in sync with whether either
    Shift key is currently held, by reading the keyboard device directly
    (the touchpad daemon has no other way to learn keyboard modifier state).
    Only used for Shift+pinch (zoom) now -- Application Dashboard moved to a
    3-finger tap, see watch_three_finger_tap."""
    dev = InputDevice(KEYBOARD_DEVICE)
    for event in dev.read_loop():
        if event.type != ecodes.EV_KEY:
            continue
        if event.code in (ecodes.KEY_LEFTSHIFT, ecodes.KEY_RIGHTSHIFT):
            state["shift"] = event.value != 0


class ScrollBurstTracker:
    def __init__(self):
        self.horiz_total = 0.0
        self.vert_total = 0.0
        self.last_event_time = None
        self.last_fire_time = 0.0

    def feed(self, ts, vert, horiz):
        if self.last_event_time is not None and ts - self.last_event_time > SCROLL_BURST_GAP:
            self.finish()
        if vert == 0.0 and horiz == 0.0:
            self.finish()
            self.last_event_time = ts
            return
        self.horiz_total += horiz
        self.vert_total += vert
        self.last_event_time = ts

    def finish(self):
        horiz, vert = self.horiz_total, self.vert_total
        self.horiz_total = 0.0
        self.vert_total = 0.0
        if abs(horiz) < SCROLL_HORIZ_THRESHOLD:
            return
        if abs(horiz) < abs(vert) * 2:
            return
        now = time.monotonic()
        if now - self.last_fire_time < SCROLL_FIRE_COOLDOWN:
            return
        self.last_fire_time = now
        send_alt_arrow("left" if horiz > 0 else "right")


def main():
    proc = subprocess.Popen(
        ["libinput", "debug-events", "--device", DEVICE],
        stdout=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    keyboard_state = {"shift": False}
    threading.Thread(target=watch_shift_key, args=(keyboard_state,), daemon=True).start()
    threading.Thread(target=watch_raw_touchpad_events, daemon=True).start()

    gesture_type = None
    gesture_shift = False
    fingers = 0
    dx_total = 0.0
    dy_total = 0.0
    last_scale = None
    scroll = ScrollBurstTracker()
    pinch_zoom = PinchZoomTracker()

    for line in proc.stdout:
        m = SCROLL_RE.search(line)
        if m:
            scroll.feed(float(m.group(1)), float(m.group(2)), float(m.group(3)))
            continue

        m = BEGIN_RE.search(line)
        if m:
            gesture_type = m.group(1)
            fingers = int(m.group(2))
            dx_total = dy_total = 0.0
            last_scale = None
            gesture_shift = keyboard_state["shift"]
            pinch_zoom.reset()
            continue

        if gesture_type == "SWIPE":
            m = SWIPE_UPDATE_RE.search(line)
            if m:
                dx_total += float(m.group(1))
                dy_total += float(m.group(2))
                continue
        elif gesture_type == "PINCH":
            m = PINCH_UPDATE_RE.search(line)
            if m:
                last_scale = float(m.group(1))
                if gesture_shift:
                    pinch_zoom.feed(last_scale)
                continue

        m = END_RE.search(line)
        if m:
            kind = m.group(1)
            if kind == "SWIPE":
                handle_swipe(fingers, dx_total, dy_total)
            elif kind == "PINCH":
                handle_pinch(last_scale, gesture_shift)
            gesture_type = None


if __name__ == "__main__":
    sys.exit(main())
