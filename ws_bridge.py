"""
MicroRave WebSocket Bridge  —  v2.0
=====================================
Runs on the Raspberry Pi alongside microrave.py when testing with the
Windows simulator.  Provides drop-in replacements for gpiozero and
rpi_ws281x so microrave.py needs no changes between hardware and simulator.

How it works
------------
microrave.py starts with --bridge, which causes it to import VirtualButton,
VirtualPixelStrip, etc. from this file instead of the real hardware libs.

  A) Button/door events:
     The simulator sends JSON over WebSocket.  The bridge fires the matching
     VirtualButton callback as if a real GPIO button was pressed.

  B) Display updates:
     VirtualPixelStrip.show() receives the display text and colour directly
     from the strip object (no pixel-decoding needed) and broadcasts it to
     all connected simulator clients.

Protocol (JSON over WebSocket, port 8765)
-----------------------------------------
  Simulator → Pi:
    {"type": "button", "name": "DIGIT_6"}
    {"type": "button", "name": "START"}
    {"type": "door",   "state": "closed"}   # or "open"

  Pi → Simulator:
    {"type": "display", "text": "12:34", "color": [255, 255, 255]}
    {"type": "display", "text": "    ",  "color": [0, 0, 0]}

Notes
-----
- start() is idempotent — safe to call multiple times.
- VirtualPixelStrip uses a side-channel (set_pending) so the bridge always
  knows exactly what text/colour is on screen without decoding pixels.
- The bridge singleton is created at module import time.  microrave.py
  imports it with:  from ws_bridge import bridge, VirtualButton, ...

Requirements (Pi):
    pip install websockets
"""

import asyncio
import json
import logging
import threading
from typing import Callable, Optional, Set

log = logging.getLogger("Bridge")

WS_PORT = 8765


# =============================================================================
# BRIDGE SINGLETON
# =============================================================================

class _Bridge:
    """
    Owns the WebSocket server and the registries of virtual devices.
    Created once at module level; microrave.py calls bridge.start() to
    begin accepting simulator connections.
    """

    def __init__(self):
        self._clients:  Set[object]                    = set()
        self._buttons:  dict[str, "_VirtualButton"]    = {}
        self._strip:    Optional["VirtualPixelStrip"]  = None
        self._loop:     Optional[asyncio.AbstractEventLoop] = None
        self._lock      = threading.Lock()
        self._started   = False

    # ------------------------------------------------------------------
    # Registration  (called by VirtualButton / VirtualPixelStrip.__init__)
    # ------------------------------------------------------------------

    def register_button(self, name: str, btn: "_VirtualButton"):
        with self._lock:
            if name in self._buttons:
                log.warning("Button '%s' re-registered — check for duplicate pins.", name)
            self._buttons[name] = btn
        log.debug("Registered button: %s", name)

    def register_strip(self, strip: "VirtualPixelStrip"):
        with self._lock:
            self._strip = strip

    # ------------------------------------------------------------------
    # Inbound events from simulator
    # ------------------------------------------------------------------

    def handle_button(self, name: str):
        with self._lock:
            btn = self._buttons.get(name)
        if btn:
            log.info("Virtual press: %s", name)
            btn._fire()
        else:
            log.warning("Unknown button: '%s'  (registered: %s)",
                        name, sorted(self._buttons.keys()))

    def handle_door(self, state: str):
        with self._lock:
            btn = self._buttons.get("DOOR")
        if btn:
            log.info("Virtual door: %s", state)
            btn._fire_door(state)
        else:
            log.warning("DOOR button not registered.")

    # ------------------------------------------------------------------
    # Outbound broadcast to all simulator clients
    # ------------------------------------------------------------------

    def broadcast(self, payload: dict):
        """Thread-safe: may be called from any thread."""
        with self._lock:
            loop    = self._loop
            clients = bool(self._clients)
        if not loop or not clients:
            return
        msg = json.dumps(payload)
        asyncio.run_coroutine_threadsafe(self._async_broadcast(msg), loop)

    async def _async_broadcast(self, msg: str):
        dead = set()
        with self._lock:
            clients = set(self._clients)
        for ws in clients:
            try:
                await ws.send(msg)
            except Exception:
                dead.add(ws)
        if dead:
            with self._lock:
                self._clients -= dead

    # ------------------------------------------------------------------
    # WebSocket server
    # ------------------------------------------------------------------

    async def _handler(self, websocket):
        with self._lock:
            self._clients.add(websocket)
        remote = websocket.remote_address
        log.info("Simulator connected: %s", remote)

        # Send current display state to the newly connected client
        with self._lock:
            strip = self._strip
        if strip:
            text, color = strip._pending_text, strip._pending_color
            try:
                await websocket.send(json.dumps({
                    "type":  "display",
                    "text":  text,
                    "color": list(color),
                }))
            except Exception:
                pass

        try:
            async for raw in websocket:
                try:
                    msg   = json.loads(raw)
                    mtype = msg.get("type")
                    if mtype == "button":
                        self.handle_button(msg["name"])
                    elif mtype == "door":
                        self.handle_door(msg["state"])
                    else:
                        log.warning("Unknown message type: %r", mtype)
                except (json.JSONDecodeError, KeyError) as exc:
                    log.warning("Bad WS message: %s — %s", raw[:120], exc)
        except Exception as exc:
            log.info("Simulator disconnected: %s (%s)", remote, exc)
        finally:
            with self._lock:
                self._clients.discard(websocket)

    def start(self):
        """
        Start the WebSocket server in a background thread.
        Idempotent: calling start() more than once is harmless.
        """
        with self._lock:
            if self._started:
                log.debug("Bridge.start() called again — ignoring.")
                return
            self._started = True

        def _run():
            async def _main():
                import websockets.server
                with self._lock:
                    pass  # just to sync before assigning loop
                self._loop = asyncio.get_event_loop()
                try:
                    async with websockets.server.serve(
                        self._handler, "0.0.0.0", WS_PORT
                    ):
                        log.info("WebSocket bridge listening on 0.0.0.0:%d", WS_PORT)
                        await asyncio.Future()   # run forever
                except OSError as exc:
                    log.error(
                        "Bridge failed to bind port %d: %s\n"
                        "  Run:  sudo fuser -k %d/tcp  then restart.",
                        WS_PORT, exc, WS_PORT
                    )
                    with self._lock:
                        self._started = False   # allow retry

            asyncio.run(_main())

        t = threading.Thread(target=_run, name="WSBridge", daemon=True)
        t.start()
        log.info("Bridge thread started.")


