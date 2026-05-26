"""
MicroRave hardware adapters
===========================
Physical-device IO: pygame display, pygame.mixer audio, Arduino relay serial.

These classes were extracted verbatim from microrave.py so that a network
adapter module (adapters_net.py) can substitute for them when MICRORAVE_IO=net.

Public symbols:
  Display            — fullscreen pygame 7-seg renderer (main-thread render())
  _race_animation    — chase-around-the-digits helper used by easter eggs
  AudioEngine        — pygame.mixer-based playlist + beep/ding/easter player
  RelayController    — USB-serial driver for the Arduino DJ relay board
  HardwareInput      — lgpio polled-input adapter (50Hz, debounced)

Constants exported for callers that need them (e.g. startup checks):
  BEEP_SOUND, DING_SOUND
"""

import json
import logging
import os
import threading
import time

try:
    import serial as _serial
    _SERIAL_AVAILABLE = True
except ImportError:
    _SERIAL_AVAILABLE = False

import lgpio
import pygame

log = logging.getLogger("MicroRave.adapters")

# =============================================================================
# CONSTANTS  (moved with the classes that own them)
# =============================================================================

# GPIO input
DEBOUNCE_S        = 0.05  # seconds to ignore re-triggers after a switch edge
GPIO_POLL_HZ      = 50    # GPIO polling rate

# Relay (Arduino USB serial)
RELAY_PORT        = "/dev/ttyACM0"
RELAY_BAUD        = 9600

# Audio
_SOUNDS_DIR       = "sounds"
PLAYCOUNTS_FILE   = "playcounts.json"
BEEP_SOUND        = os.path.join(_SOUNDS_DIR, "beep.mp3")
DING_SOUND        = os.path.join(_SOUNDS_DIR, "ding.mp3")
VOLUME_DEFAULT    = 70    # 0–100
VOLUME_STEP       = 10

# Race animation
RACE_DURATION     = 3.0   # seconds for race-around animation
RACE_STEP_S       = 0.08  # seconds per animation frame

# Display colors
COLOR_BG          = (  0,   0,   0)   # black background
COLOR_ON          = (  0, 255,   0)   # bright green segments
COLOR_DIM         = (  0,  13,   0)   # dim green for unlit segments
SHOW_DIM_SEGS     = True               # show unlit segments (real 7-seg look)

# 7-segment character map
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
# AUDIO ENGINE
# =============================================================================

class AudioEngine:
    _MUSIC_END = pygame.USEREVENT + 1

    def __init__(self):
        self._ok      = False
        self._volume  = VOLUME_DEFAULT
        self._tracks: list = []      # legacy: used only by easter eggs (single-track)
        self._t_idx   = 0
        self._provider = None        # callable() -> next track path, or None to stop
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
                pygame.mixer.pre_init(44100, -16, 2,4096)
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

    def start(self, track_provider):
        """Start normal playlist playback.
        track_provider() is called once now (to get the first track) and again
        on every track-end (to get the next). Returns None to stop playback.
        This lets PlaylistManager bag-shuffle on demand instead of cycling
        through a fixed pre-shuffled list."""
        if not self._ok or not callable(track_provider):
            return
        first = track_provider()
        if not first:
            return
        with self._lock:
            self._tracks          = []   # not used in provider mode
            self._t_idx           = 0
            self._provider        = track_provider
            self._playing         = True
            self._paused          = False
            self._egg_complete_cb = None
        self._play(first)

    def start_easter(self, track: str, on_complete=None):
        """Play a single easter egg clip. on_complete fires (from dispatch thread) when done."""
        if not self._ok:
            if on_complete:
                on_complete()
            return
        with self._lock:
            self._tracks          = [track]
            self._t_idx           = 0
            self._provider        = None     # eggs use the legacy single-track path
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
        self._volume = max(10, self._volume - VOLUME_STEP)
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
                elif self._provider:
                    # Bag-shuffle mode: ask PlaylistManager for the next track
                    nxt = self._provider()
                    if not nxt:
                        self._playing = False
                else:
                    # Legacy: cycle a fixed list (no longer used by normal playback)
                    self._t_idx = (self._t_idx + 1) % max(1, len(self._tracks))
                    nxt = self._tracks[self._t_idx] if self._tracks else None

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
# HARDWARE INPUT  (lgpio polled at GPIO_POLL_HZ)
# =============================================================================

