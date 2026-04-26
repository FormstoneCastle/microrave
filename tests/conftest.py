"""
Hardware mocks and pytest fixtures for MicroRave tests.

Mocks are installed at module scope so they are in place before any test file
imports microrave (which has top-level `import lgpio` and `import pygame`).
"""
import sys
import os
import tempfile
import threading
from unittest.mock import MagicMock
import pytest

# ── lgpio mock ─────────────────────────────────────────────────────────────────
_lgpio = MagicMock()
_lgpio.SET_PULL_UP        = 0
_lgpio.gpiochip_open.return_value = 99
# gpio_read return_value is set via side_effect below after microrave is imported

# ── pygame mock ────────────────────────────────────────────────────────────────
_pygame = MagicMock()

_pygame.FULLSCREEN = 0x00000001
_pygame.NOFRAME    = 0x00000020
_pygame.QUIT       = 256
_pygame.KEYDOWN    = 768
_pygame.K_ESCAPE   = 27
_pygame.USEREVENT  = 24   # must be int so USEREVENT + 1 works

_display_info            = MagicMock()
_display_info.current_w  = 1920
_display_info.current_h  = 1080
_pygame.display.Info.return_value     = _display_info
_pygame.display.set_mode.return_value = MagicMock()
_pygame.event.get.return_value        = []
_pygame.mixer.Channel.return_value    = MagicMock()
_pygame.mixer.Sound.return_value      = MagicMock()

# Install before any test file is imported
sys.modules['lgpio']  = _lgpio
sys.modules['pygame'] = _pygame

# ── Now safe to import microrave constants ─────────────────────────────────────
import microrave as _mr

# gpio_read side effect:
#   PIN_DOOR → 0  (LOW = door CLOSED = switch engaged)
#   all button pins → 1  (HIGH = not pressed)
# Without this, every app fixture starts with door OPEN and _begin_countdown
# returns immediately, making all countdown tests fail.
def _gpio_read(chip, pin):
    return 0 if pin == _mr.PIN_DOOR else 1

_lgpio.gpio_read.side_effect = _gpio_read

# Redirect playcounts.json to /tmp so tests don't need write access to the
# production file (which may be root-owned when the systemd service has run).
_mr.PLAYCOUNTS_FILE = os.path.join(tempfile.gettempdir(), "mr_test_playcounts.json")


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def app():
    """
    Create a MicroRaveApp, drain the init queue, yield it, then shut down cleanly.
    Tests get a freshly initialised app in IDLE state with door closed.
    """
    a = _mr.MicroRaveApp()
    a._drain()
    yield a
    a._shutdown()
    if a._dispatch_thread and a._dispatch_thread.is_alive():
        a._dispatch_thread.join(timeout=2.0)


@pytest.fixture
def lgpio_mock():
    return _lgpio


@pytest.fixture
def pygame_mock():
    return _pygame
