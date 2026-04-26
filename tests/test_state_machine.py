"""
Integration tests for MicroRaveApp state machine.

All hardware is mocked (see conftest.py).  Events are injected directly into
the dispatch queue via app._post(); app._drain() blocks until the queue is empty.
"""
import time
import threading
import pytest
import microrave
from microrave import State, MicroRaveApp


# ── Helpers ────────────────────────────────────────────────────────────────────

def push_digits(app, *digits):
    for d in digits:
        app._post(app._on_digit, d)
    app._drain()


def start_countdown(app, *digits):
    """Enter digits and press Start, then drain."""
    push_digits(app, *digits)
    app._post(app._on_start)
    app._drain()


# ── Basic state transitions ────────────────────────────────────────────────────

class TestBasicTransitions:

    def test_starts_in_idle(self, app):
        assert app._state == State.IDLE

    def test_digit_moves_to_entering_time(self, app):
        app._post(app._on_digit, 3)
        app._drain()
        assert app._state == State.ENTERING_TIME

    def test_multiple_digits_shift_buffer(self, app):
        push_digits(app, 1, 3, 0)
        assert app.buf.display_str() == "0130"
        assert app.buf.to_seconds() == 90

    def test_stop_from_idle_shows_zero_entry(self, app):
        app._post(app._on_stop)
        app._drain()
        assert app._state == State.ENTERING_TIME
        assert app.buf.display_str() == "0000"

    def test_start_with_zero_does_nothing(self, app):
        app._post(app._on_start)
        app._drain()
        assert app._state == State.IDLE

    def test_start_with_time_begins_countdown(self, app):
        start_countdown(app, 0, 3, 0)
        assert app._state == State.COUNTING_DOWN

    def test_door_open_pauses_countdown(self, app):
        start_countdown(app, 0, 3, 0)
        app._post(app._on_door_open)
        app._drain()
        assert app._state == State.PAUSED

    def test_door_close_resumes_countdown(self, app):
        start_countdown(app, 0, 3, 0)
        app._post(app._on_door_open)
        app._drain()
        app._post(app._on_door_close)
        app._drain()
        assert app._state == State.COUNTING_DOWN

    def test_start_resumes_when_paused(self, app):
        start_countdown(app, 0, 3, 0)
        app._post(app._on_door_open)
        app._drain()
        app._post(app._on_start)
        app._drain()
        assert app._state == State.COUNTING_DOWN

    def test_stop_during_countdown_resets(self, app):
        start_countdown(app, 1, 0, 0)
        app._post(app._on_stop)
        app._drain()
        assert app._state == State.ENTERING_TIME
        assert app.buf.to_seconds() == 0

    def test_stop_during_pause_resets(self, app):
        start_countdown(app, 1, 0, 0)
        app._post(app._on_door_open)
        app._drain()
        app._post(app._on_stop)
        app._drain()
        assert app._state == State.ENTERING_TIME


# ── +30s button ────────────────────────────────────────────────────────────────

class TestAdd30:

    def test_add30_from_idle_starts_30s_countdown(self, app):
        app._post(app._on_add_30)
        app._drain()
        assert app._state == State.COUNTING_DOWN
        assert app.timer.remaining == 30

    def test_add30_during_countdown_adds_time(self, app):
        start_countdown(app, 0, 3, 0)
        before = app.timer.remaining
        app._post(app._on_add_30)
        app._drain()
        after = app.timer.remaining
        assert after >= before + 25   # allow for 1 tick during test

    def test_add30_during_pause_adds_time(self, app):
        start_countdown(app, 0, 3, 0)
        app._post(app._on_door_open)
        app._drain()
        before = app.timer.remaining
        app._post(app._on_add_30)
        app._drain()
        assert app.timer.remaining >= before + 25


# ── DJ selection ────────────────────────────────────────────────────────────────

class TestDjSelect:

    def test_dj_select_changes_playlist(self, app):
        app._post(app._on_dj, 4)
        app._drain()
        assert app.playlists.selected == 4

    def test_dj_select_cycles_all_six(self, app):
        for n in range(1, 7):
            app._post(app._on_dj, n)
            app._drain()
            assert app.playlists.selected == n


