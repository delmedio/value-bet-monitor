"""
model.py — Modelo de calibração baseado em 182 picks do Special One + 521 do Andrey2505
Factor de conversão: odd_abertura × factor = fair_odd_estimada
Calibrado com ground truth: CLV médio real 10.1%, beat the line 76%
"""

# Factores de calibração por bucket de odds de abertura
# Derivados de 182 picks com fair close real (Special One)
# Confirmados por yield implícito do Andrey2505 (521 picks Bet365)
CALIBRATION_FACTORS = {
    (1.70, 1.80): 0.894,
    (1.80, 1.90): 0.920,
    (1.90, 2.00): 0.914,
    (2.00, 2.15): 0.873,
    (2.15, 2.50): 0.839,
}

# Threshold mínimo de edge para gerar alerta
# Baseado em análise: CLV médio real é 10.1%, usamos 8% como mínimo conservador
MIN_EDGE_PCT = 8.0

# Range de odds onde o modelo é fiável (95% dos picks históricos)
MIN_ODD = 1.70
MAX_ODD = 2.15

# Mercados suportados e labels
MARKETS = {
    "ou": "Over/Under",
    "ah": "Asian Handicap",
    "dnb": "Draw No Bet",
    "1x2": "Match Odds",
}

# Ligas cobertas — mapeamento nome display → identificadores
LEAGUES = {
    # Portugal
    "Liga Portugal 1": ["liga-portugal", "primeira-liga", "portugal/primeira-liga"],
    "Liga Portugal 2": ["liga-portugal-2", "segunda-liga", "portugal/segunda-liga"],
    # Espanha
    "La Liga": ["la-liga", "spain/la-liga", "primera-division"],
    "La Liga 2": ["laliga2", "spain/laliga2", "segunda-division"],
    # Inglaterra
    "Premier League": ["premier-league", "england/premier-league"],
    "Championship": ["championship", "england/championship"],
    # Itália
    "Serie A": ["serie-a", "italy/serie-a"],
    "Serie B": ["serie-b", "italy/serie-b"],
    "Serie C": ["serie-c", "italy/serie-c"],
    # Alemanha
    "Bundesliga": ["bundesliga", "germany/bundesliga"],
    "2. Bundesliga": ["2-bundesliga", "germany/2-bundesliga"],
    # Holanda
    "Eredivisie": ["eredivisie", "netherlands/eredivisie"],
    "Eerste Divisie": ["eerste-divisie", "netherlands/eerste-divisie"],
    # Escócia
    "Scottish Premiership": ["scottish-premiership", "scotland/premiership"],
    # Bélgica
    "Jupiler Pro League": ["jupiler-pro-league", "belgium/first-division-a"],
    # Grécia
    "Super League Greece": ["super-league", "greece/super-league"],
    # Chipre
    "1. Division Cyprus": ["cyprus-1st-division", "cyprus/first-division"],
    # Dinamarca
    "Superliga Denmark": ["superliga", "denmark/superliga"],
    # Noruega
    "Eliteserien": ["eliteserien", "norway/eliteserien"],
    # Suécia
    "Allsvenskan": ["allsvenskan", "sweden/allsvenskan"],
    # Áustria
    "Bundesliga Austria": ["austria-bundesliga", "austria/bundesliga"],
    # Rep. Checa
    "Czech First League": ["czech-first-league", "czech-republic/first-league"],
    # Bulgária
    "First League Bulgaria": ["bulgaria-first-league", "bulgaria/first-league"],
    # Competições europeias
    "Champions League": ["champions-league", "uefa/champions-league"],
    "Europa League": ["europa-league", "uefa/europa-league"],
    "Conference League": ["conference-league", "uefa/europa-conference-league"],
    # Brasil
    "Serie A Brazil": ["brazil-serie-a", "brazil/serie-a"],
    "Serie B Brazil": ["brazil-serie-b", "brazil/serie-b"],
    # Argentina
    "Primera Division": ["argentina-primera-division", "argentina/primera-division"],
    # Libertadores / Sudamericana
    "Copa Libertadores": ["copa-libertadores", "south-america/copa-libertadores"],
    "Copa Sudamericana": ["copa-sudamericana", "south-america/copa-sudamericana"],
    # Outros América do Sul
    "Liga Colombia": ["colombia-primera-a", "colombia/primera-a"],
    "Primera Chile": ["chile-primera-division", "chile/primera-division"],
    "LigaPro Ecuador": ["ecuador-liga-pro", "ecuador/liga-pro"],
    # México
    "Liga MX": ["mexico-primera-division", "mexico/liga-mx"],
    # América do Norte
    "MLS": ["mls", "usa/mls"],
    "USL Championship": ["usl-championship", "usa/usl-championship"],
    "CONCACAF Champions Cup": ["concacaf-champions-league", "concacaf/champions-cup"],
    # Ásia / Oceânia
    "J1 League": ["j-league", "japan/j1-league"],
    "A-League": ["a-league", "australia/a-league"],
    "Chinese Super League": ["chinese-super-league", "china/super-league"],
}


def get_calibration_factor(odd: float) -> float | None:
    """Retorna o factor de calibração para uma odd de abertura."""
    for (lo, hi), factor in CALIBRATION_FACTORS.items():
        if lo <= odd < hi:
            return factor
    return None


def estimate_fair_odd(opening_odd: float) -> float | None:
    """
    Estima a fair odd a partir da odd de abertura.
    fair_odd = opening_odd × factor
    """
    factor = get_calibration_factor(opening_odd)
    if factor is None:
        return None
    return round(opening_odd * factor, 3)


def calculate_edge(opening_odd: float, fair_odd: float) -> float:
    """
    Edge % = (opening_odd / fair_odd - 1) × 100
    Representa o CLV esperado se o mercado corrigir para a fair odd.
    """
    if fair_odd <= 0:
        return 0.0
    return round((opening_odd / fair_odd - 1) * 100, 2)


def minimum_acceptable_odd(fair_odd: float, min_edge: float = MIN_EDGE_PCT) -> float:
    """
    Odd mínima para apostar dado uma fair odd e edge mínimo desejado.
    min_odd = fair_odd × (1 + min_edge/100)
    """
    return round(fair_odd * (1 + min_edge / 100), 3)


def is_value_bet(opening_odd: float) -> dict | None:
    """
    Determina se uma odd de abertura representa value.
    Retorna dict com análise ou None se não houver value.
    """
    if not (MIN_ODD <= opening_odd <= MAX_ODD):
        return None

    fair_odd = estimate_fair_odd(opening_odd)
    if fair_odd is None:
        return None

    edge = calculate_edge(opening_odd, fair_odd)
    if edge < MIN_EDGE_PCT:
        return None

    min_odd = minimum_acceptable_odd(fair_odd)

    # Classificação do nível de value
    if edge >= 18:
        level = "🔥 Elite"
    elif edge >= 12:
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
