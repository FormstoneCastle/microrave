#!/usr/bin/env python3
"""
MicroRave Soak Test
===================
Runs for a configurable duration, firing random events at a human-realistic
pace and monitoring for memory leaks, thread accumulation, and dispatch errors.

Designed to run on the Pi 24/7 while the service is stopped (or in a second
SSH session). Uses mocked hardware — no GPIO or display required.

Usage:
    venv/bin/python tests/soak_test.py [duration_minutes] [--interval SECS]

Examples:
    venv/bin/python tests/soak_test.py 60          # 1 hour (default)
    venv/bin/python tests/soak_test.py 480         # overnight (8 hours)
    venv/bin/python tests/soak_test.py 5 --interval 0.5   # quick 5-min run

Optional dependency (memory monitoring):
    venv/bin/pip install psutil
"""
import sys
import os
import signal
import subprocess
import time
import random
import threading
import logging
import argparse

# ── Hardware mocks ─────────────────────────────────────────────────────────────
from unittest.mock import MagicMock

_lgpio = MagicMock()
_lgpio.SET_PULL_UP = 0
_lgpio.gpiochip_open.return_value = 99
# Plain function — NOT a MagicMock — so it doesn't record 1,100 calls/sec into
# call_args_list.  MagicMock memory grows to GB-scale after ~1 hour and causes
# OS swap pressure, which manifests as multi-second stalls on the dispatch thread.
_lgpio.gpio_read = lambda chip, pin: 1

_pygame = MagicMock()
_pygame.FULLSCREEN = 1; _pygame.NOFRAME = 0
_pygame.QUIT = 256; _pygame.KEYDOWN = 768; _pygame.K_ESCAPE = 27
_pygame.USEREVENT = 24
_info = MagicMock(); _info.current_w = 1920; _info.current_h = 1080
_pygame.display.Info.return_value = _info
_pygame.display.set_mode.return_value = MagicMock()
_pygame.event.get.return_value = []
_pygame.mixer.Channel.return_value = MagicMock()
_pygame.mixer.Sound.return_value = MagicMock()
sys.modules['lgpio']  = _lgpio
sys.modules['pygame'] = _pygame

# Kill any previously frozen soak_test instance, excluding this process.
_my_pid = os.getpid()
_pgrep = subprocess.run(["pgrep", "-f", "soak_test.py"], capture_output=True, text=True)
for _pid_str in _pgrep.stdout.split():
    try:
        _pid = int(_pid_str)
        if _pid != _my_pid:
            os.kill(_pid, signal.SIGTERM)
    except (ValueError, ProcessLookupError):
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from microrave import MicroRaveApp, State  # noqa: E402

try:
    import psutil
    _proc = psutil.Process()
    def mem_kb(): return _proc.memory_info().rss // 1024
except ImportError:
    psutil = None
    def mem_kb(): return 0


