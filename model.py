"""
model.py — Modelo de calibração para detecção de early value bets.

Objectivo: estimar a fair odd de uma aposta na Bet365 ANTES de a Sbobet
ter uma linha estabelecida (early bet). O CLV real é apurado depois quando
a Sbobet abre e fecha.

Calibrado com 703 picks reais (Special One 182 + Andrey2505 521 Bet365).
Factor contínuo: factor = 0.9927 - 0.0605 * odd
Range calibrado: 1.50 – 2.80 (acima disso usa DNB como proxy)
Threshold: edge >= 5%

Lógica DNB:
- Se ML está acima de MAX_ODD (ex: 3.25) mas tem draw disponível,
  calcula o DNB equivalente e verifica se esse tem value.
- DNB elimina o risco de empate e traz a odd para o range calibrado.
"""

MIN_EDGE_PCT     = 5.0
MIN_ODD          = 1.50
MAX_ODD          = 2.80   # acima disto usa DNB como proxy
MAX_ODD_ABSOLUTE = 4.00   # acima disto ignora mesmo com DNB
MIN_KICKOFF_DATE = "2026-04-15"


def get_calibration_factor(odd: float) -> float:
    return round(0.9927 - 0.0605 * odd, 4)


def estimate_fair_odd(opening_odd: float) -> float | None:
    if not (MIN_ODD <= opening_odd <= MAX_ODD_ABSOLUTE):
        return None
    factor = get_calibration_factor(min(opening_odd, MAX_ODD))
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


def calc_dnb_odd(ml_odd: float, draw_odd: float) -> float | None:
    """
    Calcula a odd DNB (Draw No Bet) a partir do ML e empate.
    DNB elimina o empate e normaliza home vs away.
    """
    try:
        p_ml   = 1 / ml_odd
        p_draw = 1 / draw_odd
        p_away = 1 - p_ml - p_draw
        if p_away <= 0 or p_ml <= 0:
            return None
        p_dnb = p_ml / (p_ml + p_away)
        return round(1 / p_dnb, 3)
    except Exception:
        return None


def is_value_bet(opening_odd: float,
                 draw_odd: float | None = None) -> dict | None:
    """
    Verifica se uma odd da Bet365 tem value.

    Se odd > MAX_ODD e draw_odd disponível → tenta DNB como proxy.
    Devolve dict com detalhes ou None se não tiver value.

    Campos do dict:
      - market_type: "ML" ou "DNB"
      - odd: odd analisada (ML ou DNB calculado)
      - fair_odd, edge_pct, min_odd, level
      - dnb_odd: odd DNB calculada (se aplicável)
    """
    if not (MIN_ODD <= opening_odd <= MAX_ODD_ABSOLUTE):
        return None

    # Caso 1: dentro do range calibrado → analisa directamente
    if opening_odd <= MAX_ODD:
        fair = estimate_fair_odd(opening_odd)
        if fair is None:
            return None
        edge = calculate_edge(opening_odd, fair)
        if edge < MIN_EDGE_PCT:
            return None
        return {
            "market_type": "ML",
            "odd": opening_odd,
            "fair_odd": fair,
            "edge_pct": edge,
            "min_odd": minimum_acceptable_odd(fair),
            "level": ev_level(edge),
            "dnb_odd": None,
        }

    # Caso 2: odd > MAX_ODD → tenta DNB se empate disponível
    if draw_odd is None:
        return None

    dnb = calc_dnb_odd(opening_odd, draw_odd)
    if dnb is None or not (MIN_ODD <= dnb <= MAX_ODD):
        return None

    fair = estimate_fair_odd(dnb)
    if fair is None:
        return None
    edge = calculate_edge(dnb, fair)
    if edge < MIN_EDGE_PCT:
        return None

    return {
        "market_type": "DNB",
        "odd": dnb,
        "fair_odd": fair,
        "edge_pct": edge,
        "min_odd": minimum_acceptable_odd(fair),
        "level": ev_level(edge),
        "dnb_odd": dnb,
        "ml_odd": opening_odd,  # original ML para referência
    }
