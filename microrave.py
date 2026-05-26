"""
MicroRave Music Player  —  v3.2
================================
Microwave-shell music player on Raspberry Pi 5.

Hardware:
  22-switch SPDT panel (20 wired now, Vol Up/Down future)
  HDMI display — fullscreen virtual 7-segment clock face via pygame
  HDMI audio → TV speakers

Behavior: microwave-oven UX
  Idle          Shows 12-hour clock
  Digit press   Enters countdown time (shifts in from right)
  Start         Begins countdown + music (door must be closed)
  Close door    Starts or resumes countdown
  Open door     Pauses countdown and music
  +30s          Adds 30 seconds at any time
  Stop/Clear    Cancels everything, returns to clock
  DJ1–DJ6       Selects music playlist
  Vol Up/Down   Adjusts volume (pins reserved, buttons not yet wired)

Easter eggs: special digit sequences trigger a 3-second race-around animation,
  then play a one-shot clip from sounds/easter/<key>/ before counting down.

Run from desktop terminal:
  sudo venv/bin/python microrave.py

Run headless (no desktop session):
  sudo SDL_VIDEODRIVER=kmsdrm venv/bin/python microrave.py
"""

import logging
import os
import queue
import random
import subprocess
import threading
import time
from datetime import datetime
from enum import Enum, auto

import pygame

from adapters_hw import (
    Display,
    AudioEngine,
    RelayController,
    _race_animation,
    BEEP_SOUND,
    DING_SOUND,
)

# IO mode selection.  "hardware" = real GPIO + HDMI + speakers + Arduino relay.
# "net" = browser-based simulator (Step 3 work; not yet implemented).
MICRORAVE_IO = os.environ.get("MICRORAVE_IO", "hardware")


def _get_net_module():
    """Lazy import of adapters_net so hardware mode never touches aiohttp/etc."""
    import adapters_net
    return adapters_net


def make_input_adapter():
    """Factory for the input adapter, selected by MICRORAVE_IO env var."""
    if MICRORAVE_IO == "hardware":
        from adapters_hw import HardwareInput
        return HardwareInput()
    if MICRORAVE_IO == "net":
        return _get_net_module().NetInput()
    raise ValueError(f"Unknown MICRORAVE_IO: {MICRORAVE_IO!r}")


def make_display():
    """Factory for the display adapter, selected by MICRORAVE_IO env var."""
    if MICRORAVE_IO == "hardware":
        return Display()
    if MICRORAVE_IO == "net":
        return _get_net_module().NetDisplay()
    raise ValueError(f"Unknown MICRORAVE_IO: {MICRORAVE_IO!r}")


def make_audio():
    """Factory for the audio adapter, selected by MICRORAVE_IO env var."""
    if MICRORAVE_IO == "hardware":
        return AudioEngine()
    if MICRORAVE_IO == "net":
        return _get_net_module().NetAudio()
    raise ValueError(f"Unknown MICRORAVE_IO: {MICRORAVE_IO!r}")


def make_relays():
    """Factory for the DJ-light relay adapter, selected by MICRORAVE_IO env var."""
    if MICRORAVE_IO == "hardware":
        return RelayController()
    if MICRORAVE_IO == "net":
        return _get_net_module().NetLights()
    raise ValueError(f"Unknown MICRORAVE_IO: {MICRORAVE_IO!r}")

# =============================================================================
# LOGGING
# =============================================================================

_temp_cache: dict = {"val": "?°C", "ts": 0.0}

def _read_temp() -> str:
    now = time.monotonic()
    if now - _temp_cache["ts"] >= 30.0:
        try:
            out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
            # "temp=52.3'C" → "52.3°C"
            _temp_cache["val"] = out.strip().replace("temp=", "").replace("'C", "°C")
        except Exception:
            _temp_cache["val"] = "?°C"
        _temp_cache["ts"] = now
    return _temp_cache["val"]

class _TempFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.temp = _read_temp()
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s [%(temp)s]: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("microrave.log"),
    ],
)
_temp_filter = _TempFilter()
for _h in logging.getLogger().handlers:
    _h.addFilter(_temp_filter)

log = logging.getLogger("MicroRave")

# =============================================================================
# GPIO PIN ASSIGNMENTS  (BCM numbering)
# =============================================================================

# Row 1–2: DJ playlist selectors
# DJ4 moved 26 → 10 (MOSI) so it lands on a labeled HAT pad. Requires SPI disabled.
PIN_DJ = {1: 4, 2: 5, 3: 6, 4: 10, 5: 27, 6: 9}

# Row 3: control buttons
PIN_START  = 2
PIN_STOP   = 11
PIN_ADD_30 = 12

# Rows 4–7: digit buttons
PIN_DIGITS = {
    1: 13, 2: 16, 3: 17,
    4: 18, 5: 19, 6: 20,
    7: 21, 8: 22, 9: 23,
    0: 24,
}

# Row 7: volume
# Vol Down moved 0 → 8 (CE0); GPIO 0 is ID_SD (HAT EEPROM, reserved). Requires SPI disabled.
PIN_VOL_UP   = 3
PIN_VOL_DOWN = 8

