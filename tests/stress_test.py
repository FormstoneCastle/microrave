#!/usr/bin/env python3
"""
MicroRave Stress Test
=====================
Fires synthetic events at the dispatch queue as fast as possible, then reports
throughput, queue depth, memory growth, and thread count.

Run from the MicroRave directory:
    venv/bin/python tests/stress_test.py

Optional dependency (memory monitoring):
    venv/bin/pip install psutil
"""
import sys
import os
import time
import threading
import logging

# ── Hardware mocks (must come before any microrave import) ────────────────────
from unittest.mock import MagicMock

_lgpio = MagicMock()
_lgpio.SET_PULL_UP = 0
_lgpio.gpiochip_open.return_value = 99
_lgpio.gpio_read = lambda chip, pin: 1  # plain function — avoids MagicMock call recording overhead

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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from microrave import MicroRaveApp, State  # noqa: E402  (import after mock setup)

try:
    import psutil
    _proc = psutil.Process()
    def mem_kb(): return _proc.memory_info().rss // 1024
except ImportError:
    psutil = None
    def mem_kb(): return 0
    print("  (psutil not installed — install with: venv/bin/pip install psutil)")


def run(event_count: int = 5_000, burst_size: int = 200):
    print(f"\n{'=' * 60}")
    print(f"  MicroRave Stress Test — {event_count:,} events  burst={burst_size}")
    print(f"{'=' * 60}\n")

    mem_start    = mem_kb()
    thread_start = threading.active_count()

    # Count dispatch errors via a log handler
    error_count = [0]
    class _Counter(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR:
                error_count[0] += 1
    logging.getLogger("MicroRave").addHandler(_Counter())

    app = MicroRaveApp()
    app._drain()

    print(f"  Baseline — Mem: {mem_start:,} KB   Threads: {thread_start}")
    print(f"  Firing {event_count:,} events in bursts of {burst_size}...\n")

    # Sequence: exercises every transition in a 20-event cycle, avoids easter eggs
    def _cycle(i):
        c = i % 20
        if   c < 4:  return (app._on_digit,   [c + 1])
        elif c == 4: return (app._on_start,    [])
        elif c < 8:  return (app._on_add_30,   [])
        elif c == 8: return (app._on_door_open, [])
        elif c == 9: return (app._on_door_close,[])
        elif c < 14: return (app._on_digit,    [c - 9])
        elif c == 14:return (app._on_dj,       [(i % 6) + 1])
        else:        return (app._on_stop,     [])

    max_q = 0
    t0    = time.monotonic()

    for i in range(event_count):
        fn, args = _cycle(i)
        app._post(fn, *args)
        q = app._q.qsize()
        if q > max_q:
            max_q = q

        if (i + 1) % burst_size == 0:
            app._drain()

        if (i + 1) % (burst_size * 10) == 0:
            pct = (i + 1) / event_count * 100
            print(f"    {i+1:>5,}/{event_count:,}  ({pct:4.0f}%)  "
                  f"Q-peak: {max_q:3}  "
                  f"Mem: {mem_kb():,} KB  "
                  f"Threads: {threading.active_count()}  "
                  f"Errors: {error_count[0]}")

    app._drain()
    elapsed = time.monotonic() - t0

    mem_end    = mem_kb()
    thread_end = threading.active_count()

    app._shutdown()

    # ── Report ──────────────────────────────────────────────────────────────
    mem_delta    = mem_end    - mem_start
    thread_delta = thread_end - thread_start

    print(f"\n{'─' * 60}")
    print(f"  Events fired:      {event_count:,}")
    print(f"  Elapsed:           {elapsed:.2f}s")
    print(f"  Throughput:        {event_count / elapsed:,.0f} events/sec")
    print(f"  Max queue depth:   {max_q}")
    print(f"  Dispatch errors:   {error_count[0]}")
    print(f"  Memory  start/end: {mem_start:,} / {mem_end:,} KB  (Δ{mem_delta:+,} KB)")
    print(f"  Threads start/end: {thread_start} / {thread_end}  (Δ{thread_delta:+})")
    print(f"  Final state:       {app._state.name}")
    print()

    ok = True
    if error_count[0] == 0:
        print("  ✓  No dispatch errors")
    else:
        print(f"  ✗  {error_count[0]} dispatch errors"); ok = False

    if psutil is None:
        print("  –  Memory skipped (psutil not installed)")
    elif mem_delta > 100_000:
        print(f"  ⚠  Memory grew {mem_delta:+,} KB — possible leak"); ok = False
    else:
        print(f"  ✓  Memory growth {mem_delta:+,} KB — OK")

    if thread_delta > 4:
        print(f"  ⚠  Thread count +{thread_delta} — possible leak"); ok = False
    else:
        print(f"  ✓  Thread growth {thread_delta:+} — OK")

    print()
    return ok


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="MicroRave stress test")
    p.add_argument("events",  nargs="?", type=int,   default=5_000, help="Event count (default 5000)")
    p.add_argument("--burst", type=int,   default=200,  help="Drain queue every N events (default 200)")
    args = p.parse_args()
    success = run(args.events, args.burst)
    sys.exit(0 if success else 1)
