"""
model.py — Modelo de calibração ajustado para 1xBet
Base: Special One (182 picks) + Andrey2505 (521 picks Bet365)
Ajuste: +0.020 nos factores para compensar margem menor do 1xBet (~3.5% vs ~5.5%)

O 1xBet abre genuinamente mais alto que Bet365/Betano/Stake.
O valor está em apostar nas casas europeias antes que corrijam para as odds do 1xBet.
Threshold: 10% — calibrado com diferença real observada (2.32 1xBet vs 2.15-2.25 Bet365/Betano)
"""

CALIBRATION_FACTORS = {
    (1.70, 1.80): 0.889,
    (1.80, 1.90): 0.901,
    (1.90, 2.00): 0.899,
    (2.00, 2.15): 0.878,
    (2.15, 2.50): 0.865,
}

MIN_EDGE_PCT = 10.0
MIN_ODD = 1.70
MAX_ODD = 2.50

MIN_KICKOFF_DATE = "2026-04-15"


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
    if edge >= 22:
        level = "🔥 Elite"
    elif edge >= 16:
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
