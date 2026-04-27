import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracker import (
    make_pick_id, Pick, _find_bookmaker_closing, _find_best_closing, _derive_dnb_from_ml,
    _parse_hdp_from_selection, _parse_line_from_selection, timing_band,
    filter_report_picks, report_since_dt,
)


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


# ── _find_sbobet_closing ─────────────────────────────────────────────────────

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


class TestFindClosing:
    def test_ml_home(self):
        pick = _make_pick(market="ML", selection="Porto")
        bookmaker = {"ML": {"home": 1.85, "away": 2.10}}
        assert _find_bookmaker_closing(pick, bookmaker) == 1.85

    def test_ml_away(self):
        pick = _make_pick(market="ML", selection="Benfica")
        bookmaker = {"ML": {"home": 1.85, "away": 2.10}}
        assert _find_bookmaker_closing(pick, bookmaker) == 2.10

    def test_ml_string_odds(self):
        """Sbobet/Stake devolvem odds como strings."""
        pick = _make_pick(market="ML", selection="Porto")
        bookmaker = {"ML": {"home": "2.510", "draw": "2.790", "away": "3.080"}}
        assert _find_bookmaker_closing(pick, bookmaker) == 2.51

    def test_dnb_direct(self):
        """DNB usa Draw No Bet da API, não ML."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {
            "ML": {"home": 2.50, "away": 3.00, "draw": 3.20},
            "Draw No Bet": {"home": 1.65, "away": 2.25},
        }
        assert _find_bookmaker_closing(pick, bookmaker) == 1.65

    def test_dnb_stake_format(self):
        """Stake tem Draw No Bet como mercado directo com strings."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {
            "ML": {"home": "2.440", "draw": "2.800", "away": "3.200"},
            "Draw No Bet": {"home": "1.670", "away": "2.170"},
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 1.67) < 0.01

    def test_dnb_fallback_spread_ah0(self):
        """DNB fallback para Spread AH 0."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {
            "ML": {"home": 2.50, "away": 3.00, "draw": 3.20},
            "Spread": {"home": 1.67, "away": 2.41, "hdp": 0},
        }
        assert _find_bookmaker_closing(pick, bookmaker) == 1.67

    def test_dnb_sbobet_ah0_in_spread_all(self):
        """Sbobet sem DNB mas com AH 0 no Spread_all — primeira linha pode não ser hdp 0."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {
            "ML": {"home": "2.510", "draw": "2.790", "away": "3.080"},
            "Spread": {"hdp": 0, "home": "1.670", "away": "2.330"},
            "Spread_all": [
                {"hdp": 0, "home": "1.670", "away": "2.330"},
                {"hdp": -0.5, "home": "2.510", "away": "1.580"},
                {"hdp": -0.25, "home": "2.040", "away": "1.880"},
            ],
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 1.67) < 0.01

    def test_dnb_sbobet_ah0_not_first_line(self):
        """Sbobet com AH 0 não como primeira linha do Spread."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {
            "ML": {"home": "2.510", "draw": "2.790", "away": "3.080"},
            "Spread": {"hdp": -0.5, "home": "2.510", "away": "1.580"},
            "Spread_all": [
                {"hdp": -0.5, "home": "2.510", "away": "1.580"},
                {"hdp": 0, "home": "1.670", "away": "2.330"},
                {"hdp": -0.25, "home": "2.040", "away": "1.880"},
            ],
        }
        # Sem DNB directo e Spread principal é -0.5, mas _all tem hdp 0
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 1.67) < 0.01

    def test_dnb_without_dnb_or_ah0_returns_none(self):
        """Sem DNB/AH0 real da API, o fecho fica pendente."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {"ML": {"home": 2.00, "away": 4.00, "draw": 3.50}}
        assert _find_bookmaker_closing(pick, bookmaker) is None

    def test_dnb_not_ml(self):
        """Verifica que DNB NÃO usa a odd ML directa."""
        pick = _make_pick(market="DNB", selection="Porto")
        bookmaker = {"ML": {"home": 2.50, "away": 3.00, "draw": 3.20}}
        assert _find_bookmaker_closing(pick, bookmaker) is None

    def test_totals_over(self):
        pick = _make_pick(market="Totals", selection="Over 2.5")
        bookmaker = {"Totals": {"over": 1.90, "under": 2.00, "max": 2.5}}
        assert _find_bookmaker_closing(pick, bookmaker) == 1.90

    def test_totals_under(self):
        pick = _make_pick(market="Totals", selection="Under 2.5")
        bookmaker = {"Totals": {"over": 1.90, "under": 2.00, "max": 2.5}}
        assert _find_bookmaker_closing(pick, bookmaker) == 2.00

    def test_totals_sbobet_hdp_key(self):
        """Sbobet/Stake usam 'hdp' em vez de 'max' nos Totals."""
        pick = _make_pick(market="Totals", selection="Over 2.0")
        bookmaker = {"Totals": {"hdp": 2, "over": "2.140", "under": "1.770"}}
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 2.14) < 0.01

    def test_totals_sbobet_hdp_all_lines(self):
        """Sbobet Totals com múltiplas linhas e chave hdp."""
        pick = _make_pick(market="Totals", selection="Over 1.75")
        bookmaker = {
            "Totals": {"hdp": 1.75, "over": "1.820", "under": "2.080"},
            "Totals_all": [
                {"hdp": 1.75, "over": "1.820", "under": "2.080"},
                {"hdp": 2, "over": "2.140", "under": "1.770"},
                {"hdp": 1.5, "over": "1.640", "under": "2.350"},
            ],
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 1.82) < 0.01

    def test_totals_stake_find_specific_line(self):
        """Stake Totals — encontra linha 2.25 nas múltiplas linhas."""
        pick = _make_pick(market="Totals", selection="Under 2.25")
        bookmaker = {
            "Totals": {"hdp": 1.75, "over": "1.770", "under": "2.020"},
            "Totals_all": [
                {"hdp": 1.75, "over": "1.770", "under": "2.020"},
                {"hdp": 2, "over": "2.100", "under": "1.710"},
                {"hdp": 2.25, "over": "2.470", "under": "1.530"},
                {"hdp": 2.5, "over": "2.850", "under": "1.420"},
            ],
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 1.53) < 0.01

    def test_spread_home_hdp_match(self):
        """Spread devolve odd quando hdp corresponde."""
        pick = _make_pick(market="Spread", selection="Porto -0.50")
        bookmaker = {"Spread": {"home": 1.95, "away": 1.95, "hdp": -0.5}}
        assert _find_bookmaker_closing(pick, bookmaker) == 1.95

    def test_spread_hdp_mismatch(self):
        """Spread rejeita quando hdp não corresponde."""
        pick = _make_pick(market="Spread", selection="Porto -0.25")
        bookmaker = {"Spread": {"home": 1.95, "away": 1.95, "hdp": -0.5}}
        assert _find_bookmaker_closing(pick, bookmaker) is None

    def test_spread_all_lines_match(self):
        """Spread encontra linha correcta em múltiplas linhas."""
        pick = _make_pick(market="Spread", selection="Porto -0.25")
        bookmaker = {
            "Spread": {"home": 1.95, "away": 1.95, "hdp": -0.5},
            "Spread_all": [
                {"home": 1.95, "away": 1.95, "hdp": -0.5},
                {"home": 1.87, "away": 2.03, "hdp": -0.25},
            ],
        }
        assert _find_bookmaker_closing(pick, bookmaker) == 1.87

    def test_spread_sbobet_string_odds(self):
        """Sbobet Spread com odds como strings e múltiplas linhas."""
        pick = _make_pick(market="Spread", selection="Porto -0.25")
        bookmaker = {
            "Spread": {"hdp": 0, "home": "1.670", "away": "2.330"},
            "Spread_all": [
                {"hdp": 0, "home": "1.670", "away": "2.330"},
                {"hdp": -0.5, "home": "2.510", "away": "1.580"},
                {"hdp": -0.25, "home": "2.040", "away": "1.880"},
            ],
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 2.04) < 0.01

    def test_spread_stake_quarter_line(self):
        """Stake Spread com quarter lines e strings."""
        pick = _make_pick(market="Spread", selection="Porto -0.75")
        bookmaker = {
            "Spread": {"hdp": -0.75, "home": "3.000", "away": "1.380"},
            "Spread_all": [
                {"hdp": -0.75, "home": "3.000", "away": "1.380"},
                {"hdp": 0, "home": "1.670", "away": "2.170"},
                {"hdp": -0.25, "home": "2.050", "away": "1.740"},
                {"hdp": 0.25, "home": "1.460", "away": "2.650"},
                {"hdp": -0.5, "home": "2.440", "away": "1.540"},
            ],
        }
        assert abs(_find_bookmaker_closing(pick, bookmaker) - 3.0) < 0.01

    def test_totals_line_match(self):
        """Totals devolve odd quando line corresponde."""
        pick = _make_pick(market="Totals", selection="Over 2.5")
        bookmaker = {"Totals": {"over": 1.90, "under": 2.00, "max": 2.5}}
        assert _find_bookmaker_closing(pick, bookmaker) == 1.90

    def test_totals_line_mismatch(self):
        """Totals rejeita quando line não corresponde."""
        pick = _make_pick(market="Totals", selection="Over 2.75")
        bookmaker = {"Totals": {"over": 1.90, "under": 2.00, "max": 2.5}}
        assert _find_bookmaker_closing(pick, bookmaker) is None

    def test_totals_all_lines_match(self):
        """Totals encontra linha correcta em múltiplas linhas."""
        pick = _make_pick(market="Totals", selection="Over 2.75")
        bookmaker = {
            "Totals": {"over": 1.90, "under": 2.00, "max": 2.5},
            "Totals_all": [
                {"over": 1.90, "under": 2.00, "max": 2.5},
                {"over": 2.05, "under": 1.85, "max": 2.75},
            ],
        }
        assert _find_bookmaker_closing(pick, bookmaker) == 2.05

    def test_empty_bookmaker(self):
        pick = _make_pick(market="ML", selection="Porto")
        assert _find_bookmaker_closing(pick, {}) is None

    def test_missing_market(self):
        pick = _make_pick(market="ML", selection="Porto")
        bookmaker = {"Totals": {"over": 1.90}}
        assert _find_bookmaker_closing(pick, bookmaker) is None

    def test_best_closing_prefers_higher_odd(self):
        pick = _make_pick(market="DNB", selection="Porto")
        feeds = {
            "Sbobet": {"Spread": {"home": 1.64, "away": 2.38, "hdp": 0}},
            "Stake": {"Spread": {"home": 1.67, "away": 2.17, "hdp": 0}},
        }
        assert _find_best_closing(pick, feeds) == ("Stake", 1.67)

    def test_best_closing_sbobet_ah0_vs_stake_dnb(self):
        """Sbobet tem Spread hdp 0, Stake tem Draw No Bet — ambos válidos para DNB."""
        pick = _make_pick(market="DNB", selection="Porto")
        feeds = {
            "Sbobet": {
                "ML": {"home": "2.510", "draw": "2.790", "away": "3.080"},
                "Spread": {"hdp": 0, "home": "1.670", "away": "2.330"},
            },
            "Stake": {
                "ML": {"home": "2.440", "draw": "2.800", "away": "3.200"},
                "Draw No Bet": {"home": "1.670", "away": "2.170"},
            },
        }
        result = _find_best_closing(pick, feeds)
        assert result is not None
        bookmaker, odd = result
        assert abs(odd - 1.67) < 0.01

    def test_best_closing_totals_across_bookmakers(self):
        """Melhor odd de Totals entre Sbobet e Stake."""
        pick = _make_pick(market="Totals", selection="Over 2.0")
        feeds = {
            "Sbobet": {
                "Totals": {"hdp": 2, "over": "2.140", "under": "1.770"},
                "Totals_all": [
                    {"hdp": 1.75, "over": "1.820", "under": "2.080"},
                    {"hdp": 2, "over": "2.140", "under": "1.770"},
                ],
            },
            "Stake": {
                "Totals": {"hdp": 1.75, "over": "1.770", "under": "2.020"},
                "Totals_all": [
                    {"hdp": 1.75, "over": "1.770", "under": "2.020"},
                    {"hdp": 2, "over": "2.100", "under": "1.710"},
                ],
            },
        }
        bookmaker, odd = _find_best_closing(pick, feeds)
        # Sbobet over 2.0 = 2.140, Stake over 2.0 = 2.100 → Sbobet ganha
        assert bookmaker == "Sbobet"
        assert abs(odd - 2.14) < 0.01


