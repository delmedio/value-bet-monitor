"""
scraper.py — Scraper via odds-api.io

Fluxo:
  1. /v3/events?sport=Football&bookmaker=Bet365 → lista de jogos
  2. /v3/odds/multi?eventIds=...&bookmakers=Bet365,SBObet → odds de ambas
  3. model.py analisa Bet365 com factor de calibração → detecta early value
  4. SBObet é guardada para report CLV (se já aberta) — não é usada para detectar value

O value é detectado independentemente de a SBObet ter linha ou não.
Se a SBObet ainda não abriu (early bet) → CLV apurado depois quando abrir.
Se já abriu → CLV disponível imediatamente no report.
"""

import os, time, logging, requests
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

from model import is_value_bet, MIN_KICKOFF_DATE, ev_level

logger = logging.getLogger(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_IO_KEY", "")
BASE_URL     = "https://api.odds-api.io/v3"

ALLOWED_LEAGUES = {
    # Portugal
    "Portugal - Liga Portugal",
    "Portugal - Liga Portugal 2",
    # Espanha
    "Spain - LaLiga",
    "Spain - LaLiga 2",
    # Inglaterra
    "England - Premier League",
    "England - Championship",
    "England - League One",
    "England - League Two",
    # Itália
    "Italy - Serie A",
    "Italy - Serie B",
    # Alemanha
    "Germany - Bundesliga",
    "Germany - 2. Bundesliga",
    # França
    "France - Ligue 1",
    "France - Ligue 2",
    # Holanda
    "Netherlands - Eredivisie",
    # Escócia
    "Scotland - Premiership",
    "Scotland - Championship",
    # Bélgica
    "Belgium - Pro League",
    # Grécia
    "Greece - Super League",
    # Noruega
    "Norway - Eliteserien",
    # Suécia
    "Sweden - Allsvenskan",
    # Dinamarca
    "Denmark - Superliga",
    # Finlândia
    "Finland - Veikkausliiga",
    # Suíça
    "Switzerland - Super League",
    # Áustria
    "Austria - Bundesliga",
    # Turquia
    "Turkiye - Super Lig",
    # Polónia
    "Poland - Ekstraklasa",
    # Roménia
    "Romania - Superliga",
    # Rússia
    "Russia - Premier League",
    # Sérvia
    "Serbia - Superliga",
    # Europa
    "International Clubs - UEFA Champions League",
    "International Clubs - UEFA Europa League",
    "International Clubs - UEFA Conference League",
    # Brasil
    "Brazil - Brasileiro Serie A",
    "Brazil - Brasileiro Serie B",
    # Argentina
    "Argentina - Liga Profesional",
    # Sul-América
    "International Clubs - Copa Libertadores",
    "International Clubs - Copa Sudamericana",
    # México
    "Mexico - Liga MX, Clausura",
    # EUA
    "USA - MLS",
    # Mundial
    "International - World Cup",
    # Ásia/Oceânia
    "Japan - J.League",
    "Republic of Korea - K-League 1",
    "Australia - A-League",
    "China - Chinese Super League",
}


@dataclass
class ValueBet:
    game: str
    home_team: str
    away_team: str
    league: str
    kickoff: str           # "DD/MM/YYYY HH:MM"
    market: str            # ML / Spread / Totals
    selection: str         # ex: "Chelsea", "Chelsea -0.5", "Over 2.5"
    odds_b365: float       # odd Bet365 (nossa entrada)
    fair_odd: float        # estimada pelo modelo calibrado
    min_odd: float         # mínimo aceitável (fair * 1.05)
    edge_pct: float        # edge estimado em %
    level: str             # Value / Strong / Elite
    event_id: int
    hdp: Optional[float] = None
    line: Optional[float] = None
    odds_x: Optional[float] = None    # empate Bet365 (para linhas DNB/AH)
    opp_odd: Optional[float] = None   # odd oposta OU (para quarter lines)
    bet_href: str = ""
    # SBObet — preenchida se já aberta (para CLV imediato no report)
    odds_sbo: Optional[float] = None


def _get(path: str, params: dict, retries: int = 3) -> dict | list:
    if not ODDS_API_KEY:
        raise ValueError("ODDS_API_IO_KEY não definida")
    params = {**params, "apiKey": ODDS_API_KEY}
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            if e.response.status_code == 429:
                time.sleep(2 ** attempt)
            else:
                raise
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(1)
    return {}


def _kickoff_str(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso


def _kickoff_ok(kickoff_str: str) -> bool:
    try:
        dt = datetime.strptime(kickoff_str, "%d/%m/%Y %H:%M")
        min_dt = datetime.strptime(MIN_KICKOFF_DATE, "%Y-%m-%d")
        return dt >= min_dt
    except Exception:
        return True


def _float(val) -> float:
    try:
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def _extract_markets(bookmaker_markets: list) -> dict:
    """Organiza mercados por nome."""
    result = {}
    for mkt in bookmaker_markets:
        name = mkt.get("name", "")
        odds_list = mkt.get("odds", [])
        if odds_list:
            result[name] = {**odds_list[0], "href": mkt.get("href", "")}
    return result


def _analyse_event(event_data: dict) -> Optional[ValueBet]:
    """
    Analisa um evento.
    - Usa Bet365 + modelo calibrado para detectar value
    - Guarda odds SBObet se disponíveis (para CLV)
    - Devolve o melhor pick do jogo (maior edge) ou None
    """
    bms      = event_data.get("bookmakers", {})
    b365_raw = bms.get("Bet365", [])
    sbo_raw  = bms.get("SBObet", [])

    if not b365_raw:
        return None

    b365 = _extract_markets(b365_raw)
    sbo  = _extract_markets(sbo_raw) if sbo_raw else {}

    home     = event_data.get("home", "")
    away     = event_data.get("away", "")
    game     = f"{home} vs {away}"
    league   = event_data.get("league", event_data.get("leagueName", ""))
    kickoff  = _kickoff_str(event_data.get("date", event_data.get("startTime", "")))
    event_id = event_data.get("id", 0)

    if league not in ALLOWED_LEAGUES or not _kickoff_ok(kickoff):
        return None

    best_edge = 0.0
    best_vb: Optional[ValueBet] = None

    # ── Match Odds (ML) ──────────────────────────────────────────────────────
    b_ml = b365.get("ML", {})
    if b_ml:
        for side, team in [("home", home), ("away", away)]:
            odd = _float(b_ml.get(side))
            result = is_value_bet(odd)
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                sbo_ml = sbo.get("ML", {})
                odds_sbo = _float(sbo_ml.get(side)) or None
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, kickoff=kickoff,
                    market="ML", selection=team,
                    odds_b365=odd,
                    fair_odd=result["fair_odd"],
                    min_odd=result["min_odd"],
                    edge_pct=result["edge_pct"],
                    level=result["level"],
                    event_id=event_id,
                    odds_x=_float(b_ml.get("draw")) or None,
                    bet_href=b_ml.get("href", ""),
                    odds_sbo=odds_sbo,
                )

    # ── Asian Handicap (Spread) ──────────────────────────────────────────────
    b_ah = b365.get("Spread", {})
    if b_ah:
        hdp = _float(b_ah.get("hdp") or 0)
        for side, team in [("home", home), ("away", away)]:
            odd = _float(b_ah.get(side))
            result = is_value_bet(odd)
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                sbo_ah = sbo.get("Spread", {})
                odds_sbo = _float(sbo_ah.get(side)) or None
                sign = f"{hdp:+.2f}" if hdp != 0 else ""
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, kickoff=kickoff,
                    market="Spread", selection=f"{team} {sign}".strip(),
                    odds_b365=odd,
                    fair_odd=result["fair_odd"],
                    min_odd=result["min_odd"],
                    edge_pct=result["edge_pct"],
                    level=result["level"],
                    event_id=event_id, hdp=hdp,
                    bet_href=b_ah.get("href", ""),
                    odds_sbo=odds_sbo,
                )

    # ── Over/Under (Totals) ───────────────────────────────────────────────────
    b_ou = b365.get("Totals", {})
    if b_ou:
        line = _float(b_ou.get("max") or b_ou.get("hdp") or 0)
        over_odd  = _float(b_ou.get("over") or b_ou.get("home"))
        under_odd = _float(b_ou.get("under") or b_ou.get("away"))
        sbo_ou = sbo.get("Totals", {})

        for direction, odd, opp in [
            ("Over",  over_odd,  under_odd),
            ("Under", under_odd, over_odd),
        ]:
            result = is_value_bet(odd)
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                sbo_key = ("over" if direction == "Over" else "under")
                odds_sbo = _float(sbo_ou.get(sbo_key) or sbo_ou.get("home" if direction == "Over" else "away")) or None
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, kickoff=kickoff,
                    market="Totals", selection=f"{direction} {line}",
                    odds_b365=odd,
                    fair_odd=result["fair_odd"],
                    min_odd=result["min_odd"],
                    edge_pct=result["edge_pct"],
                    level=result["level"],
                    event_id=event_id, line=line, opp_odd=opp,
                    bet_href=b_ou.get("href", ""),
                    odds_sbo=odds_sbo,
                )

    return best_vb


