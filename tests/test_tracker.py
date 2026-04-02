import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import make_pick_id, Pick, _find_singbet_closing


# ── make_pick_id ─────────────────────────────────────────────────────────────

class TestMakePickId:
    def test_totals_dedup(self):
        """Over e Under do mesmo jogo devem gerar o mesmo pick_id."""
        id_over = make_pick_id("Porto vs Benfica", "Totals", "Over 2.5")
        id_under = make_pick_id("Porto vs Benfica", "Totals", "Under 2.5")
        assert id_over == id_under

    def test_side_markets_dedup(self):
        """ML e DNB do mesmo jogo devem gerar o mesmo pick_id."""
        id_ml = make_pick_id("Porto vs Benfica", "ML", "Porto")
        id_dnb = make_pick_id("Porto vs Benfica", "DNB", "Porto")
        id_spread = make_pick_id("Porto vs Benfica", "Spread", "Porto -0.50")
        assert id_ml == id_dnb == id_spread

    def test_different_games(self):
        id1 = make_pick_id("Porto vs Benfica", "ML", "Porto")
        id2 = make_pick_id("Sporting vs Braga", "ML", "Sporting")
        assert id1 != id2

    def test_deterministic(self):
        id1 = make_pick_id("Porto vs Benfica", "Totals", "Over 2.5")
        id2 = make_pick_id("Porto vs Benfica", "Totals", "Over 2.5")
        assert id1 == id2

    def test_totals_vs_side_different(self):
        id_side = make_pick_id("Porto vs Benfica", "ML", "Porto")
        id_totals = make_pick_id("Porto vs Benfica", "Totals", "Over 2.5")
        assert id_side != id_totals


# ── _find_singbet_closing ────────────────────────────────────────────────────

def _make_pick(**kwargs) -> Pick:
    defaults = dict(
        pick_id="test",
        game="Porto vs Benfica",
        league="Portugal - Liga Portugal",
        league_slug="portugal-liga-portugal",
        home_team="Porto",
        away_team="Benfica",
        market="ML",
        selection="Porto",
        kickoff="10/04/2026 20:00",
        opening_odd=2.00,
        fair_odd=1.90,
        edge_pct=5.0,
        level="Value",
        bet_href="",
        event_id=123,
    )
    defaults.update(kwargs)
    return Pick(**defaults)


class TestFindSingbetClosing:
    def test_ml_home(self):
        pick = _make_pick(market="ML", selection="Porto")
        singbet = {"ML": {"home": 1.85, "away": 2.10}}
        assert _find_singbet_closing(pick, singbet) == 1.85

    def test_ml_away(self):
        pick = _make_pick(market="ML", selection="Benfica")
        singbet = {"ML": {"home": 1.85, "away": 2.10}}
        assert _find_singbet_closing(pick, singbet) == 2.10

    def test_totals_over(self):
        pick = _make_pick(market="Totals", selection="Over 2.5")
        singbet = {"Totals": {"over": 1.90, "under": 2.00}}
        assert _find_singbet_closing(pick, singbet) == 1.90

    def test_totals_under(self):
        pick = _make_pick(market="Totals", selection="Under 2.5")
        singbet = {"Totals": {"over": 1.90, "under": 2.00}}
        assert _find_singbet_closing(pick, singbet) == 2.00

    def test_spread_home(self):
        pick = _make_pick(market="Spread", selection="Porto -0.50")
        singbet = {"Spread": {"home": 1.95, "away": 1.95}}
        assert _find_singbet_closing(pick, singbet) == 1.95

    def test_empty_singbet(self):
        pick = _make_pick(market="ML", selection="Porto")
        assert _find_singbet_closing(pick, {}) is None

    def test_missing_market(self):
        pick = _make_pick(market="ML", selection="Porto")
        singbet = {"Totals": {"over": 1.90}}
        assert _find_singbet_closing(pick, singbet) is None
