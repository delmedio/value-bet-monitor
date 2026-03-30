"""
model.py — Modelo de calibração para detecção de early value bets.

Calibrado com 703 picks reais (Special One 182 + Andrey2505 521 Bet365).
Factor contínuo: factor = 0.9927 - 0.0605 * odd
Range: 1.50 – 2.80 | Threshold: edge >= 5%
"""

MIN_EDGE_PCT     = 5.0
MIN_ODD          = 1.50
MAX_ODD          = 2.80
MIN_KICKOFF_DATE = "2026-04-15"


def get_calibration_factor(odd: float) -> float:
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


def minimum_acceptable_odd(fair_odd: float,
                            min_edge: float = MIN_EDGE_PCT) -> float:
    return round(fair_odd * (1 + min_edge / 100), 3)


def ev_level(edge_pct: float) -> str:
    if edge_pct >= 20:
        return "🔥 Elite"
    elif edge_pct >= 15:
        return "✅ Strong"
    return "📊 Value"


def is_value_bet(opening_odd: float) -> dict | None:
    """
    Verifica se uma odd tem value com base no modelo calibrado.
    Devolve dict com detalhes ou None se não tiver value ou fora do range.
    """
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None
    fair = estimate_fair_odd(opening_odd)
    if fair is None:
        return None
    edge = calculate_edge(opening_odd, fair)
    if edge < MIN_EDGE_PCT:
        return None
    return {
        "fair_odd":  fair,
        "edge_pct":  edge,
        "min_odd":   minimum_acceptable_odd(fair),
        "level":     ev_level(edge),
    }