# Module-level singleton
bridge = _Bridge()


# =============================================================================
# VIRTUAL BUTTON  —  drop-in for gpiozero.Button
# =============================================================================

class VirtualButton:
    """
    Mimics gpiozero.Button.

    The bridge maps GPIO pin numbers to button names using the same pin
    constants defined in microrave.py.  The mapping is defined here so this
    file stays self-contained.

    IMPORTANT: every pin must appear at most once across this map and all
    other pin constants.  microrave.py's _assert_unique_pins() catches
    collisions at startup.
    """

    _PIN_NAMES: dict[int, str] = {
        # Digit keys  (individual mode)
         4: "DIGIT_1",   5: "DIGIT_2",   6: "DIGIT_3",
        12: "DIGIT_4",  13: "DIGIT_5",  16: "DIGIT_6",
        17: "DIGIT_7",  18: "DIGIT_8",  19: "DIGIT_9",
        20: "DIGIT_0",
        # Control buttons
         8: "START",
         9: "CANCEL",
        10: "ADD_30",
        11: "PLAYLIST_PREV",
        14: "PLAYLIST_NEXT",
        15: "VOLUME_UP",
        25: "VOLUME_DOWN",   # NOTE: 16 is taken by DIGIT_6 in individual mode
        # Door
         7: "DOOR",
    }

    def __init__(self, pin: int, pull_up: bool = True,
                 bounce_time: float = 0.05, active_state=None):
        self.pin           = pin
        self.is_pressed    = False
        self.when_pressed:  Optional[Callable] = None
        self.when_released: Optional[Callable] = None
        name = self._PIN_NAMES.get(pin, f"GPIO_{pin}")
        bridge.register_button(name, self)

    def _fire(self):
        """Simulate a button press (called by bridge from WS thread)."""
        self.is_pressed = True
        if callable(self.when_pressed):
            threading.Thread(
                target=self.when_pressed, daemon=True
            ).start()
        self.is_pressed = False

    def _fire_door(self, state: str):
        """Simulate door open or close."""
        if state == "closed":
            self.is_pressed = True
            if callable(self.when_pressed):
                threading.Thread(
                    target=self.when_pressed, daemon=True
                ).start()
        else:  # "open"
            self.is_pressed = False
            if callable(self.when_released):
                threading.Thread(
                    target=self.when_released, daemon=True
                ).start()


# =============================================================================
# VIRTUAL OUTPUT DEVICE  —  drop-in for gpiozero.OutputDevice (matrix rows)
# =============================================================================

class VirtualOutputDevice:
    """No-op shim.  Matrix row pins are not used in virtual mode."""
    def __init__(self, pin: int, initial_value: bool = True):
        self.value = initial_value
    def on(self):  self.value = True
    def off(self): self.value = False


