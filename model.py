"""
model.py — Modelo de calibração combinado
Special One (182 picks) + Andrey2505 (521 picks Bet365)
CLV médio real confirmado: +10.1% | Beat the line: 76%
"""

# Factores combinados: média entre Special One e Andrey2505
CALIBRATION_FACTORS = {
    (1.70, 1.80): 0.869,
    (1.80, 1.90): 0.881,
    (1.90, 2.00): 0.879,
    (2.00, 2.15): 0.858,
    (2.15, 2.50): 0.845,
}

MIN_EDGE_PCT = 10.0
MIN_ODD = 1.70
MAX_ODD = 2.50

# Data mínima para alertar (jogos a partir desta data)
# Formato: "YYYY-MM-DD" — None para sem filtro
MIN_KICKOFF_DATE = "2026-03-28"


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
    }
