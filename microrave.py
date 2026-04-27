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

import json
import logging
import os
import queue
import random
import subprocess
import threading
import time
from datetime import datetime
from enum import Enum, auto

try:
    import serial as _serial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False

import lgpio
import pygame

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
PIN_DJ = {1: 4, 2: 5, 3: 6, 4: 26, 5: 27, 6: 9}

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

# Row 7: volume (reserved — not yet wired)
PIN_VOL_UP   = 3
PIN_VOL_DOWN = 0

# Row 8: door switch
PIN_DOOR = 25

# =============================================================================
# SETTINGS
# =============================================================================

RELAY_PORT        = "/dev/ttyACM0"  # Arduino USB serial port
RELAY_BAUD        = 9600

MUSIC_ROOT        = "music"
SOUNDS_DIR        = "sounds"
PLAYCOUNTS_FILE   = "playcounts.json"
EASTER_EGG_DIR    = os.path.join(SOUNDS_DIR, "easter")
BEEP_SOUND        = os.path.join(SOUNDS_DIR, "beep.mp3")
DING_SOUND        = os.path.join(SOUNDS_DIR, "ding.mp3")
VOLUME_DEFAULT    = 70    # 0–100
VOLUME_STEP       = 5
DOOR_OPEN_TIMEOUT   = 60    # seconds before auto-cancel when door left open; 0 = disabled
ENTRY_IDLE_TIMEOUT  = 60    # seconds on 0000 screen with no input before returning to clock
DEBOUNCE_S        = 0.05  # seconds to ignore re-triggers after a switch edge
GPIO_POLL_HZ      = 50    # GPIO polling rate

# DJ race animation (runs after each session to pick a random DJ)
RANDOM_DJ_ON_FINISH = True   # set False to disable random DJ selection
DJ_RACE_SEQUENCE    = [1, 2, 3, 6, 5, 4]  # clockwise around the 2×3 light grid
DJ_RACE_LAPS        = 3      # full laps before landing
DJ_RACE_STEP_S      = 0.12   # seconds per step

# Easter egg trigger map
# key = digit string as typed; seconds/mm/ss define the countdown
# 6767 overrides to 0:67 (67 sec) — the fun is in entering it
EASTER_EGGS: dict[str, dict] = {
    "007":  {"folder": "007",  "seconds": 102,  "mm": 1,  "ss": 42},
    "42":   {"folder": "042",  "seconds": 213,  "mm": 3,  "ss": 33},
    "069":  {"folder": "069",  "seconds": 69,   "mm": 0,  "ss": 69},
    "420":  {"folder": "420",  "seconds": 260,  "mm": 4,  "ss": 20},
    "666":  {"folder": "666",  "seconds": 426,  "mm": 6,  "ss": 66},
    "67":   {"folder": "067",  "seconds": 67,   "mm": 0,  "ss": 67},
    "6767": {"folder": "6767", "seconds": 67,   "mm": 0,  "ss": 67},
    "8008": {"folder": "8008", "seconds": 4808, "mm": 80, "ss": 8},
}

RACE_DURATION = 3.0   # seconds for race-around animation
RACE_STEP_S   = 0.08  # seconds per animation frame

# =============================================================================
# DISPLAY COLORS & GEOMETRY
# =============================================================================

COLOR_BG      = (  0,   0,   0)   # black background
COLOR_ON      = (  0, 255,   0)   # bright green segments
COLOR_DIM     = (  0,  13,   0)   # dim green for unlit segments
SHOW_DIM_SEGS = True               # show unlit segments (real 7-seg look)

# =============================================================================
# 7-SEGMENT CHARACTER MAP
# =============================================================================

#   _a_
#  f   b
#   _g_
#  e   c
#   _d_

