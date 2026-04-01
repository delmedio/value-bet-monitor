"""
model.py — Modelo de calibração para detecção de early value bets.

Base revisto com dados reais do separador Bets do Special One.
A principal conclusão dessa amostra:

  - ML puro e claramente mais fraco que DNB
  - DNB, AH e Totals sustentam melhor CLV real
  - odds altas em ML devem ser muito mais filtradas

O modelo passou a usar bandas por mercado, em vez de um factor linear unico.
"""

import json
from pathlib import Path

MIN_ODD = 1.50
MIN_KICKOFF_DATE = "2026-04-10"
LEARNING_PICKS_FILE = Path("picks_log.json")

MARKET_PROFILES = {
    # Match Result puro: manter bem apertado.
    "ML": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.979, 7.0),
            (1.80, 2.00, 0.987, 7.5),
            (2.00, 2.20, 0.976, 8.0),
        ],
    },
    # DNB mostrou-se muito mais robusto no histórico.
    "DNB": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.983, 4.5),
            (1.80, 2.00, 0.923, 4.5),
            (2.00, 2.20, 0.903, 5.0),
        ],
    },
    "AH": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 2.00, 0.958, 4.0),
            (2.00, 2.20, 0.872, 5.0),
        ],
    },
    "OU": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.990, 4.0),
            (1.80, 2.00, 0.932, 4.5),
            (2.00, 2.20, 0.892, 5.0),
        ],
    },
}


def _normalize_market(market: str) -> str:
    aliases = {
        "1X2": "ML",
        "Match Result": "ML",
        "Spread": "AH",
        "Totals": "OU",
    }
    return aliases.get(market, market)


def _market_aliases(market: str) -> tuple[str, ...]:
    market = _normalize_market(market)
    if market == "AH":
        return ("Spread",)
    if market == "OU":
        return ("Totals",)
    if market == "DNB":
        return ("DNB",)
    return ("ML",)


def _profile_for(market: str) -> dict:
    return MARKET_PROFILES[_normalize_market(market)]


def get_calibration_factor(odd: float, market: str = "ML") -> float | None:
    profile = _profile_for(market)
    if not (MIN_ODD <= odd <= profile["max_odd"]):
        return None

    for lower, upper, factor, _ in profile["bands"]:
        if lower <= odd < upper:
            return factor

    # Inclui o limite superior da ultima banda.
    last_lower, last_upper, last_factor, _ = profile["bands"][-1]
    if last_lower <= odd <= last_upper:
        return last_factor
    return None


def base_min_edge(market: str = "ML", opening_odd: float | None = None) -> float:
    profile = _profile_for(market)
    if opening_odd is None:
        return profile["bands"][0][3]

    for lower, upper, _, min_edge in profile["bands"]:
        if lower <= opening_odd < upper:
            return min_edge

    last_lower, last_upper, _, last_min_edge = profile["bands"][-1]
    if last_lower <= opening_odd <= last_upper:
        return last_min_edge
    return last_min_edge


def estimate_fair_odd(opening_odd: float, market: str = "ML") -> float | None:
    factor = get_calibration_factor(opening_odd, market)
    if factor is None:
        return None
    return round(opening_odd * factor, 3)


def calculate_edge(opening_odd: float, fair_odd: float) -> float:
    if fair_odd <= 0:
        return 0.0
    return round((opening_odd / fair_odd - 1) * 100, 2)


def minimum_acceptable_odd(fair_odd: float, min_edge: float) -> float:
    return round(fair_odd * (1 + min_edge / 100), 3)


def ev_level(edge_pct: float) -> str:
    if edge_pct >= 20:
        return "🔥 Elite"
    if edge_pct >= 15:
        return "✅ Strong"
    return "📊 Value"


def adaptive_min_edge(market: str = "ML", opening_odd: float | None = None) -> float:
    """
    Ajuste pequeno por mercado com base no tracking real do proprio bot.
    Parte de uma base mais conservadora calibrada nos dados historicos.
    """
    base_edge = base_min_edge(market, opening_odd)
    if not LEARNING_PICKS_FILE.exists():
        return base_edge

    try:
        raw = json.loads(LEARNING_PICKS_FILE.read_text())
        tracked = [
            pick for pick in raw
            if isinstance(pick, dict)
            and pick.get("market") in _market_aliases(market)
            and isinstance(pick.get("clv_real"), (int, float))
        ]
    except Exception:
        return base_edge

    if len(tracked) < 15:
        return base_edge

    avg_clv = sum(pick["clv_real"] for pick in tracked) / len(tracked)
    beat_pct = sum(1 for pick in tracked if pick["clv_real"] > 0) / len(tracked) * 100

    adjustment = 0.0
    if avg_clv < 0 or beat_pct < 47:
        adjustment += 1.0
    elif avg_clv < 2 or beat_pct < 52:
        adjustment += 0.5
    elif avg_clv >= 7 and beat_pct >= 60:
        adjustment -= 0.5

    return round(min(max(base_edge + adjustment, 3.5), 9.0), 2)


def is_value_bet(opening_odd: float, market: str = "ML") -> dict | None:
    """
    market:
      - ML   : Match Result
      - DNB  : Draw No Bet
      - AH   : Asian Handicap
      - OU   : Totals / Over-Under
    """
    fair = estimate_fair_odd(opening_odd, market)
    if fair is None:
        return None

    edge = calculate_edge(opening_odd, fair)
    min_edge = adaptive_min_edge(market, opening_odd)
    if edge < min_edge:
        return None

    return {
        "fair_odd": fair,
        "edge_pct": edge,
        "min_odd": minimum_acceptable_odd(fair, min_edge=min_edge),
        "min_edge_pct": min_edge,
        "level": ev_level(edge),
    }
