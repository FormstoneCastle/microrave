"""Unit tests for CountdownTimer."""
import threading
import time
import pytest
from microrave import CountdownTimer


def make_timer():
    ticks    = []
    finished = threading.Event()
    timer    = CountdownTimer(
        on_tick   = lambda r: ticks.append(r),
        on_finish = finished.set,
    )
    return timer, ticks, finished


class TestCountdownTimer:

    def test_start_fires_ticks_descending(self):
        timer, ticks, finished = make_timer()
        timer.start(2)
        finished.wait(timeout=4.0)
        timer.stop()
        assert 2 in ticks
        assert 1 in ticks
        assert 0 in ticks

    def test_start_fires_finish_event(self):
        timer, ticks, finished = make_timer()
        timer.start(2)
        assert finished.wait(timeout=4.0), "on_finish should fire within 4s for a 2s timer"
        timer.stop()

    def test_remaining_decreases_over_time(self):
        timer, _, _ = make_timer()
        timer.start(10)
        time.sleep(1.5)
        r = timer.remaining
        timer.stop()
        assert r <= 9, f"Expected remaining ≤ 9 after 1.5s, got {r}"

    def test_pause_stops_ticking(self):
        timer, ticks, _ = make_timer()
        timer.start(10)
        time.sleep(1.2)
        count_before = len(ticks)
        timer.pause()
        time.sleep(2.0)
        count_after = len(ticks)
        timer.stop()
        assert count_after == count_before, (
            f"Ticks should stop while paused: before={count_before}, after={count_after}"
        )

    def test_resume_continues_ticking(self):
        timer, ticks, _ = make_timer()
        timer.start(10)
        time.sleep(1.2)
        timer.pause()
        count_at_pause = len(ticks)
        time.sleep(1.5)
        timer.resume()
        time.sleep(1.2)
        count_after_resume = len(ticks)
        timer.stop()
        assert count_after_resume > count_at_pause, "Ticks should resume after resume()"

    def test_stop_prevents_finish(self):
        timer, _, finished = make_timer()
        timer.start(5)
        time.sleep(1.2)
        timer.stop()
        fired = finished.wait(timeout=0.5)
        assert not fired, "on_finish must not fire after stop()"

    def test_add_increases_remaining(self):
        timer, _, _ = make_timer()
        timer.start(3)
        time.sleep(0.3)
        before = timer.remaining
        timer.add(10)
        after = timer.remaining
        timer.stop()
        assert after > before, f"add(10) should increase remaining: before={before}, after={after}"

    def test_accuracy_3_second_countdown(self):
        timer, _, finished = make_timer()
        t0 = time.monotonic()
        timer.start(3)
        assert finished.wait(timeout=5.0)
        elapsed = time.monotonic() - t0
        timer.stop()
        assert 2.7 <= elapsed <= 3.5, f"3s countdown took {elapsed:.2f}s — outside ±0.3s window"

    def test_stop_then_restart_works(self):
        timer, ticks, finished = make_timer()
        timer.start(5)
        time.sleep(1.2)
        timer.stop()
        ticks.clear()
        finished.clear()
        timer.start(2)
        assert finished.wait(timeout=4.0), "Restarted timer should finish"
        timer.stop()
        assert 0 in ticks
