"""
model.py — Modelo de calibração para detecção de early value bets.

Calibrado com 703 picks reais (Special One 182 + Andrey2505 521 Bet365).
Factores contínuos segmentados por mercado:

  1X2 (ML/DNB): factor = 0.9927 - 0.0605 * odd  (mais margem, mais drift)
  AH  (Spread): factor = 0.9980 - 0.0520 * odd  (mais eficiente, menos drift)
  OU  (Totals): factor = 0.9955 - 0.0560 * odd  (intermédio)

Range: 1.50 – 2.80 | Threshold: edge >= 3%
"""

import json
from pathlib import Path

MIN_EDGE_PCT     = 3.0
MIN_ODD          = 1.50
MAX_ODD          = 2.80
MIN_KICKOFF_DATE = "2026-04-10"
LEARNING_PICKS_FILE = Path("picks_log.json")


def get_calibration_factor(odd: float, market: str = "1X2") -> float:
    if market == "AH":
        return round(0.9980 - 0.0520 * odd, 4)
    elif market == "OU":
        return round(0.9955 - 0.0560 * odd, 4)
    else:  # 1X2, ML, DNB
        return round(0.9927 - 0.0605 * odd, 4)


def estimate_fair_odd(opening_odd: float, market: str = "1X2") -> float | None:
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None
    return round(opening_odd * get_calibration_factor(opening_odd, market), 3)


def calculate_edge(opening_odd: float, fair_odd: float) -> float:
    if fair_odd <= 0:
        return 0.0
    return round((opening_odd / fair_odd - 1) * 100, 2)


def minimum_acceptable_odd(fair_odd: float,
                            min_edge: float = MIN_EDGE_PCT) -> float:
    return round(fair_odd * (1 + min_edge / 100), 3)


def ev_level(edge_pct: float) -> str:
    if edge_pct >= 20:
        return "🔥 Elite"
    elif edge_pct >= 15:
        return "✅ Strong"
    return "📊 Value"


def _market_aliases(market: str) -> tuple[str, ...]:
    if market == "AH":
        return ("Spread",)
    if market == "OU":
        return ("Totals",)
    return ("ML", "DNB")


def adaptive_min_edge(market: str = "1X2") -> float:
    """
    Ajusta o threshold por mercado com base no CLV tracked.
    O ajuste e pequeno e so entra com amostra suficiente.
    """
    if not LEARNING_PICKS_FILE.exists():
        return MIN_EDGE_PCT

    try:
        raw = json.loads(LEARNING_PICKS_FILE.read_text())
        tracked = [
            pick for pick in raw
            if isinstance(pick, dict)
            and pick.get("market") in _market_aliases(market)
            and isinstance(pick.get("clv_real"), (int, float))
            and isinstance(pick.get("edge_pct"), (int, float))
        ]
    except Exception:
        return MIN_EDGE_PCT

    if len(tracked) < 15:
        return MIN_EDGE_PCT

    avg_clv = sum(pick["clv_real"] for pick in tracked) / len(tracked)
    beat_pct = sum(1 for pick in tracked if pick["clv_real"] > 0) / len(tracked) * 100

    adjustment = 0.0
    if avg_clv < 0 or beat_pct < 47:
        adjustment += 1.0
    elif avg_clv < 2 or beat_pct < 52:
        adjustment += 0.5
    elif avg_clv >= 6 and beat_pct >= 60:
        adjustment -= 0.5

    return round(min(max(MIN_EDGE_PCT + adjustment, 2.5), 5.0), 2)


def is_value_bet(opening_odd: float, market: str = "1X2") -> dict | None:
    """
    Verifica se uma odd tem value com base no modelo calibrado.
    market: "1X2" (ML/DNB), "AH" (Spread), "OU" (Totals)
    Devolve dict ou None.
    """
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None
    fair = estimate_fair_odd(opening_odd, market)
    if fair is None:
        return None
    edge = calculate_edge(opening_odd, fair)
    min_edge = adaptive_min_edge(market)
    if edge < min_edge:
        return None
    return {
        "fair_odd": fair,
        "edge_pct": edge,
        "min_odd":  minimum_acceptable_odd(fair, min_edge=min_edge),
        "min_edge_pct": min_edge,
        "level":    ev_level(edge),
    }
