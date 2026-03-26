"""
model.py — Modelo de calibração combinado
Baseado em:
  - Special One: 182 picks com fair close real → CLV médio +10.1%, beat the line 76%
  - Andrey2505: 521 picks Bet365 → Yield +16.4%, factor implícito 0.833-0.842
Factor combinado = média entre os dois tipsters por bucket de odds
"""

CALIBRATION_FACTORS = {
    (1.70, 1.80): 0.869,
    (1.80, 1.90): 0.881,
    (1.90, 2.00): 0.879,
    (2.00, 2.15): 0.858,
    (2.15, 2.50): 0.845,
}

MIN_EDGE_PCT = 10.0
MIN_ODD = 1.70
MAX_ODD = 2.15

MARKETS = {
    "ou":  "Over/Under",
    "ah":  "Asian Handicap",
    "dnb": "Draw No Bet",
    "1x2": "Match Odds",
}


def get_calibration_factor(odd: float) -> float | None:
    for (lo, hi), factor in CALIBRATION_FACTORS.items():
        if lo <= odd < hi:
            return factor
    return None


def estimate_fair_odd(opening_odd: float) -> float | None:
    factor = get_calibration_factor(opening_odd)
    if factor is None:
        return None
    return round(opening_odd * factor, 3)


def calculate_edge(opening_odd: float, fair_odd: float) -> float:
    if fair_odd <= 0:
        return 0.0
    return round((opening_odd / fair_odd - 1) * 100, 2)


def minimum_acceptable_odd(fair_odd: float, min_edge: float = MIN_EDGE_PCT) -> float:
    return round(fair_odd * (1 + min_edge / 100), 3)


def is_value_bet(opening_odd: float) -> dict | None:
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None
    fair_odd = estimate_fair_odd(opening_odd)
    if fair_odd is None:
        return None
    edge = calculate_edge(opening_odd, fair_odd)
    if edge < MIN_EDGE_PCT:
        return None
    min_odd = minimum_acceptable_odd(fair_odd)
    if edge >= 20:
        level = "🔥 Elite"
    elif edge >= 14:
        level = "✅ Strong"
    else:
        level = "📊 Value"
    return {
        "opening_odd": opening_odd,
        "fair_odd": fair_odd,
        "edge_pct": edge,
        "min_odd": min_odd,
        "level": level,
        "clv_expected": f"+{edge:.1f}%",
    }