def fetch_events() -> list[dict]:
    all_events = []
    page = 1
    while True:
        data = _get("/events", {"sport": "Football", "bookmaker": "Bet365",
                                "limit": 100, "page": page})
        if isinstance(data, list):
            if not data:
                break
            all_events.extend(data)
            if len(data) < 100:
                break
            page += 1
        elif isinstance(data, dict):
            items = data.get("data", data.get("events", []))
            all_events.extend(items)
            break
        else:
            break
    logger.info(f"fetch_events: {len(all_events)} eventos")
    return all_events


def fetch_odds_multi(event_ids: list[int]) -> list[dict]:
    results = []
    for i in range(0, len(event_ids), 10):
        batch = event_ids[i:i+10]
        ids_str = ",".join(str(x) for x in batch)
        data = _get("/odds/multi", {"eventIds": ids_str,
                                    "bookmakers": "Bet365,SBObet"})
        if isinstance(data, list):
            results.extend(data)
        elif isinstance(data, dict):
            results.extend(data.get("data", []))
    return results


def fetch_value_bets() -> list[ValueBet]:
    """
    Detecta early value bets na Bet365 usando o modelo calibrado.
    A SBObet é consultada em paralelo mas apenas para guardar odds de
    abertura (para CLV report) — não é usada para detectar value.
    """
    events = fetch_events()
    if not events:
        return []

    relevant = [ev for ev in events
                if ev.get("league", ev.get("leagueName", "")) in ALLOWED_LEAGUES]
    logger.info(f"Eventos nas ligas alvo: {len(relevant)}")

    if not relevant:
        return []

    event_ids = [ev.get("id") for ev in relevant if ev.get("id")]
    odds_data = fetch_odds_multi(event_ids)

    value_bets = []
    for event_odds in odds_data:
        try:
            vb = _analyse_event(event_odds)
            if vb:
                value_bets.append(vb)
        except Exception as e:
            logger.warning(f"_analyse_event: {e}")

    logger.info(f"Value bets encontradas: {len(value_bets)}")
    return value_bets


def fetch_sbo_closing_odds(event_id: int) -> dict:
    """Busca odds de fecho SBObet para CLV real."""
    try:
        data = _get("/odds", {"eventId": event_id, "bookmakers": "SBObet"})
        bms = data.get("bookmakers", {}) if isinstance(data, dict) else {}
        sbo = bms.get("SBObet", [])
        result = {}
        for mkt in sbo:
            name = mkt.get("name", "")
            odds_list = mkt.get("odds", [])
            if odds_list:
                result[name] = odds_list[0]
        return result
    except Exception as e:
        logger.warning(f"fetch_sbo_closing_odds {event_id}: {e}")
        return {}
