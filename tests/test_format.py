"""
Tests for _fmt_countdown display logic and EASTER_EGGS config consistency.
"""
import pytest
from microrave import EASTER_EGGS, MicroRaveApp


# ── _fmt_countdown ─────────────────────────────────────────────────────────────

class TestFmtCountdown:

    @pytest.fixture(autouse=True)
    def _setup(self, app):
        self.app = app

    def fmt(self, cd_mm: int, remaining: int) -> str:
        self.app._cd_mm = cd_mm
        return self.app._fmt_countdown(remaining)

    def test_normal_2m30s(self):
        assert self.fmt(2, 150) == "0230"

    def test_normal_1m00s(self):
        assert self.fmt(1, 60) == "0100"

    def test_zero(self):
        assert self.fmt(0, 0) == "0000"

    def test_069_starts_at_0069(self):
        assert self.fmt(0, 69) == "0069"

    def test_069_at_60_shows_0060(self):
        """Remaining=60, mm=0 → still above mm*60=0, so displays 0:60."""
        assert self.fmt(0, 60) == "0060"

    def test_069_normalises_below_60(self):
        assert self.fmt(0, 59) == "0059"

    def test_666_starts_at_0666(self):
        # 6:66 = 426 seconds, mm=6
        assert self.fmt(6, 426) == "0666"

    def test_666_preserves_mm6_above_360(self):
        assert self.fmt(6, 400) == "0640"

    def test_666_normalises_at_360(self):
        # remaining == mm*60: normalise
        assert self.fmt(6, 360) == "0600"

    def test_8008_starts_at_8008(self):
        # 80:08 = 4808 seconds, mm=80
        assert self.fmt(80, 4808) == "8008"

    def test_8008_above_4800_keeps_mm80(self):
        assert self.fmt(80, 4820) == "8020"

    def test_large_remaining_mm_clamped_at_99(self):
        # 6000s / 60 = 100 min — min(m, 99) should clamp
        self.app._cd_mm = 99
        result = self.app._fmt_countdown(6000)
        m = int(result[:2])
        assert m <= 99


# ── EASTER_EGGS config validation ──────────────────────────────────────────────

class TestEasterEggConfig:

    def test_all_eggs_have_required_keys(self):
        for key, egg in EASTER_EGGS.items():
            for field in ("folder", "seconds", "mm", "ss"):
                assert field in egg, f"Egg '{key}' missing '{field}'"

    def test_no_duplicate_folders(self):
        folders = [egg["folder"] for egg in EASTER_EGGS.values()]
        assert len(folders) == len(set(folders)), "Duplicate folder names"

    def test_all_seconds_positive(self):
        for key, egg in EASTER_EGGS.items():
            assert egg["seconds"] > 0, f"Egg '{key}' seconds must be > 0"

    def test_all_mm_ss_non_negative(self):
        for key, egg in EASTER_EGGS.items():
            assert egg["mm"] >= 0, f"Egg '{key}' mm must be >= 0"
            assert egg["ss"] >= 0, f"Egg '{key}' ss must be >= 0"

    def test_007_is_1m42s(self):
        assert EASTER_EGGS["007"]["seconds"] == 102    # 1*60 + 42

    def test_42_is_3m33s(self):
        assert EASTER_EGGS["42"]["seconds"] == 213     # 3*60 + 33

    def test_069_is_69s(self):
        assert EASTER_EGGS["069"]["seconds"] == 69

    def test_420_is_4m20s(self):
        assert EASTER_EGGS["420"]["seconds"] == 260    # 4*60 + 20

    def test_666_is_426s(self):
        assert EASTER_EGGS["666"]["seconds"] == 426    # 6*60 + 66 (overflow display)

    def test_67_is_67s(self):
        assert EASTER_EGGS["67"]["seconds"] == 67

    def test_6767_overrides_to_67s(self):
        """Typing 6767 ignores the raw 67:67 time and maps to 67 seconds."""
        egg = EASTER_EGGS["6767"]
        assert egg["seconds"] == 67
        assert egg["mm"]      == 0
        assert egg["ss"]      == 67

    def test_8008_is_80m08s(self):
        assert EASTER_EGGS["8008"]["seconds"] == 4808  # 80*60 + 8

    def test_trigger_42_not_042(self):
        """The key '42' exists; '042' does not — typing 4-2 is the trigger."""
        assert "42"  in EASTER_EGGS
        assert "042" not in EASTER_EGGS

    @pytest.mark.parametrize("key", EASTER_EGGS.keys())
    def test_folder_name_is_nonempty_string(self, key):
        assert isinstance(EASTER_EGGS[key]["folder"], str)
        assert len(EASTER_EGGS[key]["folder"]) > 0
