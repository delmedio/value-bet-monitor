"""
model.py — Modelo de calibração com factor contínuo para 1xBet
Base: Special One (182 picks) + Andrey2505 (521 picks Bet365)
Ajuste: +2% para compensar margem menor do 1xBet (~3.5% vs ~5.5% Bet365)

Factor contínuo (regressão linear): factor = 0.9927 - 0.0605 * odd
Isto dá edges diferenciados por odd em vez de iguais dentro de um range.

Threshold: 10% — calibrado com diferença real observada
"""

MIN_EDGE_PCT = 10.0
MIN_ODD = 1.70
MAX_ODD = 2.50
MIN_KICKOFF_DATE = "2026-04-15"


def get_calibration_factor(odd: float) -> float:
    """Factor contínuo — decresce à medida que a odd sobe."""
    return round(0.9927 - 0.0605 * odd, 4)


def estimate_fair_odd(opening_odd: float) -> float | None:
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None
    factor = get_calibration_factor(opening_odd)
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
    elif edge >= 15:
        level = "✅ Strong"
    else:
        level = "📊 Value"
    return {
        "opening_odd": opening_odd,
        "fair_odd":    fair_odd,
        "edge_pct":    edge,
        "min_odd":     min_odd,
        "level":       level,
    }
