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
    _timing_bonus,
    _league_bonus,
    _hour_bonus,
    _hour_band,
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
        assert get_calibration_factor(1.70, "DNB") == 0.975

    def test_dnb_high_band(self):
        assert get_calibration_factor(2.10, "DNB") == 0.955

    def test_ah_low_band(self):
        assert get_calibration_factor(1.75, "AH") == 0.958

    def test_ou_mid_band(self):
        assert get_calibration_factor(1.90, "OU") == 0.955


# ── estimate_fair_odd ────────────────────────────────────────────────────────

class TestEstimateFairOdd:
    def test_ml_value(self):
        assert estimate_fair_odd(1.65, "ML") == round(1.65 * 0.979, 3)

    def test_dnb_value(self):
        assert estimate_fair_odd(2.10, "DNB") == round(2.10 * 0.955, 3)

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
        # DNB 2.10: factor 0.955, fair ~2.006, edge ~4.7%, min_edge 4.5
        result = is_value_bet(2.10, "DNB")
        assert result is not None
        assert result["edge_pct"] > 4.0
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
        assert result >= 3.0

    def test_with_league(self):
        result = adaptive_min_edge("DNB", 1.90, league="England - Premier League")
        assert isinstance(result, float)


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

# ── _timing_bonus ────────────────────────────────────────────────────────────

class TestTimingBonus:
    def test_no_timing_data(self):
        """Sem dados de timing retorna 0."""
        tracked = [{"clv_real": 5.0, "market": "ML"} for _ in range(20)]
        assert _timing_bonus(tracked) == 0.0

    def test_super_early_strong_clv(self):
        """Super earlys (7d+) com bom CLV devem relaxar o threshold (-0.5)."""
        super_early = [{"clv_real": 8.0, "hours_to_kickoff": 200} for _ in range(10)]
        standard = [{"clv_real": 2.0, "hours_to_kickoff": 55} for _ in range(10)]
        assert _timing_bonus(super_early + standard) == -0.5

    def test_super_early_weak_clv(self):
        """Super earlys com CLV negativo devem apertar (+0.5)."""
        super_early = [{"clv_real": -3.0, "hours_to_kickoff": 200} for _ in range(10)]
        standard = [{"clv_real": 5.0, "hours_to_kickoff": 55} for _ in range(10)]
        assert _timing_bonus(super_early + standard) == 0.5

    def test_few_samples_returns_zero(self):
        """Com poucas amostras, sem ajuste."""
        super_early = [{"clv_real": 8.0, "hours_to_kickoff": 200} for _ in range(3)]
        standard = [{"clv_real": 2.0, "hours_to_kickoff": 55} for _ in range(3)]
        assert _timing_bonus(super_early + standard) == 0.0


# ── _league_bonus ────────────────────────────────────────────────────────────

