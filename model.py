"""
model.py — Modelo de calibração para detecção de early value bets.

Calibrado com 703 picks reais (Special One 182 + Andrey2505 521 Bet365).
Factores contínuos segmentados por mercado:

  1X2 (ML/DNB): factor = 0.9927 - 0.0605 * odd  (mais margem, mais drift)
  AH  (Spread): factor = 0.9980 - 0.0520 * odd  (mais eficiente, menos drift)
  OU  (Totals): factor = 0.9955 - 0.0560 * odd  (intermédio)

Range: 1.50 – 2.80 | Threshold: edge >= 3%
"""

MIN_EDGE_PCT     = 3.0
MIN_ODD          = 1.50
MAX_ODD          = 2.80
MIN_KICKOFF_DATE = "2026-04-10"


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
    if edge < MIN_EDGE_PCT:
        return None
    return {
        "fair_odd": fair,
        "edge_pct": edge,
        "min_odd":  minimum_acceptable_odd(fair),
        "level":    ev_level(edge),
    }
