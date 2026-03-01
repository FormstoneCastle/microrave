"""
MicroRave Music Player  —  v2.0
================================
Turns a microwave shell into a music player.

  - Number keys enter a countdown time (digits shift in from the right)
  - Closing the door (or pressing START) begins the countdown
  - Music plays from a selected playlist for the duration
  - Opening the door pauses music and freezes the timer
  - Re-closing the door resumes both
  - Timer hitting 0:00 stops music and plays a finish sound
  - CANCEL resets to idle at any point
  - ADD_30 adds 30 seconds at any time
  - PLAYLIST_PREV / NEXT cycles playlists while idle or entering time
  - VOLUME_UP / DOWN works in any state

Hardware:
  WS2812 LED strip as 4-digit 7-segment display
  Number keys 0-9 on individual GPIO pins (or 4x3 matrix — see config)
  START, CANCEL, ADD_30, PLAYLIST_PREV, PLAYLIST_NEXT, VOLUME_UP, VOLUME_DOWN
  Magnetic door switch (reed switch)

Run normally (real hardware):
  sudo venv/bin/python microrave.py

Run in bridge mode (Windows simulator):
  venv/bin/python microrave.py --bridge

Requirements:
  pip install pygame rpi_ws281x gpiozero websockets
"""

import argparse
import logging
import os
import queue
import random
import threading
import time
from enum import Enum, auto

import pygame

# =============================================================================
# ARGUMENT PARSING  —  done before everything else so imports can branch
# =============================================================================

_ap = argparse.ArgumentParser(description="MicroRave Music Player")
_ap.add_argument("--bridge", action="store_true",
                 help="Use virtual GPIO/LED over WebSocket (for simulator testing)")
_ap.add_argument("--debug", action="store_true",
                 help="Enable DEBUG-level logging")
ARGS = _ap.parse_args()

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.DEBUG if ARGS.debug else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("microrave.log"),
    ],
)
log = logging.getLogger("MicroRave")

# =============================================================================
# HARDWARE / BRIDGE IMPORTS
# =============================================================================

if ARGS.bridge:
    from ws_bridge import (
        bridge,
        VirtualButton       as Button,
        VirtualOutputDevice as OutputDevice,
        VirtualPixelStrip   as PixelStrip,
        Color,
    )
    bridge.start()
    log.info("Bridge mode: GPIO and LED strip are virtual (WebSocket).")
else:
    from gpiozero import Button, OutputDevice
    from rpi_ws281x import Color, PixelStrip
    log.info("Hardware mode: real GPIO and WS2812 strip.")

# =============================================================================
# CONFIGURATION  —  edit this section to match your hardware wiring
# =============================================================================

# --- Keypad ---
# Set KEYPAD_MODE to "individual" (one GPIO per key) or "matrix" (4x3 matrix).
KEYPAD_MODE = "individual"

# Individual mode: maps digit character -> GPIO pin.
# IMPORTANT: every pin here must be unique and must not appear in any other
# pin constant below.  A duplicate will silently overwrite the button registry
# in bridge mode, causing one key to stop working.
KEYPAD_PIN_MAP = {
    "1":  4,  "2":  5,  "3":  6,
    "4": 12,  "5": 13,  "6": 16,
    "7": 17,  "8": 18,  "9": 19,
              "0": 20,
}

# Matrix mode: row and column pins, and the key layout.
MATRIX_ROW_PINS = [21, 22, 23, 24]
MATRIX_COL_PINS = [26, 27, 28]
MATRIX_LAYOUT   = [
    ["1", "2", "3"],
    ["4", "5", "6"],
    ["7", "8", "9"],
    ["*", "0", "#"],
]

# --- Control buttons  (must not share any pin with KEYPAD_PIN_MAP) ---
PIN_START         =  8
PIN_CANCEL        =  9
PIN_ADD_30        = 10
PIN_PLAYLIST_PREV = 11
PIN_PLAYLIST_NEXT = 14
PIN_VOLUME_UP     = 15
PIN_VOLUME_DOWN   = 25   # NOTE: not 16 — that is DIGIT_6 in individual mode

# --- Door switch ---
PIN_DOOR          =  7
DOOR_CLOSED_STATE = True   # True  = pin is pressed when door is closed
                            # False = pin is released when door is closed