CHAR_SEGS: dict[str, set] = {
    '0': set('abcdef'),
    '1': set('bc'),
    '2': set('abdeg'),
    '3': set('abcdg'),
    '4': set('bcfg'),
    '5': set('acdfg'),
    '6': set('acdefg'),
    '7': set('abc'),
    '8': set('abcdefg'),
    '9': set('abcdfg'),
    'd': set('bcdeg'),    # lowercase d  (used in DJ flash: "dJ x")
    'J': set('bcd'),      # uppercase J
    '-': set('g'),
    ' ': set(),
}


# =============================================================================
# DISPLAY
# =============================================================================

class Display:
    """
    Fullscreen pygame window rendering a 4-digit 7-segment display.
    Always green on black.

    Thread safety:
      show() / show_segs() — safe to call from any thread (sets a pending update)
      render()             — must be called from the main thread only
    """

    def __init__(self):
        info = pygame.display.Info()
        self._sw = info.current_w
        self._sh = info.current_h

        self._screen = pygame.display.set_mode(
            (self._sw, self._sh), pygame.FULLSCREEN | pygame.NOFRAME
        )
        pygame.display.set_caption("MicroRave")
        pygame.mouse.set_visible(False)

        # Digit geometry — fill screen minus MARGIN on each edge
        MARGIN   = 20
        avail_w  = self._sw - 2 * MARGIN
        avail_h  = self._sh - 2 * MARGIN
        ASPECT   = 0.55   # digit width / digit height

        dh_from_w = avail_w / (ASPECT * (4 + 1/3 + 0.4))
        self._dh  = int(min(dh_from_w, avail_h))
        self._dw  = int(self._dh * ASPECT)
        self._T   = max(8, int(self._dh * 0.10))
        self._G   = max(2, int(self._T  * 0.15))

        col_w = self._dw // 3
        sp    = max(4, int(self._dw * 0.08))

        total_w = 4 * self._dw + col_w + 5 * sp
        ox = (self._sw - total_w) // 2
        oy = (self._sh - self._dh)  // 2

        self._dx = [
            ox,
            ox +     self._dw + sp,
            ox + 2 * self._dw + 2 * sp + col_w + sp,
            ox + 3 * self._dw + 3 * sp + col_w + sp,
        ]
        self._colon_x = ox + 2 * self._dw + 2 * sp
        self._col_w   = col_w
        self._oy      = oy

        self._lock     = threading.Lock()
        self._text     = "    "
        self._segs:    list[set] = [set(), set(), set(), set()]
        self._use_segs = False
        self._colon    = False
        self._dirty    = True

        log.info("Display ready: %dx%d  digit %dx%d  T=%d",
                 self._sw, self._sh, self._dw, self._dh, self._T)

    def show(self, text: str, colon: bool = True):
        """Queue a character display update (thread-safe)."""
        clean = text.replace(":", "").replace(".", "")[:4].ljust(4)
        with self._lock:
            self._text     = clean
            self._colon    = colon
            self._use_segs = False
            self._dirty    = True

    def show_segs(self, segs: list[set], colon: bool = False):
        """Queue an arbitrary segment display update (thread-safe). segs = list of 4 sets."""
        with self._lock:
            self._segs     = list(segs)
            self._colon    = colon
            self._use_segs = True
            self._dirty    = True

    def render(self):
        """Flush pending update to screen. Call from the main thread only."""
        with self._lock:
            if not self._dirty:
                return
            text     = self._text
            segs     = self._segs
            colon    = self._colon
            use_segs = self._use_segs
            self._dirty = False

        self._screen.fill(COLOR_BG)
        if use_segs:
            for i, seg_set in enumerate(segs):
                self._draw_segs_direct(self._dx[i], self._oy, seg_set)
        else:
            for i, ch in enumerate(text):
                self._draw_digit(self._dx[i], self._oy, ch)
        if colon:
            self._draw_colon()
        pygame.display.flip()

    # ------------------------------------------------------------------
    # Private drawing helpers
    # ------------------------------------------------------------------

    def _seg_rects(self, x: int, y: int) -> dict:
        """Build segment-name → pygame rect mapping for a digit at (x, y)."""
        H, W, T, G = self._dh, self._dw, self._T, self._G
        h2 = H // 2
        return {
            'a': (x + T + G,  y,              W - 2*T - 2*G, T       ),
            'b': (x + W - T,  y + T + G,      T,             h2-T-2*G),
            'c': (x + W - T,  y + h2 + G,     T,             h2-T-2*G),
            'd': (x + T + G,  y + H - T,      W - 2*T - 2*G, T       ),
            'e': (x,          y + h2 + G,      T,             h2-T-2*G),
            'f': (x,          y + T + G,       T,             h2-T-2*G),
            'g': (x + T + G,  y + h2 - T//2,  W - 2*T - 2*G, T       ),
        }

    def _draw_digit(self, x: int, y: int, ch: str):
        self._draw_segs_direct(x, y, CHAR_SEGS.get(ch, set()))

    def _draw_segs_direct(self, x: int, y: int, lit: set):
        for seg, rect in self._seg_rects(x, y).items():
            if seg in lit:
                color = COLOR_ON
            elif SHOW_DIM_SEGS:
                color = COLOR_DIM
            else:
                continue
            pygame.draw.rect(self._screen, color, rect, border_radius=2)

    def _draw_colon(self):
        cx = self._colon_x + self._col_w // 2
        r  = max(4, self._T // 2)
        pygame.draw.circle(self._screen, COLOR_ON, (cx, self._oy + self._dh // 3),     r)
        pygame.draw.circle(self._screen, COLOR_ON, (cx, self._oy + 2 * self._dh // 3), r)


# =============================================================================
# RACE-AROUND ANIMATION
# =============================================================================

def _race_animation(display: Display, abort: threading.Event,
                    duration: float = RACE_DURATION):
    """
    Chase a glowing cluster of segments clockwise around all 4 digits simultaneously.
    Runs synchronously on the calling thread; respects abort event for clean cancellation.
    """
    # Clockwise perimeter order (skips middle 'g')
    SEQ  = ['a', 'b', 'c', 'd', 'e', 'f']
    TAIL = 2   # trailing segments in the glow cluster
    n    = len(SEQ)
    steps = int(duration / RACE_STEP_S)

    for i in range(steps):
        if abort.is_set():
            return
        lit = {SEQ[(i - t) % n] for t in range(TAIL + 1)}
        display.show_segs([lit, lit, lit, lit], colon=(i % 6 < 3))
        time.sleep(RACE_STEP_S)


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

    def select(self, dj: int):
        if 1 <= dj <= 6:
            self._sel = dj
            log.info("Selected DJ%d (%d tracks)", dj, len(self._lists[dj]))

    @property
    def selected(self) -> int:
        return self._sel

    def shuffled_tracks(self) -> list:
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
# AUDIO ENGINE
# =============================================================================

class AudioEngine:
    _MUSIC_END = pygame.USEREVENT + 1

    def __init__(self):
        self._ok      = False
        self._volume  = VOLUME_DEFAULT
        self._tracks: list = []
        self._t_idx   = 0
        self._playing = False
        self._paused  = False
        self._lock    = threading.Lock()
        self._done    = threading.Event()
        self._egg_complete_cb = None  # set during easter egg play
        self._counts  = self._load_counts()
        self._save_counter = 0

        for driver in ("pipewire", "pulseaudio", "alsa", "dummy"):
            os.environ["SDL_AUDIODRIVER"] = driver
            try:
                pygame.mixer.quit()
                pygame.mixer.pre_init(44100, -16, 2, 512)
                pygame.mixer.init()
                log.info("Audio driver: %s", driver)
                self._ok = True
                break
            except Exception as exc:
                log.warning("Audio driver '%s' failed: %s", driver, exc)

        if not self._ok:
            log.error("All audio drivers failed — silent mode.")
            return

        pygame.mixer.set_num_channels(8)
        pygame.mixer.music.set_endevent(self._MUSIC_END)
        self._beep_ch  = pygame.mixer.Channel(7)
        self._ding_ch  = pygame.mixer.Channel(6)
        self._beep_snd = self._load(BEEP_SOUND, "beep")
        self._ding_snd = self._load(DING_SOUND, "ding")
        self._apply_volume()

        threading.Thread(target=self._track_manager, name="TrackManager", daemon=True).start()
        log.info("Audio ready (volume=%d%%)", self._volume)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, tracks: list):
        """Start normal playlist playback."""
        if not self._ok or not tracks:
            return
        with self._lock:
            self._tracks          = tracks
            self._t_idx           = 0
            self._playing         = True
            self._paused          = False
            self._egg_complete_cb = None
        self._play(tracks[0])

    def start_easter(self, track: str, on_complete=None):
        """Play a single easter egg clip. on_complete fires (from dispatch thread) when done."""
        if not self._ok:
            if on_complete:
                on_complete()
            return
        with self._lock:
            self._tracks          = [track]
            self._t_idx           = 0
            self._playing         = True
            self._paused          = False
            self._egg_complete_cb = on_complete
        self._play(track)
        log.info("Easter egg track: %s", os.path.basename(track))

    def pause(self):
        if not self._ok:
            return
        with self._lock:
            if not self._playing or self._paused:
                return
            self._paused = True
        pygame.mixer.music.pause()

    def resume(self):
        if not self._ok:
            return
        with self._lock:
            if not self._playing or not self._paused:
                return
            self._paused = False
        pygame.mixer.music.unpause()

    def stop(self):
        with self._lock:
            self._playing         = False
            self._paused          = False
            self._egg_complete_cb = None
        if self._ok:
            pygame.mixer.music.stop()

    def beep(self):
        if self._ok and self._beep_snd and self._beep_ch:
            self._beep_ch.stop()
            self._beep_ch.play(self._beep_snd)

    def ding(self):
        self.stop()
        if self._ok and self._ding_snd and self._ding_ch:
            self._ding_ch.play(self._ding_snd)

    def volume_up(self):
        self._volume = min(100, self._volume + VOLUME_STEP)
        self._apply_volume()
        log.info("Volume → %d%%", self._volume)

    def volume_down(self):
        self._volume = max(0, self._volume - VOLUME_STEP)
        self._apply_volume()
        log.info("Volume → %d%%", self._volume)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    @staticmethod
    def _load(path: str, label: str):
        try:
            snd = pygame.mixer.Sound(path)
            log.info("Loaded %s: %s", label, path)
            return snd
        except Exception as exc:
            log.error("Cannot load %s '%s': %s", label, path, exc)
            return None

    def _load_counts(self) -> dict:
        try:
            with open(PLAYCOUNTS_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_counts(self):
        try:
            with open(PLAYCOUNTS_FILE, 'w') as f:
                json.dump(self._counts, f, indent=2, sort_keys=True)
        except Exception as exc:
            log.warning("Could not save play counts: %s", exc)

    def _play(self, path: str):
        key = os.path.basename(path)
        self._counts[key] = self._counts.get(key, 0) + 1
        self._save_counter += 1
        if self._save_counter >= 5:
            self._save_counts()
            self._save_counter = 0
        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.set_volume(self._volume / 100)
            pygame.mixer.music.play()
            log.info("Playing: %s (play #%d)", key, self._counts[key])
        except Exception as exc:
            log.error("Cannot play '%s': %s — skipping", path, exc)
            self._done.set()

    def notify_music_end(self):
        """Signal that the current track ended. Called from the main thread's event loop."""
        self._done.set()

    def _track_manager(self):
        """Advance playlist on track end; fire callback for easter egg clips."""
        while True:
            self._done.wait()
            self._done.clear()
            with self._lock:
                if not self._playing or self._paused:
                    continue
                egg_cb = self._egg_complete_cb
                if egg_cb:
                    # Easter egg clip finished — signal app, stop music
                    self._egg_complete_cb = None
                    self._playing = False
                    nxt = None
                else:
                    self._t_idx = (self._t_idx + 1) % max(1, len(self._tracks))
                    nxt = self._tracks[self._t_idx]

            if egg_cb:
                egg_cb()
            elif nxt:
                self._play(nxt)

    def shutdown(self):
        """Flush any unsaved play counts — called by app on exit."""
        self._save_counts()

    def _apply_volume(self):
        if self._ok:
            pygame.mixer.music.set_volume(self._volume / 100)


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

    _MAX = 99 * 60 + 59

    def __init__(self):
        self._d         = [0, 0, 0, 0]
        self._typed: list[int] = []   # digits as actually pressed (up to 4)
        self._from_add30 = False      # True when buffer was last set by +30 (not manual digits)

    def push(self, digit: int):
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
# RELAY CONTROLLER  (Arduino via USB serial)
# =============================================================================

class RelayController:
    """Sends DJ selection commands to the Arduino relay board over USB serial.
    Gracefully disabled if Arduino is not connected."""

    def __init__(self, port: str = RELAY_PORT, baud: int = RELAY_BAUD):
        self._lock = threading.Lock()
        self._ser  = None
        if not _SERIAL_AVAILABLE:
            log.warning("pyserial not installed — relay controller disabled.")
            return
        try:
            self._ser = _serial.Serial(port, baud, timeout=1)
            time.sleep(2)          # wait for Arduino to reset after USB connect
            self._ser.reset_input_buffer()
            log.info("Relay controller ready on %s", port)
        except Exception as exc:
            log.warning("Relay controller not available (%s): %s", port, exc)

    def set_dj(self, dj: int) -> None:
        self._send(f"DJ:{dj}")

    def all_off(self) -> None:
        self._send("OFF")

    def close(self) -> None:
        with self._lock:
            if self._ser and self._ser.is_open:
                try:
                    self._ser.write(b"OFF\n")
                    self._ser.flush()
                    self._ser.close()
                except Exception:
                    pass

    def _send(self, cmd: str) -> None:
        with self._lock:
            if not self._ser or not self._ser.is_open:
                return
            try:
                self._ser.write(f"{cmd}\n".encode())
                self._ser.flush()
            except Exception as exc:
                log.warning("Relay send error: %s", exc)


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

        self._dispatch_thread = threading.Thread(target=self._dispatch, name="Dispatch", daemon=True)
        self._dispatch_thread.start()

        pygame.init()
        self.display   = Display()
        self.playlists = PlaylistManager()
        self.audio     = AudioEngine()
        self.timer     = CountdownTimer(
            on_tick   = lambda r: self._post(self._on_tick,   r),
            on_finish = lambda:   self._post(self._on_finish),
        )
        self.buf = TimeEntryBuffer()

        self.relays = RelayController()
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
    # GPIO setup  (lgpio — Pi 5 native)
    # -------------------------------------------------------------------------

    def _setup_gpio(self):
        self._chip     = lgpio.gpiochip_open(0)
        self._btn_map: dict[int, callable] = {}
        self._all_pins: list[int] = []

        def reg(pin: int, fn, *args):
            lgpio.gpio_claim_input(self._chip, pin, lgpio.SET_PULL_UP)
            self._btn_map[pin] = lambda f=fn, a=args: self._post(f, *a)
            self._all_pins.append(pin)

        for dj, pin in PIN_DJ.items():
            reg(pin, self._on_dj, dj)

        for digit, pin in PIN_DIGITS.items():
            reg(pin, self._on_digit, digit)

        for pin, fn in [
            (PIN_START,    self._on_start),
            (PIN_STOP,     self._on_stop),
            (PIN_ADD_30,   self._on_add_30),
            (PIN_VOL_UP,   self._on_vol_up),
            (PIN_VOL_DOWN, self._on_vol_down),
        ]:
            reg(pin, fn)

        lgpio.gpio_claim_input(self._chip, PIN_DOOR, lgpio.SET_PULL_UP)
        self._all_pins.append(PIN_DOOR)

        self._pin_prev = {p: lgpio.gpio_read(self._chip, p) for p in self._all_pins}
        self._pin_dbnc = {p: 0.0 for p in self._all_pins}

        self._door_closed = self._pin_prev[PIN_DOOR] == 0
        self._gpio_stop = threading.Event()
        log.info("GPIO ready (polling @%dHz) — door: %s",
                 GPIO_POLL_HZ, "closed" if self._door_closed else "open")

        threading.Thread(target=self._poll_gpio, name="GPIOPoll", daemon=True).start()

    def _poll_gpio(self):
        interval = 1.0 / GPIO_POLL_HZ
        while not self._gpio_stop.is_set():
            now = time.monotonic()
            for pin in self._all_pins:
                level = lgpio.gpio_read(self._chip, pin)
                if level == self._pin_prev[pin]:
                    continue
                if now - self._pin_dbnc[pin] < DEBOUNCE_S:
                    continue
                self._pin_dbnc[pin] = now
                self._pin_prev[pin] = level

                if pin == PIN_DOOR:
                    if level == 0:
                        self._post(self._on_door_close)
                    else:
                        self._post(self._on_door_open)
                elif level == 0:
                    handler = self._btn_map.get(pin)
                    if handler:
                        handler()

            time.sleep(interval)

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
        if self._state == State.ENTERING_TIME and not self.buf.is_zero():
            self._begin_countdown()
        elif self._state == State.PAUSED:
            self._resume_countdown()

    def _on_stop(self):
        log.info("Button: STOP")
        self.audio.beep()
        self._race_abort.set()   # cancel any running easter egg animation
        self._cancel_door_timer()
        self.timer.stop()
        self.audio.stop()
        self.buf.clear()
        self._state = State.ENTERING_TIME
        self.display.show("0000")
        log.info("Cleared — ready for input.")
        self._start_entry_timer()

    def _on_door_timeout(self):
        log.info("Door left open — auto-cancelled.")
        self._race_abort.set()
        self.timer.stop()
        self.audio.stop()
        self.buf.clear()
        self._state = State.IDLE
        self._show_clock(force=True)

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
        self._state = State.FINISHED
        self.buf.clear()
        self.audio.ding()
        self.display.show("0000")
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

    def _flash_dj(self, dj: int) -> None:
        self.display.show(f"dJ {dj}", colon=False)
        t = threading.Timer(1.5, lambda: self._post(self._restore_display))
        t.daemon = True
        t.start()

    # -------------------------------------------------------------------------
    # Easter egg handlers
    # -------------------------------------------------------------------------

    def _begin_easter_egg(self, egg: dict, tracks: list):
        log.info("Easter egg! '%s' — %ds countdown", egg['folder'], egg['seconds'])
        self._state  = State.ANIMATING
        self._cd_mm  = egg['mm']
        self._race_abort.clear()

        entry_display = self.buf.display_str()  # capture typed digits before buffer clears
        track = random.choice(tracks)
        self.audio.start_easter(
            track,
            on_complete=lambda: self._post(self._on_easter_egg_audio_done)
        )

        egg_secs = egg['seconds']

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
                self._post(self._on_easter_egg_countdown, egg_secs)

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
        self.audio.start(self.playlists.shuffled_tracks())
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
        # Non-zero entries only time out when the door is open — door closed means
        # the user may still be mid-entry and is about to press START.
        if self.buf.is_zero() or not self._door_closed:
            self.buf.clear()
            self._state = State.IDLE
            self._show_clock(force=True)
            log.info("Entry idle timeout — returned to clock.")

    # -------------------------------------------------------------------------
    # Main loop  (runs on main thread — owns pygame event pump and rendering)
    # -------------------------------------------------------------------------

    def run(self):
        log.info("Running — Ctrl+C or Esc to quit.")
        try:
            while True:
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
        self._gpio_stop.set()        # signal poll thread to exit
        time.sleep(0.1)              # let it finish its current iteration
        self._q.put(_STOP_SENTINEL)  # drain dispatch thread cleanly
        self.relays.close()
        try:
            lgpio.gpiochip_close(self._chip)
        except Exception:
            pass
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