# Row 8: door switch
PIN_DOOR = 25

# =============================================================================
# SETTINGS
# =============================================================================

MUSIC_ROOT        = "music"
SOUNDS_DIR        = "sounds"
EASTER_EGG_DIR    = os.path.join(SOUNDS_DIR, "easter")
# RELAY_PORT/BAUD, PLAYCOUNTS_FILE, BEEP_SOUND, DING_SOUND, VOLUME_DEFAULT,
# VOLUME_STEP live in adapters_hw.py (they travel with the classes that own them).

# Hidden recovery code: typing this digit sequence anywhere on the keypad
# runs /usr/local/bin/microrave-wifi-restore-defaults — which tears down AP
# mode and re-enables client autoconnect on home/laptop/phone profiles.
# Use this when you want the Pi to rejoin a known WiFi network (e.g. at home).
APMODE_CODE   = "4108617369"
APMODE_SCRIPT = "/usr/local/bin/microrave-wifi-restore-defaults"

# Hidden tally-reset code: typing this anywhere on the keypad clears
# playcounts.json (the per-track play tally). Useful between events when
# starting a fresh count. Both hidden codes are 10 digits — _code_buf below
# is sized accordingly.
RESET_TALLY_CODE = "4103744720"
DOOR_OPEN_TIMEOUT   = 60    # seconds before auto-cancel when door left open; 0 = disabled
ENTRY_IDLE_TIMEOUT  = 60    # seconds on 0000 screen with no input before returning to clock
# DEBOUNCE_S and GPIO_POLL_HZ live in adapters_hw.py (with HardwareInput).

# DJ race animation (runs after each session to pick a random DJ)
RANDOM_DJ_ON_FINISH = True   # set False to disable random DJ selection
DJ_RACE_SEQUENCE    = [1, 2, 3, 6, 5, 4]  # clockwise around the 2×3 light grid
DJ_RACE_LAPS        = 3      # full laps before landing
DJ_RACE_STEP_S      = 0.12   # seconds per step (random-DJ race after sessions)

# Easter egg DJ-light chase: own slower step so each LED is clearly visible and
# the Arduino isn't getting hammered with serial commands for the entire egg.
EGG_CHASE_STEP_S    = 0.25   # seconds per step (cw/ccw chase during egg playback)

# Easter egg trigger map
# key = digit string as typed; seconds/mm/ss define the countdown
# 6767 overrides to 0:67 (67 sec) — the fun is in entering it
EASTER_EGGS: dict[str, dict] = {
    "007":  {"folder": "007",  "seconds": 102,  "mm": 1,  "ss": 42},
    "42":   {"folder": "042",  "seconds": 42,   "mm": 0,  "ss": 42,  "auto_duration": True},
    "69":   {"folder": "069",  "seconds": 69,   "mm": 0,  "ss": 69},
    "069":  {"folder": "069",  "seconds": 69,   "mm": 0,  "ss": 69},
    "0069": {"folder": "069",  "seconds": 69,   "mm": 0,  "ss": 69},
    "420":  {"folder": "420",  "seconds": 260,  "mm": 4,  "ss": 20},
    "666":  {"folder": "666",  "seconds": 426,  "mm": 6,  "ss": 66},
    "67":   {"folder": "067",  "seconds": 67,   "mm": 0,  "ss": 67},
    "6767": {"folder": "6767", "seconds": 67,   "mm": 0,  "ss": 67},
    "8008": {"folder": "8008", "seconds": 4808, "mm": 80, "ss": 8},
    "911":  {"folder": "911",  "seconds": 551,  "mm": 9,  "ss": 11},
    "923":  {"folder": "923",  "seconds": 563,  "mm": 9,  "ss": 23},
    "7734": {"folder": "7734", "seconds": 4654, "mm": 77, "ss": 34},
}

# Display colors/geometry, CHAR_SEGS, and RACE_DURATION/RACE_STEP_S live in
# adapters_hw.py with the Display class and _race_animation helper.


# =============================================================================
# PLAYLIST MANAGER
# =============================================================================