DOOR_OPEN_TIMEOUT = 300    # seconds before auto-cancel if door stays open (0=disabled)

# --- LED display ---
LED_PIN          = 18   # GPIO pin for WS2812 data
LEDS_PER_SEGMENT =  8   # LEDs per segment bar
NUM_DIGITS       =  4   # display digits
LED_BRIGHTNESS   = 200  # 0-255

# --- Display colours (R, G, B) ---
COLOR_WHITE    = (255, 255, 255)   # idle / counting down
COLOR_CYAN     = (  0, 180, 255)   # entering time
COLOR_AMBER    = (255, 120,   0)   # paused (door open)
COLOR_GREEN    = (  0, 255,   0)   # finished
COLOR_OFF      = (  0,   0,   0)   # segment off

# --- Audio ---
MUSIC_ROOT     = "music"
FINISH_SOUND   = "sounds/ding.mp3"
BEEP_SOUND     = "sounds/beep.mp3"
VOLUME_DEFAULT = 70    # 0-100
VOLUME_STEP    =  5    # per button press

# =============================================================================
# PIN COLLISION GUARD
# Run at import time so a misconfiguration is caught immediately on startup.
# =============================================================================

def _assert_unique_pins():
    named = {
        "PIN_START":         PIN_START,
        "PIN_CANCEL":        PIN_CANCEL,
        "PIN_ADD_30":        PIN_ADD_30,
        "PIN_PLAYLIST_PREV": PIN_PLAYLIST_PREV,
        "PIN_PLAYLIST_NEXT": PIN_PLAYLIST_NEXT,
        "PIN_VOLUME_UP":     PIN_VOLUME_UP,
        "PIN_VOLUME_DOWN":   PIN_VOLUME_DOWN,
        "PIN_DOOR":          PIN_DOOR,
    }
    if KEYPAD_MODE == "individual":
        for char, pin in KEYPAD_PIN_MAP.items():
            named[f"DIGIT_{char}"] = pin

    seen   = {}
    errors = []
    for name, pin in named.items():
        if pin in seen:
            errors.append(f"  Pin {pin} used by both {seen[pin]} and {name}")
        else:
            seen[pin] = name

    if errors:
        msg = "PIN COLLISION — fix configuration before running:\n" + "\n".join(errors)
        log.critical(msg)
        raise SystemExit(1)

_assert_unique_pins()

# =============================================================================
# DISPLAY  —  7-segment LED strip
# =============================================================================

# Segment order as wired on the physical strip.
# Each digit uses 7 segments in this order, each LEDS_PER_SEGMENT LEDs wide.
SEGMENT_ORDER = ["g", "b", "a", "f", "e", "d", "c"]

# Which segments are lit for each displayable character.
CHAR_SEGMENTS = {
    "0": set("abcdef"),
    "1": set("bc"),
    "2": set("abdeg"),
    "3": set("abcdg"),
    "4": set("bcfg"),
    "5": set("acdfg"),
    "6": set("acdefg"),
    "7": set("abc"),
    "8": set("abcdefg"),
    "9": set("abcdfg"),
    "-": set("g"),
    " ": set(),
}


