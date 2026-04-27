#!/usr/bin/env python3
"""
MicroRave Live Hardware Test
=============================
Simulates realistic human interaction against real hardware.
Runs with actual GPIO, display, audio, and relay board.

Stop the service before running:
    sudo systemctl stop microrave

Run from /home/pi/MicroRave:
    sudo DISPLAY=:0 XAUTHORITY=/home/pi/.Xauthority venv/bin/python tests/live_test.py

Ctrl+C to abort at any time.
"""
import os
import sys
import time
import random
import signal
import subprocess

# ── Must run as root for GPIO + display ───────────────────────────────────────
if os.geteuid() != 0:
    print("ERROR: Run with sudo.")
    sys.exit(1)

# ── Kill any other microrave processes ────────────────────────────────────────
_my_pid = os.getpid()
_pgrep  = subprocess.run(["pgrep", "-f", "microrave"], capture_output=True, text=True)
for _p in _pgrep.stdout.split():
    try:
        _pid = int(_p)
        if _pid != _my_pid:
            os.kill(_pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from microrave import MicroRaveApp, State  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def pause(lo: float, hi: float = 0):
    """Human-paced pause. If hi given, random between lo and hi."""
    time.sleep(random.uniform(lo, hi) if hi else lo)

def post(fn, *args):
    app._post(fn, *args)

def say(msg: str):
    print(f"\n  ► {msg}")

def step(label: str, fn, *args, before: float = 0, after: float = 1.2):
    if before:
        pause(before)
    print(f"    [{label}]")
    post(fn, *args)
    pause(after)

def wait_for_state(target, timeout: float = 60.0):
    """Block until app reaches target state or timeout."""
    t0 = time.monotonic()
    while app._state != target:
        if time.monotonic() - t0 > timeout:
            print(f"    (timeout waiting for {target.name})")
            return False
        time.sleep(0.1)
    return True

def wait_for_idle(timeout: float = 120.0):
    """Block until app is back in IDLE (session fully over)."""
    return wait_for_state(State.IDLE, timeout)

# ── Boot ─────────────────────────────────────────────────────────────────────

print("\n" + "=" * 65)
print("  MicroRave Live Hardware Test")
print("  Unattended overnight — Ctrl+C to stop")
print("=" * 65)

app = MicroRaveApp()
app._drain()
pause(2.0)

session = 0

try:
    while True:
        session += 1
        print(f"\n{'─' * 65}")
        print(f"  SESSION {session}  —  state: {app._state.name}")
        print(f"{'─' * 65}")

        # ── Scenario A: Tourist mode — exploring the panel ────────────────────
        say("New user discovers the machine. Poking at DJ buttons.")
        step("DJ2",  app._on_dj,    2,  after=0.8)
        step("DJ5",  app._on_dj,    5,  after=0.6)
        step("DJ1",  app._on_dj,    1,  after=1.5)

        say("Tries pressing digits — not sure what they do.")
        step("3",    app._on_digit, 3,  after=0.4)
        step("7",    app._on_digit, 7,  after=0.3)
        step("STOP", app._on_stop,      after=1.2)

        # ── Scenario B: Tries START with no time entered ──────────────────────
        say("Presses START before entering any time.")
        step("START", app._on_start,    after=1.5)

        # ── Scenario C: Enters time, changes mind, re-enters ─────────────────
        say("Enters a time they don't want, clears it, tries again.")
        step("1",    app._on_digit, 1,  after=0.5)
        step("5",    app._on_digit, 5,  after=0.4)
        step("0",    app._on_digit, 0,  after=0.4)

        say("Changed mind — clears and enters 20 seconds instead.")
        pause(0.8, 1.5)
        step("STOP", app._on_stop,      after=0.7)
        step("2",    app._on_digit, 2,  after=0.4)
        step("0",    app._on_digit, 0,  after=0.6)
        step("START", app._on_start,    after=0.5)

        say("Cooking for 20 seconds...")
        wait_for_state(State.COUNTING_DOWN)
        pause(5.0)

        # ── Scenario D: Add time mid-cook ─────────────────────────────────────
        say("Taps +30s a couple of times.")
        step("+30s", app._on_add_30,    after=0.5)
        step("+30s", app._on_add_30,    after=1.0)

        say("Waiting for session to finish...")
        wait_for_idle(120)
        pause(2.0)

        # ── Scenario E: Door opens mid-cook ───────────────────────────────────
        say("Enters 45 seconds and starts cooking.")
        step("4",    app._on_digit, 4,  after=0.4)
        step("5",    app._on_digit, 5,  after=0.5)
        step("START", app._on_start,    after=0.5)

        wait_for_state(State.COUNTING_DOWN)
        pause(8.0, 12.0)

        say("Door opens — music pauses.")
        step("DOOR OPEN",  app._on_door_open,  after=0.5)
        wait_for_state(State.PAUSED)
        pause(4.0, 7.0)

        say("Door closes — music resumes.")
        step("DOOR CLOSE", app._on_door_close, after=1.0)

        say("Waiting for session to finish...")
        wait_for_idle(120)
        pause(2.0)

        # ── Scenario F: DJ change mid-cook (light should stay on playing DJ) ──
        say("Enters 30 seconds, starts cooking.")
        step("3",    app._on_digit, 3,  after=0.4)
        step("0",    app._on_digit, 0,  after=0.5)
        step("START", app._on_start,    after=0.5)

        wait_for_state(State.COUNTING_DOWN)
        pause(5.0, 8.0)

        say("Presses a different DJ mid-cook — light should NOT move yet.")
        next_dj = random.choice([dj for dj in range(1, 7) if dj != app.playlists.selected])
        step(f"DJ{next_dj}", app._on_dj, next_dj, after=1.0)

        say("Waiting for session to finish — light should move after race.")
        wait_for_idle(120)
        pause(2.0)

        # ── Scenario G: Easter egg 069 ────────────────────────────────────────
        say("Spells out 069 — easter egg time.")
        step("0",    app._on_digit, 0,  after=0.5)
        step("6",    app._on_digit, 6,  after=0.4)
        step("9",    app._on_digit, 9,  after=0.8)
        step("START", app._on_start,    after=0.5)

        say("Watching easter egg animation and countdown...")
        wait_for_idle(180)
        pause(3.0)

        # ── Scenario H: Rapid digit entry (fat fingers) ───────────────────────
        say("Fast entry — fat fingers, enters too many digits.")
        step("1",    app._on_digit, 1,  after=0.2)
        step("2",    app._on_digit, 2,  after=0.15)
        step("3",    app._on_digit, 3,  after=0.15)
        step("4",    app._on_digit, 4,  after=0.15)
        step("5",    app._on_digit, 5,  after=0.15)

        say("Clears and tries a normal 15-second cook.")
        step("STOP", app._on_stop,      after=0.7)
        step("1",    app._on_digit, 1,  after=0.4)
        step("5",    app._on_digit, 5,  after=0.5)
        step("START", app._on_start,    after=0.5)

        say("Waiting for session to finish...")
        wait_for_idle(120)

        say(f"Session {session} complete. Looping...\n")
        pause(3.0, 5.0)

except KeyboardInterrupt:
    say("Interrupted by user.")
finally:
    app._shutdown()
    print("\n  Test ended cleanly.\n")