# =============================================================================
# VIRTUAL PIXEL STRIP  —  drop-in for rpi_ws281x.PixelStrip
# =============================================================================

class VirtualColor:
    """Mimics rpi_ws281x.Color."""
    __slots__ = ("r", "g", "b")

    def __init__(self, r: int = 0, g: int = 0, b: int = 0):
        self.r = r
        self.g = g
        self.b = b

    def __iter__(self):
        return iter((self.r, self.g, self.b))

    def __repr__(self):
        return f"Color({self.r},{self.g},{self.b})"


def Color(r: int, g: int, b: int) -> VirtualColor:   # noqa: N802
    """Matches the rpi_ws281x.Color API."""
    return VirtualColor(r, g, b)


class VirtualPixelStrip:
    """
    Mimics rpi_ws281x.PixelStrip.

    Uses a side-channel (set_pending) rather than decoding pixel state.
    microrave.py's Display.show() calls strip.show() after setting pixels;
    the Display class also calls set_pending() before show() so the bridge
    always knows exactly what text and colour are being displayed.

    Because microrave.py v2.0 does NOT call set_pending directly, the strip
    decodes the pixel state itself — but only for the simple 0-9 / space
    characters that the display actually uses.  This is reliable because the
    character set is small and unambiguous.
    """

    def __init__(self, num: int, pin: int, freq_hz: int = 800_000, dma: int = 10,
                 invert: bool = False, brightness: int = 255, channel: int = 0):
        self._num        = num
        self._pixels     = [VirtualColor() for _ in range(num)]
        self._brightness = brightness
        # Pending state is updated on every show() so a newly connected
        # simulator immediately gets the current display.
        self._pending_text:  str   = "0:00"
        self._pending_color: tuple = (255, 255, 255)
        bridge.register_strip(self)

    def begin(self):
        log.info("VirtualPixelStrip ready (%d pixels).", self._num)

    def numPixels(self) -> int:
        return self._num

    def setPixelColor(self, i: int, color):
        if not (0 <= i < self._num):
            return
        if isinstance(color, VirtualColor):
            self._pixels[i] = color
        else:
            # rpi_ws281x Color is packed as 0x00RRGGBB
            self._pixels[i] = VirtualColor(
                (color >> 16) & 0xFF,
                (color >>  8) & 0xFF,
                 color        & 0xFF,
            )

    def setBrightness(self, b: int):
        self._brightness = b

    def show(self):
        """Decode pixel state and broadcast to all simulators."""
        text, color = self._decode()
        self._pending_text  = text
        self._pending_color = color
        bridge.broadcast({
            "type":  "display",
            "text":  text,
            "color": list(color),
        })

    # ------------------------------------------------------------------

    def _decode(self) -> tuple[str, tuple]:
        """
        Read pixel colours back into display text and active colour.

        Uses the same SEGMENT_ORDER and CHAR_SEGMENTS as microrave.py.
        Because microrave.py is already imported when this runs (it called
        show()), importing its constants here is safe and avoids duplication.
        """
        from microrave import (
            NUM_DIGITS, LEDS_PER_SEGMENT, SEGMENT_ORDER, CHAR_SEGMENTS
        )

        # Build reverse map: frozenset(lit segment names) → character.
        # Explicitly set space for the empty-set case so it always wins
        # over any other character that might also map to no segments.
        seg_to_char: dict[frozenset, str] = {
            frozenset(segs): ch for ch, segs in CHAR_SEGMENTS.items()
        }
        seg_to_char[frozenset()] = " "

        text        = ""
        color       = (255, 255, 255)
        found_color = False

        for digit_pos in range(NUM_DIGITS):
            base     = digit_pos * 7 * LEDS_PER_SEGMENT
            lit_segs = set()

            for seg_i, seg_name in enumerate(SEGMENT_ORDER):
                px = self._pixels[base + seg_i * LEDS_PER_SEGMENT]
                if px.r > 10 or px.g > 10 or px.b > 10:
                    lit_segs.add(seg_name)
                    if not found_color:
                        color       = (px.r, px.g, px.b)
                        found_color = True

            # Unknown segment pattern → blank (avoids grey rendering artifact)
            char  = seg_to_char.get(frozenset(lit_segs), " ")
            text += char

        # Re-insert colon only for standard MM:SS time strings.
        # (4 chars, first two are digits, last two are digits)
        if (len(text) == 4
                and text[0].isdigit() and text[1].isdigit()
                and text[2].isdigit() and text[3].isdigit()):
            text = text[:2] + ":" + text[2:]

        return text, color


# =============================================================================
# STANDALONE ENTRY POINT
# =============================================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log.info("Starting bridge standalone on port %d …", WS_PORT)
    bridge.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        log.info("Bridge stopped.")