class Display:
    """
    Drives the WS2812 LED strip as a 4-digit 7-segment display.
    Thread-safe: show(), set_color(), and clear() may be called from any thread.
    """

    def __init__(self):
        total = NUM_DIGITS * 7 * LEDS_PER_SEGMENT
        self._strip = PixelStrip(
            total, LED_PIN,
            freq_hz=800_000, dma=10, invert=False,
            brightness=LED_BRIGHTNESS, channel=0,
        )
        self._color = COLOR_WHITE
        self._lock  = threading.Lock()
        try:
            self._strip.begin()
            log.info("Display ready — %d LEDs (%d digits × 7 segs × %d LEDs/seg).",
                     total, NUM_DIGITS, LEDS_PER_SEGMENT)
        except Exception as exc:
            log.error("Display init failed: %s  Running without display.", exc)
            self._strip = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_color(self, color: tuple):
        """Set the on-colour for subsequent show() calls."""
        with self._lock:
            self._color = color

    def show(self, text: str):
        """
        Render text on the display.  text is up to 4 characters; a colon or
        dot is stripped (the colon position is always implied by the layout).
        Unknown characters render as blanks.
        """
        log.debug("Display.show(%r)", text)
        with self._lock:
            if not self._strip:
                return
            clean = text.replace(":", "").replace(".", "")[:NUM_DIGITS].rjust(NUM_DIGITS)
            for pos, char in enumerate(clean):
                self._render_digit(pos, char)
            self._flush()

    def clear(self):
        """Turn off every LED."""
        with self._lock:
            if not self._strip:
                return
            for i in range(self._strip.numPixels()):
                self._strip.setPixelColor(i, Color(*COLOR_OFF))
            self._flush()

    def set_brightness(self, value: int):
        with self._lock:
            if self._strip:
                self._strip.setBrightness(max(0, min(255, value)))
                self._flush()

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _render_digit(self, pos: int, char: str):
        lit      = CHAR_SEGMENTS.get(char, set())   # unknown char -> all off
        base     = pos * 7 * LEDS_PER_SEGMENT
        color_on = self._color
        for seg_i, seg_name in enumerate(SEGMENT_ORDER):
            color    = Color(*color_on) if seg_name in lit else Color(*COLOR_OFF)
            seg_base = base + seg_i * LEDS_PER_SEGMENT
            for led in range(LEDS_PER_SEGMENT):
                self._strip.setPixelColor(seg_base + led, color)

    def _flush(self):
        try:
            self._strip.show()
        except Exception as exc:
            log.warning("Display flush error: %s", exc)


# =============================================================================
# PLAYLIST MANAGER
# =============================================================================

class PlaylistManager:
    """Scans MUSIC_ROOT for subdirectories containing audio files."""

    _AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".flac", ".m4a")

    def __init__(self):
        self._playlists: list[tuple[str, list[str]]] = []
        self._index = 0
        self._load()

    def _load(self):
        if not os.path.isdir(MUSIC_ROOT):
            raise RuntimeError(f"Music root not found: '{MUSIC_ROOT}'")
        for entry in sorted(os.scandir(MUSIC_ROOT), key=lambda e: e.name.lower()):
            if not entry.is_dir():
                continue
            tracks = sorted(
                os.path.join(entry.path, f)
                for f in os.listdir(entry.path)
                if f.lower().endswith(self._AUDIO_EXTS)
            )
            if tracks:
                self._playlists.append((entry.name, tracks))
        if not self._playlists:
            raise RuntimeError(f"No playlists found in '{MUSIC_ROOT}'.")
        log.info("Playlists loaded: %s", [p[0] for p in self._playlists])

    @property
    def current_name(self) -> str:
        return self._playlists[self._index][0]

    def shuffled_tracks(self) -> list[str]:
        tracks = list(self._playlists[self._index][1])
        random.shuffle(tracks)
        return tracks

    def next(self):
        self._index = (self._index + 1) % len(self._playlists)
        log.info("Playlist → %s", self.current_name)

    def prev(self):
        self._index = (self._index - 1) % len(self._playlists)
        log.info("Playlist → %s", self.current_name)


# =============================================================================
# AUDIO ENGINE
# =============================================================================