# ── Digit press during countdown ───────────────────────────────────────────────

class TestDigitDuringCountdown:

    def test_digit_during_countdown_ignored_by_buffer(self, app):
        start_countdown(app, 0, 3, 0)
        app._post(app._on_digit, 5)
        app._drain()
        # State stays COUNTING_DOWN; digit is not added to the time-entry buffer
        assert app._state == State.COUNTING_DOWN


# ── Easter egg state transitions ───────────────────────────────────────────────

class TestEasterEgg:

    @pytest.fixture
    def egg_dir(self, tmp_path):
        """Create a fake easter egg folder for '069' with a dummy audio file."""
        d = tmp_path / "sounds" / "easter" / "069"
        d.mkdir(parents=True)
        (d / "clip.mp3").write_bytes(b"\x00" * 16)
        return tmp_path

    @pytest.fixture(autouse=True)
    def _patch_egg_dir(self, egg_dir):
        original = microrave.EASTER_EGG_DIR
        microrave.EASTER_EGG_DIR = str(egg_dir / "sounds" / "easter")
        yield
        microrave.EASTER_EGG_DIR = original
        # Leave animation abort + stop to the individual tests

    def test_069_triggers_animating_state(self, app):
        start_countdown(app, 0, 6, 9)
        assert app._state == State.ANIMATING
        # Clean up
        app._race_abort.set()
        app._post(app._on_stop)
        app._drain()

    def test_stop_during_animation_goes_to_entering_time(self, app):
        start_countdown(app, 0, 6, 9)
        assert app._state == State.ANIMATING
        app._post(app._on_stop)
        app._drain()
        assert app._state == State.ENTERING_TIME

    def test_door_open_during_animation_aborts(self, app):
        start_countdown(app, 0, 6, 9)
        assert app._state == State.ANIMATING
        app._post(app._on_door_open)
        app._drain()
        assert app._state == State.ENTERING_TIME

    def test_animation_transitions_to_countdown(self, app):
        """After the full animation completes, state becomes COUNTING_DOWN."""
        start_countdown(app, 0, 6, 9)
        assert app._state == State.ANIMATING
        # Wait for animation: 3 flashes (≈4s) + race (3s) = ≈7s
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if app._state == State.COUNTING_DOWN:
                break
        assert app._state == State.COUNTING_DOWN, \
            f"Expected COUNTING_DOWN after animation, got {app._state.name}"
        app._post(app._on_stop)
        app._drain()

    def test_no_audio_files_falls_through_to_normal_countdown(self, app, tmp_path):
        """When the egg folder is empty, easter egg is skipped — normal countdown."""
        microrave.EASTER_EGG_DIR = str(tmp_path / "nonexistent")
        start_countdown(app, 0, 6, 9)
        assert app._state == State.COUNTING_DOWN   # NOT ANIMATING
        app._post(app._on_stop)
        app._drain()


# ── Rapid-fire robustness ──────────────────────────────────────────────────────

class TestRapidFire:

    def test_100_digit_presses_no_crash(self, app):
        for _ in range(100):
            app._post(app._on_digit, 1)
        app._drain()
        # Should be ENTERING_TIME with a valid (non-crashing) buffer
        assert app._state == State.ENTERING_TIME

    def test_alternating_start_stop_no_crash(self, app):
        for _ in range(20):
            push_digits(app, 0, 3, 0)
            app._post(app._on_start)
            app._drain()
            app._post(app._on_stop)
            app._drain()
        assert app._state == State.ENTERING_TIME

    def test_door_slam_no_crash(self, app):
        start_countdown(app, 0, 3, 0)
        for _ in range(30):
            app._post(app._on_door_open)
            app._post(app._on_door_close)
        app._drain()
        # Should be in a valid state
        assert app._state in (State.COUNTING_DOWN, State.PAUSED)
        app._post(app._on_stop)
        app._drain()
