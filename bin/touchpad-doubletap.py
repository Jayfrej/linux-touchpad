#!/usr/bin/env python3
import math
import os
import subprocess
import sys
import time

from evdev import InputDevice, ecodes

DEVICE = "/dev/input/event5"

TAP_MAX_DURATION = 0.25   # seconds: single contact must lift within this to count as a tap
DOUBLE_TAP_WINDOW = 0.4   # seconds: max gap between two taps to count as a double-tap

YDOTOOL_ENV = {**os.environ, "YDOTOOL_SOCKET": "/tmp/.ydotool_socket"}


def send_click():
    result = subprocess.run(
        ["ydotool", "key", "272:1", "272:0"],
        env=YDOTOOL_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(f"DEBUG: ydotool exit={result.returncode} output={result.stdout!r}", flush=True)


def movement_threshold(dev):
    info = dev.absinfo(ecodes.ABS_MT_POSITION_X)
    span = info.max - info.min
    return span * 0.03  # ~3% of the pad's width counts as "moved", not a clean tap


def main():
    dev = InputDevice(DEVICE)
    max_move = movement_threshold(dev)

    contacts = {}       # slot -> dict(start_time, tainted, start_x, start_y, x, y)
    current_slot = 0
    last_tap_time = None

    for event in dev.read_loop():
        if event.type != ecodes.EV_ABS:
            continue

        if event.code == ecodes.ABS_MT_SLOT:
            current_slot = event.value

        elif event.code == ecodes.ABS_MT_TRACKING_ID:
            now = time.monotonic()
            if event.value != -1:
                tainted = len(contacts) > 0
                if tainted:
                    for c in contacts.values():
                        c["tainted"] = True
                contacts[current_slot] = {
                    "start_time": now,
                    "tainted": tainted,
                    "start_x": None,
                    "start_y": None,
                    "x": None,
                    "y": None,
                }
            else:
                c = contacts.pop(current_slot, None)
                if c is None:
                    print("DEBUG: lift with no contact recorded", flush=True)
                    continue
                if c["tainted"]:
                    print("DEBUG: ignored tainted (multi-finger) contact", flush=True)
                    continue
                duration = now - c["start_time"]
                moved = 0.0
                if (
                    c["start_x"] is not None
                    and c["x"] is not None
                    and c["start_y"] is not None
                    and c["y"] is not None
                ):
                    moved = math.hypot(c["x"] - c["start_x"], c["y"] - c["start_y"])
                print(f"DEBUG: tap candidate duration={duration:.3f}s moved={moved:.1f} (max_move={max_move:.1f})", flush=True)
                if duration > TAP_MAX_DURATION or moved > max_move:
                    print("DEBUG: rejected (too slow or moved too much)", flush=True)
                    continue
                if last_tap_time is not None and now - last_tap_time < DOUBLE_TAP_WINDOW:
                    print("DEBUG: DOUBLE TAP -> clicking", flush=True)
                    send_click()
                    last_tap_time = None
                else:
                    print("DEBUG: first tap registered, waiting for second", flush=True)
                    last_tap_time = now

        elif event.code == ecodes.ABS_MT_POSITION_X:
            c = contacts.get(current_slot)
            if c is not None:
                c["x"] = event.value
                if c["start_x"] is None:
                    c["start_x"] = event.value

        elif event.code == ecodes.ABS_MT_POSITION_Y:
            c = contacts.get(current_slot)
            if c is not None:
                c["y"] = event.value
                if c["start_y"] is None:
                    c["start_y"] = event.value


if __name__ == "__main__":
    sys.exit(main())