class AudioEngine:
    """
    Wraps pygame.mixer.  Tries audio drivers in order until one works.
    Falls back to silent mode if none do — the app continues running.
    """

    _MUSIC_END = pygame.USEREVENT + 1

    def __init__(self):
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
        self._ok      = False
        self._volume  = VOLUME_DEFAULT
        self._tracks: list[str] = []
        self._t_index = 0
        self._playing = False
        self._paused  = False
        self._lock    = threading.Lock()

        for driver in ("pipewire", "pulseaudio", "alsa", "dummy"):
            os.environ["SDL_AUDIODRIVER"] = driver
            try:
                pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
                pygame.init()
                pygame.mixer.init()
                log.info("Audio driver: %s", driver)
                self._ok = True
                break
            except Exception as exc:
                log.warning("Audio driver '%s' unavailable: %s", driver, exc)
                # Full teardown before trying next driver
                try:
                    pygame.mixer.quit()
                except Exception:
                    pass
                try:
                    pygame.quit()
                except Exception:
                    pass

        if not self._ok:
            log.error("All audio drivers failed — running silent.")
            os.environ["SDL_AUDIODRIVER"] = "dummy"
            pygame.init()

        if self._ok:
            pygame.mixer.set_num_channels(8)
            pygame.mixer.music.set_endevent(self._MUSIC_END)
            self._beep_ch   = pygame.mixer.Channel(7)
            self._finish_ch = pygame.mixer.Channel(6)
        else:
            self._beep_ch   = None
            self._finish_ch = None

        self._beep_snd   = self._load_sound(BEEP_SOUND,   "beep")   if self._ok else None
        self._finish_snd = self._load_sound(FINISH_SOUND, "finish") if self._ok else None

        if self._ok:
            self._apply_volume()
            threading.Thread(
                target=self._event_loop, name="AudioEvents", daemon=True
            ).start()

        log.info("Audio engine ready (ok=%s, volume=%d%%).", self._ok, self._volume)

    # ------------------------------------------------------------------

    def start(self, tracks: list[str]):
        if not self._ok or not tracks:
            return
        with self._lock:
            self._tracks  = tracks
            self._t_index = 0
            self._playing = True
            self._paused  = False
        self._play_track(tracks[0])

    def pause(self):
        if not self._ok:
            return
        with self._lock:
            if not self._playing or self._paused:
                return
            self._paused = True
        pygame.mixer.music.pause()
        log.info("Audio paused.")

    def resume(self):
        if not self._ok:
            return
        with self._lock:
            if not self._playing or not self._paused:
                return
            self._paused = False
        pygame.mixer.music.unpause()
        log.info("Audio resumed.")

    def stop(self):
        with self._lock:
            self._playing = False
            self._paused  = False
        if self._ok:
            pygame.mixer.music.stop()

    def play_finish_sound(self):
        self.stop()
        if self._ok and self._finish_snd and self._finish_ch:
            self._finish_ch.set_volume(self._volume / 100)
            self._finish_ch.play(self._finish_snd)
            log.info("Finish sound.")

    def beep(self):
        if self._ok and self._beep_snd and self._beep_ch:
            self._beep_ch.stop()
            self._beep_ch.set_volume(self._volume / 100)
            self._beep_ch.play(self._beep_snd)

    def volume_up(self):
        self._volume = min(100, self._volume + VOLUME_STEP)
        self._apply_volume()
        log.info("Volume → %d%%", self._volume)

    def volume_down(self):
        self._volume = max(0, self._volume - VOLUME_STEP)
        self._apply_volume()
        log.info("Volume → %d%%", self._volume)

    # ------------------------------------------------------------------

    @staticmethod
    def _load_sound(path: str, label: str):
        try:
            snd = pygame.mixer.Sound(path)
            log.info("Loaded %s: %s", label, path)
            return snd
        except Exception as exc:
            log.error("Cannot load %s '%s': %s", label, path, exc)
            return None

    def _play_track(self, path: str):
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self._volume / 100)
            pygame.mixer.music.play()
            log.info("Now playing: %s", os.path.basename(path))
        except Exception as exc:
            log.error("Cannot play '%s': %s — skipping.", path, exc)
            pygame.event.post(pygame.event.Event(self._MUSIC_END))

    def _event_loop(self):
        """Advance to next track when the current one ends."""
        while True:
            try:
                event = pygame.event.wait()
                if event.type != self._MUSIC_END:
                    continue
                with self._lock:
                    if not self._playing or self._paused:
                        continue
                    self._t_index = (self._t_index + 1) % max(1, len(self._tracks))
                    next_track    = self._tracks[self._t_index]
                self._play_track(next_track)
            except Exception as exc:
                log.error("Audio event loop: %s", exc)
                time.sleep(0.5)

    def _apply_volume(self):
        if not self._ok:
            return
        v = self._volume / 100
        pygame.mixer.music.set_volume(v)
        if self._beep_ch:
            self._beep_ch.set_volume(v)
        if self._finish_ch:
            self._finish_ch.set_volume(v)


# =============================================================================
# COUNTDOWN TIMER
# =============================================================================