class PlaylistManager:
    _EXTS = ('.mp3', '.wav', '.ogg', '.flac', '.m4a')

    def __init__(self):
        self._lists: dict[int, list] = {}
        for n in range(1, 7):
            folder = os.path.join(MUSIC_ROOT, f"dj{n}")
            if os.path.isdir(folder):
                tracks = sorted(
                    os.path.join(folder, f)
                    for f in os.listdir(folder)
                    if f.lower().endswith(self._EXTS)
                )
                self._lists[n] = tracks
                log.info("DJ%d: %d track(s)", n, len(tracks))
            else:
                self._lists[n] = []
                log.warning("DJ%d folder not found: %s", n, folder)
        self._sel = 1
        # Bag shuffle state: each DJ has its own queue. Bags survive across
        # sessions, so short countdowns rotate through the whole folder before
        # any track repeats.
        self._bags: dict[int, list] = {n: [] for n in range(1, 7)}
        self._last_track: dict[int, str | None] = {n: None for n in range(1, 7)}

    def select(self, dj: int):
        if 1 <= dj <= 6:
            self._sel = dj
            log.info("Selected DJ%d (%d tracks)", dj, len(self._lists[dj]))

    @property
    def selected(self) -> int:
        return self._sel

    def next_track(self) -> str | None:
        """Bag-shuffle the next track for the currently-selected DJ.
        Each track plays once per bag before any repeat. When the bag is
        empty it's refilled with a fresh shuffle, and we swap positions if
        the new first track would be the same as the just-played one
        (prevents back-to-back duplicates at bag boundaries)."""
        dj = self._sel
        tracks = self._lists.get(dj, [])
        if not tracks:
            return None
        if not self._bags[dj]:
            new_bag = list(tracks)
            random.shuffle(new_bag)
            if len(new_bag) > 1 and new_bag[0] == self._last_track[dj]:
                new_bag[0], new_bag[1] = new_bag[1], new_bag[0]
            self._bags[dj] = new_bag
            log.info("DJ%d bag refilled (%d tracks)", dj, len(new_bag))
        track = self._bags[dj].pop(0)
        self._last_track[dj] = track
        log.info("DJ%d next: %s (%d left in bag)",
                 dj, os.path.basename(track), len(self._bags[dj]))
        return track

    def shuffled_tracks(self) -> list:
        """Legacy: one-shot uniform shuffle of the current DJ's tracks.
        Kept for backwards compatibility — superseded by next_track()."""
        tracks = list(self._lists.get(self._sel, []))
        random.shuffle(tracks)
        return tracks

    def easter_tracks(self, key: str) -> list:
        """Return audio tracks for easter egg key, or [] if folder missing/empty."""
        egg = EASTER_EGGS.get(key)
        if not egg:
            return []
        folder = os.path.join(EASTER_EGG_DIR, egg["folder"])
        if not os.path.isdir(folder):
            log.info("Easter egg '%s': folder not found — skipping.", key)
            return []
        tracks = sorted(
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if f.lower().endswith(self._EXTS)
        )
        if not tracks:
            log.info("Easter egg '%s': folder empty — skipping.", key)
        return tracks


# =============================================================================
# COUNTDOWN TIMER
# =============================================================================

class CountdownTimer:
    """
    Accurate 1-second countdown.
    on_tick(remaining) fires every second.
    on_finish() fires when remaining reaches zero.
    Both callbacks come from the timer thread — callers should post to a queue.
    """

    def __init__(self, on_tick, on_finish):
        self._on_tick   = on_tick
        self._on_finish = on_finish
        self._remaining = 0
        self._lock      = threading.Lock()
        self._stop      = threading.Event()
        self._pause     = threading.Event()
        self._pause.set()
        self._thread: threading.Thread | None = None

    def start(self, seconds: int):
        self.stop()
        with self._lock:
            self._remaining = max(0, seconds)
        self._stop.clear()
        self._pause.set()
        self._thread = threading.Thread(target=self._run, name="Countdown", daemon=True)
        self._thread.start()

    def pause(self):
        self._pause.clear()

    def resume(self):
        self._pause.set()

    def stop(self):
        self._stop.set()
        self._pause.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def add(self, n: int):
        with self._lock:
            self._remaining = max(0, self._remaining + n)
        log.info("Timer +%ds → %d remaining", n, self._remaining)

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining

    def _run(self):
        while not self._stop.is_set():
            self._pause.wait()
            if self._stop.is_set():
                break
            with self._lock:
                r = self._remaining
            self._on_tick(r)
            if r <= 0:
                self._on_finish()
                return
            self._stop.wait(timeout=1.0)
            if not self._stop.is_set():
                with self._lock:
                    self._remaining = max(0, self._remaining - 1)


# =============================================================================
# TIME ENTRY BUFFER
# =============================================================================

