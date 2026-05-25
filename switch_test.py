#!/usr/bin/env python3
"""
switch_test.py — Real-time SPDT switch monitor for Raspberry Pi 5

Wiring (per switch):
  Common (C) → GND rail
  NO         → GPIO pin listed below
  NC         → leave unconnected

Logic:  switch OPEN = HIGH (1),  switch CLOSED = LOW (0)
Library: lgpio (Pi 5 native — RPi.GPIO does NOT work on Pi 5)
Display: curses (live terminal grid, no scrolling)

Run with:  python3 switch_test.py
Quit with: Ctrl+C  or  q
"""

import lgpio
import curses
import time
import sys

# ── GPIO Pin Assignments (BCM numbering) ──────────────────────────────────────
# Adafruit Perma-Proto Pi HAT — all pins land on labeled pads.
# Requires SPI disabled (raspi-config) to free GPIO 8 (CE0) and GPIO 10 (MOSI).
SWITCHES = [
    {"id":  1, "gpio":  4},   # DJ1   → HAT #4
    {"id":  2, "gpio":  5},   # DJ2   → HAT #5
    {"id":  3, "gpio":  6},   # DJ3   → HAT #6
    {"id":  4, "gpio": 10},   # DJ4   → HAT MOSI  (was GPIO 26 — not on HAT)
    {"id":  5, "gpio": 27},   # DJ5   → HAT #27
    {"id":  6, "gpio":  9},   # DJ6   → HAT MISO
    {"id":  7, "gpio":  2},   # Start → HAT SDA
    {"id":  8, "gpio": 11},   # Stop  → HAT CLK
    {"id":  9, "gpio": 12},   # +30s  → HAT #12
    {"id": 10, "gpio": 13},   # 1     → HAT #13
    {"id": 11, "gpio": 16},   # 2     → HAT #16
    {"id": 12, "gpio": 17},   # 3     → HAT #17
    {"id": 13, "gpio": 18},   # 4     → HAT #18
    {"id": 14, "gpio": 19},   # 5     → HAT #19
    {"id": 15, "gpio": 20},   # 6     → HAT #20
    {"id": 16, "gpio": 21},   # 7     → HAT #21
    {"id": 17, "gpio": 22},   # 8     → HAT #22
    {"id": 18, "gpio": 23},   # 9     → HAT #23
    {"id": 19, "gpio": 24},   # 0     → HAT #24
    {"id": 20, "gpio": 25},   # Door  → HAT #25
    {"id": 21, "gpio":  3},   # VolUp → HAT SCL
    {"id": 22, "gpio":  8},   # VolDn → HAT CE0   (was GPIO 0 — ID_SD, reserved)
]

POLL_HZ = 20  # reads per second


# ── GPIO Setup ────────────────────────────────────────────────────────────────

def setup_gpio():
    """Open lgpio chip handle and configure all pins as inputs with pull-ups."""
    h = lgpio.gpiochip_open(0)  # gpiochip0 = the main Pi 5 GPIO bank
    for sw in SWITCHES:
        # Pull-up means floating pin reads HIGH (open); shorting to GND reads LOW (closed)
        lgpio.gpio_claim_input(h, sw["gpio"], lgpio.SET_PULL_UP)
    return h


def read_states(h):
    """Return list of (id, gpio, is_closed) tuples for all switches."""
    results = []
    for sw in SWITCHES:
        val = lgpio.gpio_read(h, sw["gpio"])
        closed = (val == 0)  # LOW = NO contact shorted to GND via Common
        results.append((sw["id"], sw["gpio"], closed))
    return results


# ── Display ───────────────────────────────────────────────────────────────────

def draw_grid(stdscr, states):
    """Render the live switch status grid."""
    stdscr.erase()

    # Color pairs
    curses.init_pair(1, curses.COLOR_GREEN, curses.COLOR_BLACK)   # closed
    curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)   # open (dim)

    # Header
    stdscr.addstr(0, 0, "SWITCH MONITOR  —  Raspberry Pi 5", curses.A_BOLD)
    stdscr.addstr(1, 0, "GREEN = CLOSED    dim = OPEN    Ctrl+C or Q to quit",
                  curses.A_DIM)
    stdscr.addstr(2, 0, "─" * 52)

    # Two-column grid: switches 1–11 left, 12–22 right
    ROW_START = 4
    COL_WIDTH = 26
    PER_COL = 11

    for sw_id, gpio, closed in states:
        col_idx = (sw_id - 1) // PER_COL     # 0 = left column, 1 = right column
        row_idx = (sw_id - 1) % PER_COL      # 0–10 within the column

        row = ROW_START + row_idx
        col = col_idx * COL_WIDTH

        label  = f"SW {sw_id:02d}  GPIO {gpio:02d}"
        status = "● CLOSED" if closed else "○ open  "

        attr = (curses.color_pair(1) | curses.A_BOLD) if closed \
               else (curses.color_pair(2) | curses.A_DIM)

        stdscr.addstr(row, col, f"{label}   {status}", attr)

    # Footer
    footer_row = ROW_START + PER_COL + 1
    stdscr.addstr(footer_row,     0, "─" * 52)
    stdscr.addstr(footer_row + 1, 0,
                  f"Last read: {time.strftime('%H:%M:%S')}   ({POLL_HZ} Hz)",
                  curses.A_DIM)

    stdscr.refresh()


# ── Main Loop ─────────────────────────────────────────────────────────────────

def main(stdscr):
    curses.curs_set(0)      # hide blinking cursor
    stdscr.nodelay(True)    # getch() won't block the poll loop

    h = setup_gpio()

    try:
        while True:
            states = read_states(h)
            draw_grid(stdscr, states)
            time.sleep(1 / POLL_HZ)

            key = stdscr.getch()
            if key in (ord('q'), ord('Q')):
                break

    finally:
        lgpio.gpiochip_close(h)  # always release GPIO on exit


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass  # Ctrl+C is a clean exit
    finally:
        print("\nGPIO released. Goodbye.")