class HardwareInput:
    """Polled GPIO input on Raspberry Pi 5 via lgpio.

    Active-low pull-up wiring (switch closes pin to ground):
      buttons → fire on_press on falling edge (debounced)
      door    → fire on_close on level==0, on_open on level==1

    All callbacks fire from the internal poll thread — callers are responsible
    for marshalling onto whatever thread they need (e.g. a dispatch queue).
    """

    def __init__(self):
        self._chip          = lgpio.gpiochip_open(0)
        self._btn_map: dict[int, callable] = {}
        self._all_pins: list[int] = []
        self._door_pin: int | None = None
        self._door_cb_close = None
        self._door_cb_open  = None
        self._stop          = threading.Event()
        self._thread: threading.Thread | None = None

    def register_button(self, pin: int, on_press) -> None:
        """Claim pin as pull-up input; on_press() fires on falling edge."""
        lgpio.gpio_claim_input(self._chip, pin, lgpio.SET_PULL_UP)
        self._btn_map[pin] = on_press
        self._all_pins.append(pin)

    def register_door(self, pin: int, on_close, on_open) -> None:
        """Claim door pin as pull-up input; on_close/on_open fire on edges."""
        lgpio.gpio_claim_input(self._chip, pin, lgpio.SET_PULL_UP)
        self._door_pin       = pin
        self._door_cb_close  = on_close
        self._door_cb_open   = on_open
        self._all_pins.append(pin)

    def door_closed_initial(self) -> bool:
        """Sync-read the door pin. Call after register_door(), before start()."""
        if self._door_pin is None:
            return True
        return lgpio.gpio_read(self._chip, self._door_pin) == 0

    def start(self) -> None:
        """Snapshot initial pin levels and launch the poll thread."""
        self._pin_prev = {p: lgpio.gpio_read(self._chip, p) for p in self._all_pins}
        self._pin_dbnc = {p: 0.0 for p in self._all_pins}
        self._thread = threading.Thread(target=self._poll, name="GPIOPoll", daemon=True)
        self._thread.start()
        log.info("HardwareInput ready (polling @%dHz, %d pins)",
                 GPIO_POLL_HZ, len(self._all_pins))

    def stop(self) -> None:
        """Signal the poll thread to exit and release the chip."""
        self._stop.set()
        time.sleep(0.1)   # let the current poll iteration finish
        try:
            lgpio.gpiochip_close(self._chip)
        except Exception:
            pass

    def _poll(self) -> None:
        interval = 1.0 / GPIO_POLL_HZ
        last_iter = time.monotonic()
        # Anything >3x our target interval (60ms when polling at 50Hz) is a stall worth logging.
        stall_threshold = interval * 3
        while not self._stop.is_set():
            now = time.monotonic()
            gap = now - last_iter
            if gap > stall_threshold:
                log.warning("GPIO poll stall: %.0fms gap (target %.0fms)",
                            gap * 1000, interval * 1000)
            last_iter = now
            for pin in self._all_pins:
                level = lgpio.gpio_read(self._chip, pin)
                if level == self._pin_prev[pin]:
                    continue
                if now - self._pin_dbnc[pin] < DEBOUNCE_S:
                    continue
                self._pin_dbnc[pin] = now
                self._pin_prev[pin] = level

                if pin == self._door_pin:
                    if level == 0 and self._door_cb_close:
                        self._door_cb_close()
                    elif level == 1 and self._door_cb_open:
                        self._door_cb_open()
                elif level == 0:
                    handler = self._btn_map.get(pin)
                    if handler:
                        handler()

            time.sleep(interval)