class TimeEntryBuffer:
    """
    Accumulates digit presses into MM:SS time.
    Digits shift in from the right, exactly like a real microwave keypad.
    Rejects entries that would exceed 99:59.
    Tracks the raw typed sequence for easter egg detection.
    """

    # Cap allows entry up to 99:99 (6039s = 100m 39s). The SS field accepts
    # 0–99 so users can mash 9s and see the display reflect it; _fmt_countdown
    # handles the SS>59 overflow during the first ~100 ticks, then falls
    # through to normal MM:SS once the overflow drains.
    _MAX = 99 * 60 + 99

    def __init__(self):
        self._d         = [0, 0, 0, 0]
        self._typed: list[int] = []   # digits as actually pressed (up to 4)
        self._from_add30 = False      # True when buffer was last set by +30 (not manual digits)

    def push(self, digit: int):
        # First digit 1-9 means whole minutes (1 -> 1:00, 5 -> 5:00).
        # Once a second digit lands, fall back to shift-from-right
        # (e.g. 1+2 -> 0:12, 1+9+9 -> 1:99).
        if not self._typed and 1 <= digit <= 9:
            c = [0, digit, 0, 0]
        else:
            c = self._d[1:] + [digit]
        if (c[0] * 10 + c[1]) * 60 + (c[2] * 10 + c[3]) <= self._MAX:
            self._d          = c
            self._typed      = (self._typed + [digit])[-4:]
            self._from_add30 = False

    def clear(self):
        self._d          = [0, 0, 0, 0]
        self._typed      = []
        self._from_add30 = False

    def to_seconds(self) -> int:
        return (self._d[0] * 10 + self._d[1]) * 60 + (self._d[2] * 10 + self._d[3])

    def is_zero(self) -> bool:
        return self.to_seconds() == 0

    def raw_mm(self) -> int:
        return self._d[0] * 10 + self._d[1]

    def raw_ss(self) -> int:
        return self._d[2] * 10 + self._d[3]

    def typed_str(self) -> str:
        """Digits as actually pressed — used for easter egg matching."""
        return ''.join(str(d) for d in self._typed)

    def set_from_seconds(self, secs: int):
        """Overwrite buffer from a seconds value — used by +30 when already armed."""
        secs = max(0, min(secs, self._MAX))
        m, s = divmod(secs, 60)
        self._d          = [m // 10, m % 10, s // 10, s % 10]
        self._typed      = []
        self._from_add30 = True

    def display_str(self) -> str:
        """4-char string for the display (raw digits, no normalization)."""
        return "%d%d%d%d" % tuple(self._d)


# =============================================================================
# APPLICATION STATE
# =============================================================================

class State(Enum):
    IDLE          = auto()
    ENTERING_TIME = auto()
    ANIMATING     = auto()   # easter egg flash + race animation running
    COUNTING_DOWN = auto()
    PAUSED        = auto()
    FINISHED      = auto()


# Sentinel posted to the dispatch queue to signal a clean shutdown
_STOP_SENTINEL = object()


# =============================================================================
# APPLICATION
# =============================================================================

class MicroRaveApp:
    """
    All state changes run on a single dispatch thread (via a SimpleQueue).
    GPIO callbacks and timer callbacks post work items to the queue.
    Display rendering and the pygame event pump run on the main thread.
    """

    def __init__(self):
        self._state       = State.IDLE
        self._q           = queue.SimpleQueue()
        self._door_closed = True
        self._door_timer:  threading.Timer | None = None
        self._entry_timer: threading.Timer | None = None
        self._last_clock: tuple | None = None

        # Countdown display metadata — set when a countdown starts
        self._cd_mm = 0   # original minutes entered (for display formatting)

        # Easter egg state
        self._race_abort    = threading.Event()

        # DJ race state
        self._dj_race_abort = threading.Event()

        # Easter egg DJ-light chase (dancing lights during egg playback)
        self._egg_chase_abort = threading.Event()

        self._dispatch_thread = threading.Thread(target=self._dispatch, name="Dispatch", daemon=True)
        self._dispatch_thread.start()

        pygame.init()
        self.display   = make_display()
        self.playlists = PlaylistManager()
        self.audio     = make_audio()
        self.timer     = CountdownTimer(
            on_tick   = lambda r: self._post(self._on_tick,   r),
            on_finish = lambda:   self._post(self._on_finish),
        )
        self.buf = TimeEntryBuffer()
        self._code_buf = ""    # sliding window of last 10 digits for hidden-code detection

        self.relays = make_relays()
        self._setup_gpio()
        self.relays.set_dj(self.playlists.selected)  # light up default DJ on startup
        self._show_clock(force=True)
        log.info("MicroRave ready — DJ%d selected", self.playlists.selected)

    # -------------------------------------------------------------------------
    # Dispatch queue
    # -------------------------------------------------------------------------

    def _post(self, fn, *args):
        self._q.put((fn, args))

    def _dispatch(self):
        while True:
            item = self._q.get()
            if item is _STOP_SENTINEL:
                break
            fn, args = item
            try:
                fn(*args)
            except Exception as exc:
                log.error("Dispatch error in %s: %s", fn.__name__, exc, exc_info=True)

    def _drain(self, timeout: float = 1.0) -> bool:
        """Block until all currently-queued dispatch items are processed. Used in tests."""
        done = threading.Event()
        self._q.put((done.set, ()))
        return done.wait(timeout=timeout)

    # -------------------------------------------------------------------------
    # Input setup  (delegates to the selected input adapter)
    # -------------------------------------------------------------------------

    def _setup_gpio(self):
        """Wire every button + door pin through the input adapter.
        The adapter (HardwareInput in hardware mode) owns chip/poll/debounce state;
        we just give it the pin→handler bindings."""
        self.input = make_input_adapter()

        # Button handlers — closures bind dj/digit/fn values explicitly so the
        # poll thread fires the right action for each pin.
        for dj, pin in PIN_DJ.items():
            self.input.register_button(pin, lambda d=dj: self._post(self._on_dj, d))

        for digit, pin in PIN_DIGITS.items():
            self.input.register_button(pin, lambda d=digit: self._post(self._on_digit, d))

        for pin, fn in [
            (PIN_START,    self._on_start),
            (PIN_STOP,     self._on_stop),
            (PIN_ADD_30,   self._on_add_30),
            (PIN_VOL_UP,   self._on_vol_up),
            (PIN_VOL_DOWN, self._on_vol_down),
        ]:
            self.input.register_button(pin, lambda f=fn: self._post(f))

        self.input.register_door(
            PIN_DOOR,
            on_close=lambda: self._post(self._on_door_close),
            on_open =lambda: self._post(self._on_door_open),
        )

        self._door_closed = self.input.door_closed_initial()
        log.info("Input ready (mode=%s) — door: %s",
                 MICRORAVE_IO, "closed" if self._door_closed else "open")

        self.input.start()

    # -------------------------------------------------------------------------
    # Event handlers  (all run on the dispatch thread)
    # -------------------------------------------------------------------------

    def _on_dj(self, dj: int):
        log.info("Button: DJ%d", dj)
        self._dj_race_abort.set()  # cancel any running DJ race animation
        self.audio.beep()
        self.playlists.select(dj)
        # Light follows the playing DJ — only move it when no session is in progress
        if self._state not in (State.COUNTING_DOWN, State.PAUSED):
            self.relays.set_dj(dj)
        if self._state in (State.IDLE, State.ENTERING_TIME):
            self.display.show(f"dJ {dj}", colon=False)
            t = threading.Timer(1.5, lambda: self._post(self._restore_display))
            t.daemon = True
            t.start()

    def _on_digit(self, digit: int):
        log.info("Button: %d", digit)
        self.audio.beep()
        # Hidden codes — sliding-window match across all states. Both codes are
        # 10 digits; a single buffer handles both. Order doesn't matter since
        # the strings are unique.
        self._code_buf = (self._code_buf + str(digit))[-10:]
        if self._code_buf == APMODE_CODE:
            self._code_buf = ""
            self._trigger_apmode()
            return
        if self._code_buf == RESET_TALLY_CODE:
            self._code_buf = ""
            self._trigger_reset_tally()
            return
        if self._state in (State.IDLE, State.ENTERING_TIME):
            was_idle = self._state == State.IDLE
            if self.buf._from_add30:
                # Buffer was set by +30 — add digit as seconds (not digit-shift)
                self.buf.set_from_seconds(self.buf.to_seconds() + digit)
            else:
                self.buf.push(digit)
            self._state = State.ENTERING_TIME
            self.display.show(self.buf.display_str())
            if was_idle:
                self._start_entry_timer()
        elif self._state == State.COUNTING_DOWN:
            self.timer.add(digit)

    def _on_start(self):
        log.info("Button: START")
        self.audio.beep()
        if self._state == State.PAUSED:
            self._resume_countdown()
        elif self._state == State.ENTERING_TIME and not self.buf.is_zero():
            self._begin_countdown()
        elif self._state in (State.IDLE, State.ENTERING_TIME) and self.buf.is_zero():
            # User pressed START without entering a time — prompt them by flashing 0000
            self._state = State.ENTERING_TIME
            self._flash_zero_prompt()
            self._start_entry_timer()

    def _on_stop(self):
        log.info("Button: STOP")
        self.audio.beep()
        self._race_abort.set()   # cancel any running easter egg animation
        self._stop_egg_chase()
        self._cancel_door_timer()
        self.timer.stop()
        self.audio.stop()
        self.buf.clear()
        self._state = State.ENTERING_TIME
        self.display.show("0000")
        self.relays.set_dj(self.playlists.selected)  # restore DJ light (no-op unless easter egg ran)
        log.info("Cleared — ready for input.")
        self._start_entry_timer()

    def _on_door_timeout(self):
        log.info("Door left open — auto-cancelled.")
        self._race_abort.set()
        self._stop_egg_chase()
        self.timer.stop()
        self.audio.stop()
        self.buf.clear()
        self._state = State.IDLE
        self._show_clock(force=True)
        self.relays.set_dj(self.playlists.selected)  # restore DJ light (no-op unless easter egg ran)

    def _on_add_30(self):
        log.info("Button: +30s")
        self.audio.beep()
        if self._state in (State.IDLE, State.ENTERING_TIME):
            self.buf.set_from_seconds(self.buf.to_seconds() + 30)
            self._state = State.ENTERING_TIME
            self.display.show(self.buf.display_str())
            self._begin_countdown()
        elif self._state in (State.COUNTING_DOWN, State.PAUSED):
            self.timer.add(30)

    def _on_vol_up(self):
        log.info("Button: VOL UP")
        self.audio.beep()
        self.audio.volume_up()

    def _on_vol_down(self):
        log.info("Button: VOL DOWN")
        self.audio.beep()
        self.audio.volume_down()

    def _on_door_close(self):
        log.info("Door: CLOSED")
        self._door_closed = True
        self._cancel_door_timer()
        if self._state == State.ENTERING_TIME and not self.buf.is_zero():
            self._begin_countdown()
        elif self._state == State.PAUSED:
            self._resume_countdown()

    def _on_door_open(self):
        log.info("Door: OPEN")
        self._door_closed = False
        if self._state == State.ANIMATING:
            # Timer hasn't started yet — abort animation and return to entry mode
            self._race_abort.set()
            self.audio.stop()
            self.buf.clear()
            self._state = State.ENTERING_TIME
            self.display.show("0000")
        elif self._state == State.COUNTING_DOWN:
            self._pause_countdown()
            if DOOR_OPEN_TIMEOUT > 0:
                self._door_timer = threading.Timer(
                    DOOR_OPEN_TIMEOUT, lambda: self._post(self._on_door_timeout)
                )
                self._door_timer.daemon = True
                self._door_timer.start()
                log.info("Door timeout: auto-cancel in %ds.", DOOR_OPEN_TIMEOUT)

    def _on_tick(self, remaining: int):
        if self._state == State.COUNTING_DOWN:
            self.display.show(self._fmt_countdown(remaining))

    def _on_finish(self):
        log.info("Countdown finished!")
        self._cancel_door_timer()
        self._stop_egg_chase()
        self._state = State.FINISHED
        self.buf.clear()
        self.audio.ding()
        self.display.show("0000")
        self.relays.set_dj(self.playlists.selected)  # restore DJ light (no-op unless easter egg ran)
        t = threading.Timer(3.0, lambda: self._post(self._go_idle))
        t.daemon = True
        t.start()

    def _go_idle(self):
        if self._state == State.FINISHED:
            self._state = State.IDLE
            self._show_clock(force=True)
            if RANDOM_DJ_ON_FINISH:
                others = [dj for dj in range(1, 7) if dj != self.playlists.selected]
                self._start_dj_race(random.choice(others))

    def _start_dj_race(self, target_dj: int) -> None:
        self._dj_race_abort.clear()
        t = threading.Thread(target=self._dj_race_worker, args=(target_dj,), daemon=True)
        t.start()

    def _dj_race_worker(self, target_dj: int) -> None:
        # Animate lights clockwise around the 2×3 grid for DJ_RACE_LAPS full laps,
        # then continue until landing on target_dj.
        seq = DJ_RACE_SEQUENCE
        for _ in range(DJ_RACE_LAPS):
            for dj in seq:
                if self._dj_race_abort.is_set():
                    return
                self.relays.set_dj(dj)
                time.sleep(DJ_RACE_STEP_S)
        for dj in seq:
            if self._dj_race_abort.is_set():
                return
            self.relays.set_dj(dj)
            time.sleep(DJ_RACE_STEP_S)
            if dj == target_dj:
                break
        if not self._dj_race_abort.is_set():
            self.playlists.select(target_dj)
            log.info("Random DJ selected after session: DJ%d", target_dj)
            self._post(self._flash_dj, target_dj)

    def _start_egg_chase(self) -> None:
        """Start the DJ-light chase animation that runs for the whole easter
        egg (animation + audio + countdown). Cycles clockwise, then
        counterclockwise, then repeats — until _stop_egg_chase is called."""
        self._egg_chase_abort.clear()
        t = threading.Thread(target=self._egg_chase_worker, name="EggChase", daemon=True)
        t.start()

    def _egg_chase_worker(self) -> None:
        cw  = DJ_RACE_SEQUENCE
        ccw = list(reversed(DJ_RACE_SEQUENCE))
        clockwise = True
        while not self._egg_chase_abort.is_set():
            seq = cw if clockwise else ccw
            for dj in seq:
                if self._egg_chase_abort.is_set():
                    break
                self.relays.set_dj(dj)
                time.sleep(EGG_CHASE_STEP_S)
            clockwise = not clockwise
        # On exit, restore the selected DJ light so the post-egg state is correct
        self.relays.set_dj(self.playlists.selected)

    def _stop_egg_chase(self) -> None:
        """Signal the chase to stop. Safe to call when no chase is running."""
        self._egg_chase_abort.set()

    def _flash_dj(self, dj: int) -> None:
        self.display.show(f"dJ {dj}", colon=False)
        t = threading.Timer(1.5, lambda: self._post(self._restore_display))
        t.daemon = True
        t.start()

    def _flash_zero_prompt(self) -> None:
        """Flash 0000 three times to prompt the user to enter a time.
        Fired when START is pressed with an empty buffer. Runs in a worker
        thread so the dispatch loop stays responsive. Leaves the display on
        '0000' at the end so the user can immediately start typing."""
        def _flash():
            on_s, off_s = 0.3, 0.2
            for _ in range(3):
                self.display.show("0000")
                time.sleep(on_s)
                self.display.show("    ", colon=False)
                time.sleep(off_s)
            self.display.show("0000")
        threading.Thread(target=_flash, name="ZeroPromptFlash", daemon=True).start()

    # -------------------------------------------------------------------------
    # Easter egg handlers
    # -------------------------------------------------------------------------

    def _begin_easter_egg(self, egg: dict, tracks: list):
        auto_dur = egg.get("auto_duration", False)
        self._state = State.ANIMATING
        self._race_abort.clear()
        self._start_egg_chase()   # DJ lights dance — chase cw/ccw for the duration of the egg

        entry_display = self.buf.display_str()  # capture typed digits before buffer clears
        track = random.choice(tracks)

        # Determine countdown duration — auto_duration eggs measure the actual clip length
        if auto_dur:
            try:
                actual_secs = max(1, round(pygame.mixer.Sound(track).get_length()))
            except Exception:
                actual_secs = egg['seconds']
        else:
            actual_secs = egg['seconds']

        self._cd_mm = actual_secs // 60
        log.info("Easter egg! '%s' — %ds countdown", egg['folder'], actual_secs)

        self.audio.start_easter(
            track,
            on_complete=lambda: self._post(self._on_easter_egg_audio_done)
        )

        brief_display = "%04d" % egg['seconds']  # e.g. "0042" — shown briefly after race

        def _run_animation():
            # Flash the entered number 3 times
            for _ in range(3):
                if self._race_abort.is_set():
                    return
                self.display.show(entry_display)
                time.sleep(0.9)
                if self._race_abort.is_set():
                    return
                self.display.show("    ", colon=False)
                time.sleep(0.43)

            # Race-around animation
            _race_animation(self.display, self._race_abort)

            if not self._race_abort.is_set():
                if auto_dur:
                    # Wink at the number before showing actual duration
                    self.display.show(brief_display)
                    time.sleep(1.5)
                if not self._race_abort.is_set():
                    self._post(self._on_easter_egg_countdown, actual_secs)

        threading.Thread(target=_run_animation, name="EggAnim", daemon=True).start()

    def _on_easter_egg_countdown(self, secs: int):
        """Called after race animation — starts the actual timer."""
        if self._state != State.ANIMATING:
            return
        self._state = State.COUNTING_DOWN
        self.display.show(self._fmt_countdown(secs))
        self.timer.start(secs)

    def _on_easter_egg_audio_done(self):
        """Easter egg clip finished playing — if timer still running, force finish."""
        if self._state == State.COUNTING_DOWN:
            log.info("Easter egg audio ended — forcing timer to zero.")
            self.timer.stop()
            self._post(self._on_finish)

    # -------------------------------------------------------------------------
    # Hidden WiFi panic-restore (typed code re-enables all autoconnects)
    # -------------------------------------------------------------------------

    def _trigger_apmode(self):
        """Show ---- on the display and run the wifi-restore-defaults script
        in a background thread. The script tears down AP mode, re-enables client
        autoconnects (home/laptop/phone), and triggers a rescan — bringing the
        Pi back onto a known WiFi network. Safe from the dispatch thread."""
        log.warning("APMODE CODE matched — running %s", APMODE_SCRIPT)
        self.display.show("----", colon=False)
        threading.Timer(2.5, lambda: self._post(self._restore_display)).start()

        def _run():
            try:
                result = subprocess.run(
                    ["sudo", APMODE_SCRIPT],
                    check=False, timeout=30,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                log.info("WiFi defaults restored (exit=%d)", result.returncode)
                if result.stderr:
                    log.warning("wifi-restore stderr: %s", result.stderr.decode().strip())
            except Exception as exc:
                log.warning("WiFi restore failed: %s", exc)
        threading.Thread(target=_run, name="APMode", daemon=True).start()

    def _trigger_reset_tally(self):
        """Clear playcounts.json (per-track play tally). Flash CLr- on the
        display for confirmation. In net/sim mode this is a no-op since no
        tally is kept. Safe from the dispatch thread."""
        log.warning("RESET TALLY CODE matched — clearing playcounts")
        self.display.show("CLr-", colon=False)
        threading.Timer(1.5, lambda: self._post(self._restore_display)).start()
        try:
            self.audio.clear_playcounts()
        except Exception as exc:
            log.warning("clear_playcounts failed: %s", exc)

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _fmt_countdown(self, remaining: int) -> str:
        """
        Format remaining seconds for display.
        Preserves the original minutes digit for easter egg combos like 0:69 or 6:66.
        Falls back to normal MM:SS when extra seconds exceed 99 (e.g. after many +30 presses)
        to avoid 5-char format strings that cause the display to update only every 10 ticks.
        """
        extra = remaining - self._cd_mm * 60
        if 0 <= extra <= 99:
            return "%02d%02d" % (self._cd_mm, extra)
        m, s = divmod(remaining, 60)
        return "%02d%02d" % (min(m, 99), s)

    def _begin_countdown(self):
        self._cancel_entry_timer()
        if not self._door_closed:
            log.info("Start pressed with door open — armed, waiting for door close.")
            self._start_entry_timer()
            return
        secs = self.buf.to_seconds()
        if secs == 0:
            return

        # Check for easter egg
        typed = self.buf.typed_str()
        egg   = EASTER_EGGS.get(typed)
        if egg:
            tracks = self.playlists.easter_tracks(typed)
            if tracks:
                self._begin_easter_egg(egg, tracks)
                return
            # Folder missing or empty — fall through to normal countdown

        # Normal countdown
        self._cd_mm = self.buf.raw_mm()
        log.info("Countdown: %ds (display %02d:%02d), DJ%d",
                 secs, self._cd_mm, self.buf.raw_ss(), self.playlists.selected)
        self._state = State.COUNTING_DOWN
        self.audio.start(self.playlists.next_track)
        self.timer.start(secs)

    def _pause_countdown(self):
        self._state = State.PAUSED
        self.timer.pause()
        self.audio.pause()
        log.info("Paused.")

    def _resume_countdown(self):
        self._state = State.COUNTING_DOWN
        self.timer.resume()
        self.audio.resume()
        log.info("Resumed.")

    def _show_clock(self, force: bool = False):
        now = datetime.now()
        h   = now.hour % 12 or 12
        m   = now.minute
        if not force and (h, m) == self._last_clock:
            return
        self._last_clock = (h, m)
        self.display.show("%2d%02d" % (h, m))

    def _restore_display(self):
        if self._state == State.ENTERING_TIME:
            self.display.show(self.buf.display_str())
        elif self._state == State.IDLE:
            self._show_clock(force=True)

    def _cancel_door_timer(self):
        if self._door_timer:
            self._door_timer.cancel()
            self._door_timer = None

    def _start_entry_timer(self):
        self._cancel_entry_timer()
        if ENTRY_IDLE_TIMEOUT > 0:
            t = threading.Timer(ENTRY_IDLE_TIMEOUT, lambda: self._post(self._go_idle_from_entry))
            t.daemon = True
            t.start()
            self._entry_timer = t

    def _cancel_entry_timer(self):
        if self._entry_timer:
            self._entry_timer.cancel()
            self._entry_timer = None

    def _go_idle_from_entry(self):
        if self._state != State.ENTERING_TIME:
            return
        self.buf.clear()
        self._state = State.IDLE
        self._show_clock(force=True)
        log.info("Entry idle timeout — returned to clock.")

    # -------------------------------------------------------------------------
    # Main loop  (runs on main thread — owns pygame event pump and rendering)
    # -------------------------------------------------------------------------

    def _start_scheduling_watchdog(self):
        """Background thread that sleeps 20ms in a tight loop and logs any
        scheduling gap > 60ms. Catches OS/kernel stalls that would also
        starve the SDL audio callback — the most likely cause of brief clipping."""
        def _watchdog():
            target = 0.02
            stall = 0.06
            last = time.monotonic()
            while True:
                time.sleep(target)
                now = time.monotonic()
                gap = now - last
                if gap > stall:
                    log.warning("Scheduling watchdog stall: %.0fms (target %.0fms) — possible audio-clip cause",
                                gap * 1000, target * 1000)
                last = now
        threading.Thread(target=_watchdog, name="SchedWatchdog", daemon=True).start()

    def run(self):
        log.info("Running — Ctrl+C or Esc to quit.")
        self._start_scheduling_watchdog()
        last_iter = time.monotonic()
        try:
            while True:
                now = time.monotonic()
                gap = now - last_iter
                # Main loop targets 50ms (20fps). >150ms means we hung — possible audio cause.
                if gap > 0.15:
                    log.warning("Main loop stall: %.0fms gap (target 50ms)", gap * 1000)
                last_iter = now
                for ev in pygame.event.get():
                    if ev.type == pygame.QUIT:
                        return
                    if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                        return
                    if ev.type == AudioEngine._MUSIC_END:
                        self.audio.notify_music_end()

                if self._state == State.IDLE:
                    self._show_clock()

                self.display.render()
                time.sleep(0.05)   # 20 fps — smooth enough for animation

        except KeyboardInterrupt:
            pass
        finally:
            self._shutdown()

    def _shutdown(self):
        log.info("Shutting down…")
        self._race_abort.set()
        self._dj_race_abort.set()
        self._cancel_door_timer()
        self._cancel_entry_timer()
        self.timer.stop()
        self.audio.stop()
        self.audio.shutdown()        # flush unsaved play counts
        self.input.stop()            # stop poll thread + release GPIO chip
        self._q.put(_STOP_SENTINEL)  # drain dispatch thread cleanly
        self.relays.close()
        pygame.quit()
        log.info("Goodbye.")


# =============================================================================
# STARTUP VALIDATION
# =============================================================================

def check_env() -> bool:
    ok = True
    for path, label in [(BEEP_SOUND, "beep"), (DING_SOUND, "ding")]:
        if not os.path.isfile(path):
            log.error("Missing %s sound: %s", label, path)
            ok = False
    if not os.path.isdir(MUSIC_ROOT):
        log.error("Music root not found: %s", MUSIC_ROOT)
        ok = False
    return ok


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if not check_env():
        log.critical("Environment check failed — fix errors above and restart.")
        raise SystemExit(1)
    try:
        app = MicroRaveApp()
        app.run()
    except Exception as exc:
        log.critical("Fatal: %s", exc, exc_info=True)
        raise SystemExit(1)