class TestParseSelections:
    def test_hdp_negative(self):
        assert _parse_hdp_from_selection("Porto -0.50") == -0.50

    def test_hdp_positive(self):
        assert _parse_hdp_from_selection("Porto +0.25") == 0.25

    def test_hdp_no_number(self):
        assert _parse_hdp_from_selection("Porto") is None

    def test_line_over(self):
        assert _parse_line_from_selection("Over 2.5") == 2.5

    def test_line_under(self):
        assert _parse_line_from_selection("Under 2.75") == 2.75

    def test_line_no_number(self):
        assert _parse_line_from_selection("Over") is None


class TestDeriveDnbFromMl:
    def test_basic(self):
        ml = {"home": 2.00, "away": 4.00, "draw": 3.50}
        result = _derive_dnb_from_ml(ml, "home")
        # p_home=0.5, p_away=0.25, p_dnb=0.5/0.75=0.667 → odd=1.5
        assert result is not None
        assert abs(result - 1.5) < 0.01

    def test_away(self):
        ml = {"home": 2.00, "away": 4.00, "draw": 3.50}
        result = _derive_dnb_from_ml(ml, "away")
        # p_away=0.25, p_dnb_away=0.25/0.75=0.333 → odd=3.0
        assert result is not None
        assert abs(result - 3.0) < 0.01

    def test_missing_draw(self):
        ml = {"home": 2.00, "away": 4.00}
        assert _derive_dnb_from_ml(ml, "home") is None

    def test_invalid_odds(self):
        ml = {"home": 0, "away": 0, "draw": 0}
        assert _derive_dnb_from_ml(ml, "home") is None


