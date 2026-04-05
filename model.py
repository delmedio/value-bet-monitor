"""
model.py — Modelo de calibração para detecção de early value bets.

Base revisto com dados reais do separador Bets do Special One.
A principal conclusão dessa amostra:

  - ML puro e claramente mais fraco que DNB
  - DNB, AH e Totals sustentam melhor CLV real
  - odds altas em ML devem ser muito mais filtradas

O modelo passou a usar bandas por mercado, em vez de um factor linear unico.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

MIN_ODD = 1.50


def min_kickoff_date() -> str:
    return date.today().isoformat()
LEARNING_PICKS_FILE = Path("picks_log.json")

MARKET_PROFILES = {
    # Match Result puro: sem dados tracked, manter restritivo.
    "ML": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.979, 7.0),
            (1.80, 2.00, 0.987, 7.5),
            (2.00, 2.20, 0.976, 8.0),
        ],
    },
    # DNB — recalibrado com 51 CLVs reais (Sbobet/Stake closing).
    # Factores anteriores (0.923/0.903) produziam edge ~10% fictício.
    # Break-even real: ~0.948 (1.80-2.00) e ~0.963 (2.00-2.20).
    # Factores ligeiramente abaixo do break-even para manter margem de selecção.
    "DNB": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.975, 3.5),
            (1.80, 2.00, 0.950, 4.0),
            (2.00, 2.20, 0.955, 4.5),
        ],
    },
    # AH — 1 tracked, dados insuficientes. Ajuste proporcional ao DNB.
    "AH": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 2.00, 0.958, 4.0),
            (2.00, 2.20, 0.940, 4.5),
        ],
    },
    # OU — 5 tracked, CLV -0.42% (quase break-even). Ajuste moderado.
    "OU": {
        "max_odd": 2.20,
        "bands": [
            (1.50, 1.80, 0.985, 3.5),
            (1.80, 2.00, 0.955, 4.0),
            (2.00, 2.20, 0.940, 4.5),
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


def _timing_bonus(tracked: list[dict]) -> float:
    """
    Calcula bonus/penalidade com base no timing dos picks.
    Super earlys (>7d) com bom CLV → relaxa threshold.
    Picks 48-72h com CLV fraco → aperta.
    Retorna ajuste entre -0.5 e +0.5.
    """
    super_early = [p for p in tracked
                   if isinstance(p.get("hours_to_kickoff"), (int, float))
                   and p["hours_to_kickoff"] >= 168]  # 7d+
    standard = [p for p in tracked
                if isinstance(p.get("hours_to_kickoff"), (int, float))
                and 48 <= p["hours_to_kickoff"] < 72]  # 48-72h

    if len(super_early) < 5 and len(standard) < 5:
        return 0.0

    se_clv = (sum(p["clv_real"] for p in super_early) / len(super_early)) if super_early else 0
    std_clv = (sum(p["clv_real"] for p in standard) / len(standard)) if standard else 0

    # Super earlys claramente melhores → relaxar para aceitar mais aberturas
    if len(super_early) >= 5 and se_clv >= 5 and se_clv > std_clv + 2:
        return -0.5
    # Super earlys com CLV fraco → apertar
    if len(super_early) >= 5 and se_clv < 0:
        return 0.5
    return 0.0


def _league_bonus(tracked: list[dict], league: str) -> float:
    """
    Ajuste por liga com base no CLV historico.
    Ligas com CLV consistentemente alto → relaxa threshold.
    Ligas com CLV negativo → aperta.
    Retorna ajuste entre -1.0 e +1.5.
    """
    if not league:
        return 0.0

    league_picks = [p for p in tracked if p.get("league") == league]
    if len(league_picks) < 10:
        return 0.0

    avg_clv = sum(p["clv_real"] for p in league_picks) / len(league_picks)
    beat_pct = sum(1 for p in league_picks if p["clv_real"] > 0) / len(league_picks) * 100

    # Liga forte: CLV alto e consistente
    if avg_clv >= 6 and beat_pct >= 60:
        return -1.0
    if avg_clv >= 4 and beat_pct >= 55:
        return -0.5
    # Liga fraca: CLV negativo
    if avg_clv < -2:
        return 1.5
    if avg_clv < 0:
        return 1.0
    if avg_clv < 1.5 and beat_pct < 45:
        return 0.5
    return 0.0


def _hour_band(hour: int) -> str:
    """Classifica hora UTC em faixa do dia."""
    if hour < 6:
        return "night"      # 00-06 UTC (madrugada europeia)
    if hour < 12:
        return "morning"    # 06-12 UTC (manhã europeia)
    if hour < 18:
        return "afternoon"  # 12-18 UTC (tarde europeia)
    return "evening"        # 18-00 UTC (noite europeia)


def _extract_alert_hour(pick: dict) -> int | None:
    """Extrai hora UTC do alerted_at."""
    alerted = pick.get("alerted_at", "")
    if not alerted:
        return None
    try:
        dt = datetime.fromisoformat(alerted.replace("Z", "+00:00"))
        return dt.hour
    except Exception:
        return None


def _hour_bonus(tracked: list[dict], current_hour: int | None = None) -> float:
    """
    Ajuste com base na janela horaria UTC em que o pick é detectado.
    A Bet365 actualiza preços com equipas humanas — há janelas
    (ex: madrugada europeia) em que os preços ficam stale mais tempo.
    Retorna ajuste entre -0.5 e +0.5.
    """
    if current_hour is None:
        return 0.0

    current_band = _hour_band(current_hour)

    by_band: dict[str, list[float]] = {}
    for p in tracked:
        hour = _extract_alert_hour(p)
        if hour is None:
            continue
        band = _hour_band(hour)
        clv = p.get("clv_real")
        if isinstance(clv, (int, float)):
            by_band.setdefault(band, []).append(clv)

    current_clvs = by_band.get(current_band, [])
    if len(current_clvs) < 8:
        return 0.0

    avg_clv = sum(current_clvs) / len(current_clvs)

    # Calcular CLV médio das outras faixas para comparação
    other_clvs = []
    for band, clvs in by_band.items():
        if band != current_band and len(clvs) >= 5:
            other_clvs.extend(clvs)

    if not other_clvs:
        return 0.0

    avg_other = sum(other_clvs) / len(other_clvs)

    # Faixa actual claramente melhor que as outras → relaxar
    if avg_clv >= 5 and avg_clv > avg_other + 2:
        return -0.5
    # Faixa actual claramente pior → apertar
    if avg_clv < 0 and avg_clv < avg_other - 2:
        return 0.5
    return 0.0


def adaptive_min_edge(
    market: str = "ML",
    opening_odd: float | None = None,
    league: str = "",
) -> float:
    """
    Ajuste por mercado + timing + liga com base no tracking real do proprio bot.
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

    # Ajuste por timing (super earlys vs late picks)
    adjustment += _timing_bonus(tracked)

    # Ajuste por liga (todas as tracked, não só do mercado)
    try:
        all_tracked = [
            pick for pick in raw
            if isinstance(pick, dict)
            and isinstance(pick.get("clv_real"), (int, float))
        ]
        adjustment += _league_bonus(all_tracked, league)
    except Exception:
        pass

    # Ajuste por hora do dia (janela de ineficiencia da Bet365)
    try:
        now_hour = datetime.now(timezone.utc).hour
        adjustment += _hour_bonus(all_tracked, now_hour)
    except Exception:
        pass

    return round(min(max(base_edge + adjustment, 3.0), 10.0), 2)


def is_value_bet(opening_odd: float, market: str = "ML", league: str = "") -> dict | None:
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
    min_edge = adaptive_min_edge(market, opening_odd, league=league)
    if edge < min_edge:
        return None

    return {
        "fair_odd": fair,
        "edge_pct": edge,
        "min_odd": minimum_acceptable_odd(fair, min_edge=min_edge),
        "min_edge_pct": min_edge,
        "level": ev_level(edge),
    }