class TestLeagueBonus:
    def _make_picks(self, league: str, clv: float, n: int = 15) -> list[dict]:
        return [{"league": league, "clv_real": clv} for _ in range(n)]

    def test_no_league(self):
        tracked = self._make_picks("England - Premier League", 5.0)
        assert _league_bonus(tracked, "") == 0.0

    def test_few_samples(self):
        """Menos de 10 picks na liga → sem ajuste."""
        tracked = self._make_picks("England - Premier League", 8.0, n=5)
        assert _league_bonus(tracked, "England - Premier League") == 0.0

    def test_strong_league(self):
        """Liga com CLV >= 6 e beat >= 60% → relaxa -1.0."""
        tracked = [{"league": "EPL", "clv_real": v} for v in
                   [8, 6, 7, 5, 9, 4, 7, 8, 6, 10]]  # avg=7, 100% beat
        assert _league_bonus(tracked, "EPL") == -1.0

    def test_good_league(self):
        """Liga com CLV >= 4 e beat >= 55% → relaxa -0.5."""
        tracked = [{"league": "Liga", "clv_real": v} for v in
                   [5, 4, 3, 6, 4, 5, 3, 4, 5, 4]]  # avg=4.3, 100% beat
        assert _league_bonus(tracked, "Liga") == -0.5

    def test_weak_league(self):
        """Liga com CLV < 0 → aperta +1.0."""
        tracked = [{"league": "Bad", "clv_real": v} for v in
                   [-1, -2, 1, -3, -1, -2, 0, -1, -2, -1]]  # avg=-1.2
        assert _league_bonus(tracked, "Bad") == 1.0

    def test_very_weak_league(self):
        """Liga com CLV < -2 → aperta +1.5."""
        tracked = self._make_picks("Terrible", -3.0, n=12)
        assert _league_bonus(tracked, "Terrible") == 1.5

    def test_neutral_league(self):
        """Liga com CLV ok → sem ajuste."""
        tracked = [{"league": "Neutral", "clv_real": v} for v in
                   [3, 2, 4, 1, 3, 2, 3, 2, 4, 3]]  # avg=2.7, 100% beat
        assert _league_bonus(tracked, "Neutral") == 0.0

    def test_different_league_ignored(self):
        """Picks de outra liga não contam."""
        tracked = self._make_picks("England - Premier League", 8.0, n=20)
        assert _league_bonus(tracked, "Portugal - Liga Portugal") == 0.0


# ── _hour_band ───────────────────────────────────────────────────────────────

class TestHourBand:
    def test_night(self):
        assert _hour_band(3) == "night"

    def test_morning(self):
        assert _hour_band(9) == "morning"

    def test_afternoon(self):
        assert _hour_band(14) == "afternoon"

    def test_evening(self):
        assert _hour_band(21) == "evening"

    def test_boundaries(self):
        assert _hour_band(0) == "night"
        assert _hour_band(5) == "night"
        assert _hour_band(6) == "morning"
        assert _hour_band(11) == "morning"
        assert _hour_band(12) == "afternoon"
        assert _hour_band(17) == "afternoon"
        assert _hour_band(18) == "evening"
        assert _hour_band(23) == "evening"


# ── _hour_bonus ──────────────────────────────────────────────────────────────

class TestHourBonus:
    def _make_picks(self, hour: int, clv: float, n: int = 10) -> list[dict]:
        return [
            {"alerted_at": f"2026-04-01T{hour:02d}:00:00Z", "clv_real": clv}
            for _ in range(n)
        ]

    def test_no_current_hour(self):
        tracked = self._make_picks(3, 5.0)
        assert _hour_bonus(tracked, None) == 0.0

    def test_few_samples(self):
        """Menos de 8 na faixa actual → sem ajuste."""
        tracked = self._make_picks(3, 8.0, n=5)
        other = self._make_picks(14, 2.0, n=15)
        assert _hour_bonus(tracked + other, 3) == 0.0

    def test_strong_band_relaxes(self):
        """Faixa actual com CLV alto vs outras → relaxa -0.5."""
        night = self._make_picks(3, 8.0, n=10)
        afternoon = self._make_picks(14, 2.0, n=10)
        assert _hour_bonus(night + afternoon, 3) == -0.5

    def test_weak_band_tightens(self):
        """Faixa actual com CLV negativo vs outras → aperta +0.5."""
        night = self._make_picks(3, -3.0, n=10)
        afternoon = self._make_picks(14, 5.0, n=10)
        assert _hour_bonus(night + afternoon, 3) == 0.5

    def test_neutral(self):
        """Faixas com CLV semelhante → sem ajuste."""
        night = self._make_picks(3, 4.0, n=10)
        afternoon = self._make_picks(14, 3.0, n=10)
        assert _hour_bonus(night + afternoon, 3) == 0.0


# ── min_kickoff_date ─────────────────────────────────────────────────────────

class TestMinKickoffDate:
    def test_returns_today(self):
        assert min_kickoff_date() == date.today().isoformat()

    def test_format(self):
        result = min_kickoff_date()
        assert len(result) == 10
        assert result[4] == "-" and result[7] == "-"