# ── timing_band ─────────────────────────────────────────────────────────────

class TestTimingBand:
    def test_super_early_14d(self):
        assert timing_band(400.0) == "14d+"

    def test_14d_boundary(self):
        assert timing_band(336.0) == "14d+"

    def test_early_7_14d(self):
        assert timing_band(200.0) == "7-14d"

    def test_7d_boundary(self):
        assert timing_band(168.0) == "7-14d"

    def test_3_7d(self):
        assert timing_band(96.0) == "3-7d"

    def test_48_72h(self):
        assert timing_band(50.0) == "48-72h"

    def test_48h_boundary(self):
        assert timing_band(48.0) == "48-72h"

    def test_below_48h(self):
        assert timing_band(30.0) == "<48h"

    def test_none(self):
        assert timing_band(None) == "unknown"


# ── Pick timing fields ──────────────────────────────────────────────────────

class TestPickTimingFields:
    def test_pick_with_timing(self):
        pick = _make_pick(
            first_seen_at="2026-04-02T10:00:00Z",
            alerted_at="2026-04-02T10:00:00Z",
            hours_to_kickoff=202.0,
        )
        assert pick.hours_to_kickoff == 202.0
        assert pick.alerted_at == "2026-04-02T10:00:00Z"

    def test_pick_without_timing_defaults_none(self):
        pick = _make_pick()
        assert pick.hours_to_kickoff is None
        assert pick.alerted_at is None
        assert pick.first_seen_at is None


class TestReportFilters:
    def test_report_since_dt_uses_days_when_no_env_cutoff(self):
        now = datetime(2026, 4, 27, 12, 0, tzinfo=timezone.utc)
        since = report_since_dt(days=7, now=now)
        assert since == datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc)

    def test_filter_report_picks_by_days(self):
        picks = [
            _make_pick(game="A vs B", kickoff="19/04/2026 12:00"),
            _make_pick(game="C vs D", kickoff="21/04/2026 12:00"),
            _make_pick(game="E vs F", kickoff="26/04/2026 12:00"),
        ]
        filtered = filter_report_picks(picks, days=7)
        games = [pick.game for pick in filtered]
        assert "A vs B" not in games
        assert "C vs D" in games
        assert "E vs F" in games
