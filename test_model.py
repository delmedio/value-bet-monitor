import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model import (
    get_calibration_factor,
    estimate_fair_odd,
    calculate_edge,
    is_value_bet,
    adaptive_min_edge,
    base_min_edge,
    ev_level,
    min_kickoff_date,
)


# ── get_calibration_factor ───────────────────────────────────────────────────

class TestGetCalibrationFactor:
    def test_ml_low_band(self):
        assert get_calibration_factor(1.65, "ML") == 0.979

    def test_ml_mid_band(self):
        assert get_calibration_factor(1.90, "ML") == 0.987

    def test_ml_high_band(self):
        assert get_calibration_factor(2.10, "ML") == 0.976

    def test_ml_upper_boundary(self):
        assert get_calibration_factor(2.20, "ML") == 0.976

    def test_below_min_odd(self):
        assert get_calibration_factor(1.40, "ML") is None

    def test_above_max_odd(self):
        assert get_calibration_factor(2.50, "ML") is None

    def test_dnb_low_band(self):
        assert get_calibration_factor(1.70, "DNB") == 0.983

    def test_dnb_high_band(self):
        assert get_calibration_factor(2.10, "DNB") == 0.903

    def test_ah_low_band(self):
        assert get_calibration_factor(1.75, "AH") == 0.958

    def test_ou_mid_band(self):
        assert get_calibration_factor(1.90, "OU") == 0.932


# ── estimate_fair_odd ────────────────────────────────────────────────────────

class TestEstimateFairOdd:
    def test_ml_value(self):
        assert estimate_fair_odd(1.65, "ML") == round(1.65 * 0.979, 3)

    def test_dnb_value(self):
        assert estimate_fair_odd(2.10, "DNB") == round(2.10 * 0.903, 3)

    def test_out_of_range_returns_none(self):
        assert estimate_fair_odd(1.30, "ML") is None

    def test_above_max_returns_none(self):
        assert estimate_fair_odd(3.00, "ML") is None


# ── calculate_edge ───────────────────────────────────────────────────────────

class TestCalculateEdge:
    def test_positive_edge(self):
        assert calculate_edge(2.00, 1.80) == round((2.00 / 1.80 - 1) * 100, 2)

    def test_zero_fair(self):
        assert calculate_edge(2.00, 0) == 0.0

    def test_no_edge(self):
        assert calculate_edge(1.80, 1.80) == 0.0


# ── is_value_bet ─────────────────────────────────────────────────────────────

class TestIsValueBet:
    def test_dnb_high_edge_returns_dict(self):
        # DNB 2.10: factor 0.903, fair ~1.896, edge ~10.76%, min_edge 5.0
        result = is_value_bet(2.10, "DNB")
        assert result is not None
        assert result["edge_pct"] > 5.0
        assert "fair_odd" in result
        assert "level" in result

    def test_ml_low_edge_returns_none(self):
        # ML 1.65: factor 0.979, fair ~1.615, edge ~2.17%, min_edge 7.0
        result = is_value_bet(1.65, "ML")
        assert result is None

    def test_out_of_range_returns_none(self):
        assert is_value_bet(1.30, "ML") is None


# ── adaptive_min_edge ────────────────────────────────────────────────────────

class TestAdaptiveMinEdge:
    def test_no_file_returns_base(self, tmp_path, monkeypatch):
        monkeypatch.setattr("model.LEARNING_PICKS_FILE", tmp_path / "nonexistent.json")
        assert adaptive_min_edge("ML", 1.65) == base_min_edge("ML", 1.65)

    def test_returns_float(self):
        result = adaptive_min_edge("DNB")
        assert isinstance(result, float)
        assert result >= 3.5


# ── ev_level ─────────────────────────────────────────────────────────────────

class TestEvLevel:
    def test_elite(self):
        assert "Elite" in ev_level(25)

    def test_strong(self):
        assert "Strong" in ev_level(17)

    def test_value(self):
        assert "Value" in ev_level(8)

    def test_boundary_elite(self):
        assert "Elite" in ev_level(20)

    def test_boundary_strong(self):
        assert "Strong" in ev_level(15)


# ── min_kickoff_date ─────────────────────────────────────────────────────────

class TestMinKickoffDate:
    def test_returns_today(self):
        assert min_kickoff_date() == date.today().isoformat()

    def test_format(self):
        result = min_kickoff_date()
        assert len(result) == 10
        assert result[4] == "-" and result[7] == "-"