def run(duration_minutes: float = 60.0, event_interval_s: float = 2.0):
    duration_s     = duration_minutes * 60
    expected_events = int(duration_s / event_interval_s)
    report_every_s  = max(60.0, duration_s / 10)

    print(f"\n{'=' * 65}")
    print(f"  MicroRave Soak Test")
    print(f"  Duration:  {duration_minutes:.0f} min  ({duration_s:.0f}s)")
    print(f"  Interval:  {event_interval_s}s/event  (~{expected_events:,} events total)")
    print(f"  Reporting: every {report_every_s:.0f}s")
    if psutil is None:
        print("  Memory:    psutil not installed — skipped")
    print(f"{'=' * 65}\n")

    # ── Error counting ────────────────────────────────────────────────────────
    error_count = [0]
    class _Counter(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                error_count[0] += 1
    logging.getLogger("MicroRave").addHandler(_Counter())

    app = MicroRaveApp()
    app._drain()

    mem_baseline   = mem_kb()
    thread_baseline = threading.active_count()
    t0             = time.monotonic()
    event_count    = 0
    mem_samples    = []
    last_report    = t0

    print(f"  Started:  {time.strftime('%H:%M:%S')}")
    print(f"  Baseline: {mem_baseline:,} KB   Threads: {thread_baseline}\n")

    # Weighted pool — skewed toward normal usage, avoids easter eggs
    def _event_pool(app):
        return [
            (app._on_digit,    [0]),
            (app._on_digit,    [1]),
            (app._on_digit,    [2]),
            (app._on_digit,    [3]),
            (app._on_digit,    [0]),   # extra weight toward short times
            (app._on_digit,    [5]),
            (app._on_start,    []),
            (app._on_start,    []),    # extra weight — most common action
            (app._on_stop,     []),
            (app._on_add_30,   []),
            (app._on_add_30,   []),
            (app._on_door_open,  []),
            (app._on_door_close, []),
            (app._on_dj,       [1]),
            (app._on_dj,       [2]),
            (app._on_dj,       [3]),
        ]

    pool = _event_pool(app)

    try:
        while time.monotonic() - t0 < duration_s:
            fn, args = random.choice(pool)
            app._post(fn, *args)
            event_count += 1

            time.sleep(event_interval_s)

            now = time.monotonic()
            if now - last_report >= report_every_s:
                elapsed = now - t0
                mem     = mem_kb()
                mem_samples.append(mem)
                threads  = threading.active_count()
                remaining = max(0, duration_s - elapsed)
                print(f"  [{elapsed/60:5.1f}m]  "
                      f"Events: {event_count:>5,}  "
                      f"State: {app._state.name:<15}  "
                      f"Mem: {mem:,} KB (Δ{mem-mem_baseline:+,})  "
                      f"Threads: {threads}  "
                      f"Errors: {error_count[0]}  "
                      f"Left: {remaining/60:.1f}m")
                last_report = now

    except KeyboardInterrupt:
        print("\n  [Interrupted by user — reporting partial results]")

    elapsed    = time.monotonic() - t0
    mem_end    = mem_kb()
    thread_end = threading.active_count()

    app._shutdown()

    # ── Final report ─────────────────────────────────────────────────────────
    print(f"\n{'─' * 65}")
    print(f"  Soak Test Results  ({elapsed/60:.1f} min elapsed)")
    print(f"  Events fired:     {event_count:,}")
    print(f"  Dispatch errors:  {error_count[0]}")
    print(f"  Final state:      {app._state.name}")
    if mem_samples:
        peak = max(mem_samples)
        print(f"  Memory baseline:  {mem_baseline:,} KB")
        print(f"  Memory peak:      {peak:,} KB  (Δ{peak - mem_baseline:+,} KB)")
        print(f"  Memory at end:    {mem_end:,} KB  (Δ{mem_end - mem_baseline:+,} KB)")
    else:
        print(f"  Memory:           psutil not installed")
    print(f"  Threads baseline: {thread_baseline}  →  {thread_end} at end  (Δ{thread_end-thread_baseline:+})")
    print()

    ok = True
    if error_count[0] == 0:
        print("  ✓  No dispatch errors")
    else:
        print(f"  ✗  {error_count[0]} errors logged"); ok = False

    if mem_samples and psutil:
        leak_threshold = mem_baseline + 20_000
        if max(mem_samples) > leak_threshold:
            print(f"  ⚠  Peak memory exceeded baseline by >20 MB"); ok = False
        else:
            print(f"  ✓  Memory stable")
    else:
        print("  –  Memory check skipped (install psutil)")

    if thread_end > thread_baseline + 5:
        print(f"  ⚠  Thread count grew by {thread_end - thread_baseline}"); ok = False
    else:
        print(f"  ✓  Thread count stable")

    print()
    return ok


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="MicroRave long-running soak test")
    p.add_argument("duration", nargs="?", type=float, default=60.0,
                   help="Duration in minutes (default: 60)")
    p.add_argument("--interval", type=float, default=2.0,
                   help="Seconds between events (default: 2.0)")
    args = p.parse_args()
    success = run(args.duration, args.interval)
    sys.exit(0 if success else 1)