class CountdownTimer:
    """
    Accurate 1-second countdown.  Calls on_tick(remaining) every second and
    on_finish() when remaining hits zero.  All callbacks are fired from the
    timer thread; callers should post to a queue rather than act directly.
    """

    def __init__(self, on_tick, on_finish):
        self._on_tick   = on_tick
        self._on_finish = on_finish
        self._remaining = 0
        self._lock      = threading.Lock()
        self._stop_evt  = threading.Event()
        self._pause_evt = threading.Event()
        self._pause_evt.set()   # not paused initially
        self._thread: threading.Thread | None = None

    def start(self, seconds: int):
        self.stop()
        with self._lock:
            self._remaining = max(0, seconds)
        self._stop_evt.clear()
        self._pause_evt.set()
        self._thread = threading.Thread(target=self._run, name="Countdown", daemon=True)
        self._thread.start()

    def pause(self):
        self._pause_evt.clear()

    def resume(self):
        self._pause_evt.set()

    def stop(self):
        self._stop_evt.set()
        self._pause_evt.set()   # unblock if paused so thread can exit
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None

    def add_seconds(self, n: int):
        with self._lock:
            self._remaining = max(0, self._remaining + n)
        log.info("Timer +%ds → %ds remaining.", n, self._remaining)

    @property
    def remaining(self) -> int:
        with self._lock:
            return self._remaining

    def _run(self):
        while not self._stop_evt.is_set():
            self._pause_evt.wait()
            if self._stop_evt.is_set():
                break
            with self._lock:
                r = self._remaining
            self._on_tick(r)
            if r <= 0:
                self._on_finish()
                return
            # Wait exactly 1 second (or until stop)
            self._stop_evt.wait(timeout=1.0)
            if not self._stop_evt.is_set():
                with self._lock:
                    self._remaining = max(0, self._remaining - 1)


# =============================================================================
# TIME ENTRY BUFFER
# =============================================================================

