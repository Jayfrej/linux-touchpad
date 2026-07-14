#!/usr/bin/env python3
import os
import re
import subprocess
import sys
import time

DEVICE = "/dev/input/event5"
SWIPE_MIN_DISTANCE = 15.0

SCROLL_BURST_GAP = 0.25          # seconds of silence that ends a scroll burst
SCROLL_HORIZ_THRESHOLD = 150.0   # minimum accumulated horizontal delta to count as a deliberate swipe
SCROLL_FIRE_COOLDOWN = 0.6       # seconds between back/forward triggers

YDOTOOL_ENV = {**os.environ, "YDOTOOL_SOCKET": "/tmp/.ydotool_socket"}
KEY_LEFTALT = "56"
KEY_LEFT = "105"
KEY_RIGHT = "106"

BEGIN_RE = re.compile(r"GESTURE_(SWIPE|PINCH)_BEGIN\s+\+[\d.]+s\s+(\d+)")
SWIPE_UPDATE_RE = re.compile(r"GESTURE_SWIPE_UPDATE.*?\+[\d.]+s\s+\d+\s+(-?[\d.]+)/\s*(-?[\d.]+)")
PINCH_UPDATE_RE = re.compile(r"GESTURE_PINCH_UPDATE.*?([\d.]+)\s+@")
END_RE = re.compile(r"GESTURE_(SWIPE|PINCH)_END\s+\+[\d.]+s\s+(\d+)")
SCROLL_RE = re.compile(
    r"POINTER_SCROLL_FINGER.*?\+([\d.]+)s\s+vert\s+(-?[\d.]+)/[\d.]+\*?\s+horiz\s+(-?[\d.]+)/[\d.]+\*?"
)


def invoke_shortcut(name):
    subprocess.run(
        [
            "gdbus", "call", "--session",
            "--dest", "org.kde.kglobalaccel",
            "--object-path", "/component/kwin",
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
    if fingers != 3:
        return
    if abs(dx) < SWIPE_MIN_DISTANCE and abs(dy) < SWIPE_MIN_DISTANCE:
        return
    if abs(dy) > abs(dx):
        if dy > 0:
            invoke_shortcut("GestureMinimizeActive")
        else:
            invoke_shortcut("GestureRestoreLastMinimized")
    # left/right 3-finger: leave to KWin's native virtual-desktop switch gesture


def handle_pinch(scale):
    if scale is None:
        return
    if scale < 1.0:
        invoke_shortcut("Walk Through Windows (Reverse)")
    elif scale > 1.0:
        invoke_shortcut("Walk Through Windows")


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

    gesture_type = None
    fingers = 0
    dx_total = 0.0
    dy_total = 0.0
    last_scale = None
    scroll = ScrollBurstTracker()

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
                continue

        m = END_RE.search(line)
        if m:
            kind = m.group(1)
            if kind == "SWIPE":
                handle_swipe(fingers, dx_total, dy_total)
            elif kind == "PINCH":
                handle_pinch(last_scale)
            gesture_type = None


if __name__ == "__main__":
    sys.exit(main())