class TimeEntryBuffer:
    """
    Accumulates digit presses into a MM:SS time value.
    Digits shift in from the right, like a real microwave keypad.
    Rejects entries that would exceed 99:59.
    """

    _MAX_SECONDS = 99 * 60 + 59

    def __init__(self):
        self._d = [0, 0, 0, 0]

    def push(self, digit: int):
        candidate = self._d[1:] + [digit]
        # Accept only if the resulting time is within bounds.
        total = (candidate[0] * 10 + candidate[1]) * 60 + (candidate[2] * 10 + candidate[3])
        if total <= self._MAX_SECONDS:
            self._d = candidate

    def clear(self):
        self._d = [0, 0, 0, 0]

    def to_seconds(self) -> int:
        return (self._d[0] * 10 + self._d[1]) * 60 + (self._d[2] * 10 + self._d[3])

    def to_display_string(self) -> str:
        # Normalize through seconds so that e.g. raw buffer [0,0,6,0]
        # (from typing "6","0") displays as "01:00" not "00:60".
        t = self.to_seconds()
        return "%02d:%02d" % (min(t // 60, 99), t % 60)

    def is_zero(self) -> bool:
        return self.to_seconds() == 0


# =============================================================================
# APPLICATION STATE
# =============================================================================

class State(Enum):
    IDLE          = auto()
    ENTERING_TIME = auto()
    COUNTING_DOWN = auto()
    PAUSED        = auto()
    FINISHED      = auto()


# =============================================================================
# MICRORAVE APPLICATION
# =============================================================================

class MicroRaveApp:
    """
    Central application class.  All state changes happen on the dispatch
    thread (a single-threaded queue), which prevents race conditions.
    Button callbacks and timer callbacks post work items to the queue.
    """

    def __init__(self):
        self._state      = State.IDLE
        self._state_lock = threading.Lock()
        self._q          = queue.SimpleQueue()
        self._door_timer:    threading.Timer | None = None
        self._door_is_closed: bool                = False  # set by _read_initial_door_state

        # Start the dispatch thread first so queued work is processed
        # even if later init steps post to the queue.
        threading.Thread(
            target=self._dispatch_loop, name="Dispatch", daemon=True
        ).start()

        self.display   = Display()
        self.playlists = PlaylistManager()
        self.audio     = AudioEngine()
        self.timer     = CountdownTimer(
            on_tick   = lambda r: self._post(self._on_tick,   r),
            on_finish = lambda:   self._post(self._on_finish),
        )
        self.time_buf = TimeEntryBuffer()

        self._setup_buttons()
        self._setup_door()
        self._read_initial_door_state()

        self.display.set_color(COLOR_WHITE)
        self.display.show("0:00")
        log.info("MicroRave ready.  Playlist: %s", self.playlists.current_name)

    # ------------------------------------------------------------------
    # Dispatch queue
    # ------------------------------------------------------------------

    def _post(self, fn, *args):
        self._q.put((fn, args))

    def _dispatch_loop(self):
        while True:
            fn, args = self._q.get()
            try:
                fn(*args)
            except Exception as exc:
                log.error("Dispatch error in %s: %s", fn.__name__, exc, exc_info=True)

    # ------------------------------------------------------------------
    # State property (thread-safe read/write)
    # ------------------------------------------------------------------

    @property
    def state(self) -> State:
        with self._state_lock:
            return self._state

    @state.setter
    def state(self, new: State):
        with self._state_lock:
            old = self._state
            self._state = new
        if old != new:
            log.info("State: %s → %s", old.name, new.name)

    # ------------------------------------------------------------------
    # Button / door setup
    # ------------------------------------------------------------------

    def _setup_buttons(self):
        bounce = 0.05

        if KEYPAD_MODE == "individual":
            self._num_btns = []
            for char, pin in KEYPAD_PIN_MAP.items():
                btn = Button(pin, pull_up=True, bounce_time=bounce)
                btn.when_pressed = self._make_digit_cb(int(char))
                self._num_btns.append(btn)
        else:
            self._setup_matrix()

        def cb(fn):
            return lambda: self._post(fn)

        self._btn_start    = Button(PIN_START,         pull_up=True, bounce_time=bounce)
        self._btn_cancel   = Button(PIN_CANCEL,        pull_up=True, bounce_time=bounce)
        self._btn_add30    = Button(PIN_ADD_30,        pull_up=True, bounce_time=bounce)
        self._btn_pl_prev  = Button(PIN_PLAYLIST_PREV, pull_up=True, bounce_time=bounce)
        self._btn_pl_next  = Button(PIN_PLAYLIST_NEXT, pull_up=True, bounce_time=bounce)
        self._btn_vol_up   = Button(PIN_VOLUME_UP,     pull_up=True, bounce_time=bounce)
        self._btn_vol_down = Button(PIN_VOLUME_DOWN,   pull_up=True, bounce_time=bounce)

        self._btn_start.when_pressed    = cb(self._on_start)
        self._btn_cancel.when_pressed   = cb(self._on_cancel)
        self._btn_add30.when_pressed    = cb(self._on_add_30)
        self._btn_pl_prev.when_pressed  = cb(self._on_playlist_prev)
        self._btn_pl_next.when_pressed  = cb(self._on_playlist_next)
        self._btn_vol_up.when_pressed   = cb(self._on_volume_up)
        self._btn_vol_down.when_pressed = cb(self._on_volume_down)

        log.info("Buttons configured (%s mode).", KEYPAD_MODE)

    def _setup_matrix(self):
        self._mx_rows = [OutputDevice(p, initial_value=True) for p in MATRIX_ROW_PINS]
        self._mx_cols = [Button(p, pull_up=True) for p in MATRIX_COL_PINS]
        self._mx_last = None
        threading.Thread(
            target=self._matrix_scan, name="MatrixScan", daemon=True
        ).start()

    def _matrix_scan(self):
        while True:
            try:
                pressed = None
                for r_i, row in enumerate(self._mx_rows):
                    row.off()
                    time.sleep(0.001)
                    for c_i, col in enumerate(self._mx_cols):
                        if not col.is_pressed:
                            pressed = MATRIX_LAYOUT[r_i][c_i]
                    row.on()
                if pressed != self._mx_last:
                    if pressed and pressed.isdigit():
                        self._post(self._on_digit, int(pressed))
                    self._mx_last = pressed
                time.sleep(0.02)
            except Exception as exc:
                log.error("Matrix scan error: %s — retrying.", exc)
                self._mx_last = None
                time.sleep(1)

    def _setup_door(self):
        self._door = Button(PIN_DOOR, pull_up=True, bounce_time=0.15)
        if DOOR_CLOSED_STATE:
            self._door.when_pressed  = lambda: self._post(self._on_door_close)
            self._door.when_released = lambda: self._post(self._on_door_open)
        else:
            self._door.when_pressed  = lambda: self._post(self._on_door_open)
            self._door.when_released = lambda: self._post(self._on_door_close)
        log.info("Door switch on GPIO %d (closed_state=%s).", PIN_DOOR, DOOR_CLOSED_STATE)

    def _read_initial_door_state(self):
        self._door_is_closed = (self._door.is_pressed == DOOR_CLOSED_STATE)
        log.info("Door at boot: %s.", "closed" if self._door_is_closed else "open")

    def _make_digit_cb(self, digit: int):
        return lambda: self._post(self._on_digit, digit)

    # ------------------------------------------------------------------
    # Event handlers  (all called on the dispatch thread)
    # ------------------------------------------------------------------

    def _on_digit(self, digit: int):
        self.audio.beep()
        if self.state in (State.IDLE, State.ENTERING_TIME):
            self.time_buf.push(digit)
            self.state = State.ENTERING_TIME
            self.display.set_color(COLOR_CYAN)
            self.display.show(self.time_buf.to_display_string())
        elif self.state == State.COUNTING_DOWN:
            self.timer.add_seconds(digit)
            self._refresh_display()

    def _on_start(self):
        self.audio.beep()
        if self.state == State.ENTERING_TIME:
            if not self.time_buf.is_zero():
                if self._door_is_closed:
                    self._begin_countdown()
                else:
                    # Time is set but door is open — arm and wait for door close.
                    # Display goes amber to signal "ready, door open".
                    log.info("START pressed but door is open — armed, waiting for door close.")
                    self.display.set_color(COLOR_AMBER)
                    self.display.show(self.time_buf.to_display_string())
        elif self.state == State.PAUSED:
            self._resume_countdown()

    def _on_cancel(self):
        self.audio.beep()
        log.info("Cancel.")
        self._cancel_door_timer()
        self.timer.stop()
        self.audio.stop()
        self.time_buf.clear()
        self.state = State.IDLE
        self.display.set_color(COLOR_WHITE)
        self.display.show("0:00")

    def _on_add_30(self):
        self.audio.beep()
        if self.state == State.ENTERING_TIME:
            self.time_buf.push(3)
            self.time_buf.push(0)
            self.display.show(self.time_buf.to_display_string())
        elif self.state in (State.COUNTING_DOWN, State.PAUSED):
            self.timer.add_seconds(30)
            self._refresh_display()

    def _on_playlist_prev(self):
        self.audio.beep()
        if self.state in (State.IDLE, State.ENTERING_TIME):
            self.playlists.prev()
            self._flash_playlist_name()

    def _on_playlist_next(self):
        self.audio.beep()
        if self.state in (State.IDLE, State.ENTERING_TIME):
            self.playlists.next()
            self._flash_playlist_name()

    def _on_volume_up(self):
        self.audio.beep()
        self.audio.volume_up()

    def _on_volume_down(self):
        self.audio.beep()
        self.audio.volume_down()

    def _on_door_close(self):
        log.info("Door closed.")
        self._door_is_closed = True
        self._cancel_door_timer()
        if self.state == State.ENTERING_TIME and not self.time_buf.is_zero():
            self._begin_countdown()
        elif self.state == State.PAUSED:
            self._resume_countdown()

    def _on_door_open(self):
        log.info("Door opened.")
        self._door_is_closed = False
        if self.state == State.COUNTING_DOWN:
            self._pause_countdown()
            if DOOR_OPEN_TIMEOUT > 0:
                self._door_timer = threading.Timer(
                    DOOR_OPEN_TIMEOUT,
                    lambda: self._post(self._on_cancel),
                )
                self._door_timer.daemon = True
                self._door_timer.start()
                log.info("Door timeout: auto-cancel in %ds.", DOOR_OPEN_TIMEOUT)

    def _on_tick(self, remaining: int):
        if self.state == State.COUNTING_DOWN:
            self._refresh_display()

    def _on_finish(self):
        log.info("Countdown finished!")
        self._cancel_door_timer()
        self.state = State.FINISHED
        self.time_buf.clear()
        self.display.set_color(COLOR_GREEN)
        self.display.show("0:00")
        self.audio.play_finish_sound()
        t = threading.Timer(3.0, lambda: self._post(self._go_idle))
        t.daemon = True
        t.start()

    def _go_idle(self):
        if self.state == State.FINISHED:
            self.state = State.IDLE
            self.display.set_color(COLOR_WHITE)
            self.display.show("0:00")
            log.info("Back to idle.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _begin_countdown(self):
        seconds = self.time_buf.to_seconds()
        if seconds == 0:
            log.warning("begin_countdown called with 0 seconds — ignored.")
            return
        if not self._door_is_closed:
            # Door is open — can't start, like a real microwave.
            # Stay in ENTERING_TIME so the door-close handler can launch us.
            log.info("begin_countdown: door is open — armed, waiting for door close.")
            self.display.set_color(COLOR_AMBER)
            self.display.show(self.time_buf.to_display_string())
            return
        log.info("Countdown: %ds | Playlist: %s", seconds, self.playlists.current_name)
        self.state = State.COUNTING_DOWN
        self.display.set_color(COLOR_WHITE)
        self.audio.start(self.playlists.shuffled_tracks())
        self.timer.start(seconds)

    def _pause_countdown(self):
        self.state = State.PAUSED
        self.timer.pause()
        self.audio.pause()
        self.display.set_color(COLOR_AMBER)
        self._refresh_display()

    def _resume_countdown(self):
        self.state = State.COUNTING_DOWN
        self.display.set_color(COLOR_WHITE)
        self._refresh_display()
        self.timer.resume()
        self.audio.resume()

    def _refresh_display(self):
        r = self.timer.remaining
        self.display.show("%02d:%02d" % (min(r // 60, 99), r % 60))

    def _cancel_door_timer(self):
        if self._door_timer:
            self._door_timer.cancel()
            self._door_timer = None

    def _flash_playlist_name(self):
        """Briefly show the playlist name on the display then restore."""
        name = self.playlists.current_name[:4].upper().ljust(4)
        self.display.set_color(COLOR_CYAN)
        self.display.show(name)
        t = threading.Timer(1.5, lambda: self._post(self._restore_display))
        t.daemon = True
        t.start()

    def _restore_display(self):
        if self.state == State.ENTERING_TIME:
            self.display.set_color(COLOR_CYAN)
            self.display.show(self.time_buf.to_display_string())
        elif self.state == State.IDLE:
            self.display.set_color(COLOR_WHITE)
            self.display.show("0:00")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self):
        log.info("Running.  Press Ctrl+C to quit.")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            log.info("Shutting down…")
        finally:
            self._cancel_door_timer()
            self.timer.stop()
            self.audio.stop()
            self.display.clear()
            log.info("Goodbye.")


# =============================================================================
# STARTUP VALIDATION
# =============================================================================

def validate_environment() -> bool:
    ok = True
    for path, label in [(BEEP_SOUND, "beep"), (FINISH_SOUND, "finish")]:
        if not os.path.isfile(path):
            log.error("Missing %s sound: '%s'", label, path)
            ok = False
    if not os.path.isdir(MUSIC_ROOT):
        log.error("Music root not found: '%s'", MUSIC_ROOT)
        ok = False
    else:
        exts = (".mp3", ".wav", ".ogg", ".flac", ".m4a")
        has_music = any(
            any(f.lower().endswith(exts) for f in os.listdir(e.path))
            for e in os.scandir(MUSIC_ROOT) if e.is_dir()
        )
        if not has_music:
            log.error("No audio files found under '%s'.", MUSIC_ROOT)
            ok = False
    return ok


# =============================================================================
# ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    if not validate_environment():
        log.critical("Environment check failed — fix errors above and restart.")
        raise SystemExit(1)
    try:
        app = MicroRaveApp()
        app.run()
    except Exception as exc:
        log.critical("Fatal error: %s", exc, exc_info=True)
        raise SystemExit(1)
