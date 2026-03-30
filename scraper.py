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
    sbo_raw  = bms.get("Sbobet", [])

    if not b365_raw:
        return None

    b365 = _extract_markets(b365_raw)
    sbo  = _extract_markets(sbo_raw) if sbo_raw else {}

    # Debug temporário — ver estrutura de mercados
    _home_dbg = event_data.get("home", "")
    if any(x in _home_dbg for x in ("Newcastle", "Brentford", "Chelsea")):
        logger.info(f"DEBUG {_home_dbg} — mercados B365: {list(b365.keys())}")
        for mk in ["Spread", "Alternative Asian Handicap", "Draw No Bet"]:
            if mk in b365:
                logger.info(f"  {mk}: {b365[mk]}")

    home     = event_data.get("home", "")
    away     = event_data.get("away", "")
    game     = f"{home} vs {away}"
    # league pode ser dict {"name": ..., "slug": ...} ou string
    league_raw = event_data.get("league", event_data.get("leagueName", ""))
    if isinstance(league_raw, dict):
        league = league_raw.get("name", "")
    else:
        league = league_raw
    kickoff  = _kickoff_str(event_data.get("date", event_data.get("startTime", "")))
    event_id = event_data.get("id", 0)

    if league not in ALLOWED_LEAGUES or not _kickoff_ok(kickoff):
        return None

    best_edge = 0.0
    best_vb: Optional[ValueBet] = None

    # Analise unificada: melhor linha no range calibrado
    # 1. AH principal (Spread) - linha mais eficiente
    # 2. Draw No Bet real da API - mais preciso que calculo
    # 3. ML directo - se dentro do range
    b_ml  = b365.get("ML", {})
    b_ah  = b365.get("Spread", {})
    b_dnb = b365.get("Draw No Bet", {})
    sbo_ml = sbo.get("ML", {})
    sbo_ah = sbo.get("Spread", {})

    draw_odd = _float(b_ml.get("draw")) or None
    href_ml  = b_ml.get("href", "")
    href_ah  = b_ah.get("href", "")
    href_dnb = b_dnb.get("href", href_ml)

    for side, team in [("home", home), ("away", away)]:
        candidates = []

        # 1. AH principal (Spread)
        ah_odd = _float(b_ah.get(side))
        ah_hdp = _float(b_ah.get("hdp") or 0)
        if ah_odd:
            sbo_odd = _float(sbo_ah.get(side)) or None
            sign = f"{ah_hdp:+.2f}" if ah_hdp != 0 else ""
            candidates.append((ah_odd, "Spread", f"{team} {sign}".strip(),
                                ah_hdp, href_ah, sbo_odd))

        # 2. Draw No Bet real da API
        dnb_odd = _float(b_dnb.get(side))
        if dnb_odd:
            candidates.append((dnb_odd, "DNB", team, None, href_dnb, None))

        # 3. ML directo
        ml_odd = _float(b_ml.get(side))
        if ml_odd:
            sbo_odd = _float(sbo_ml.get(side)) or None
            candidates.append((ml_odd, "ML", team, None, href_ml, sbo_odd))

        # Escolhe maior edge com SBO fechada
        MKT_TYPE = {"ML": "1X2", "DNB": "1X2", "Spread": "AH", "Totals": "OU"}
        for odd, mkt, sel, hdp_val, href, sbo_odd in candidates:
            if sbo_odd is not None:
                continue
            result = is_value_bet(odd, market=MKT_TYPE.get(mkt, "1X2"))
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, kickoff=kickoff,
                    market=mkt, selection=sel,
                    odds_b365=odd,
                    fair_odd=result["fair_odd"],
                    min_odd=result["min_odd"],
                    edge_pct=result["edge_pct"],
                    level=result["level"],
                    event_id=event_id,
                    hdp=hdp_val,
                    odds_x=draw_odd if mkt in ("ML", "DNB") else None,
                    bet_href=href,
                    odds_sbo=None,
                )



# Slugs das ligas que nos interessam (mapeamento nome → slug da API)
LEAGUE_SLUGS = [
    "portugal-liga-portugal",
    "portugal-liga-portugal-2",
    "spain-laliga",
    "spain-laliga-2",
    "england-premier-league",
    "england-championship",
    "england-league-one",
    "england-league-two",
    "italy-serie-a",
    "italy-serie-b",
    "germany-bundesliga",
    "germany-2-bundesliga",
    "france-ligue-1",
    "france-ligue-2",
    "netherlands-eredivisie",
    "scotland-premiership",
    "scotland-championship",
    "belgium-pro-league",
    "greece-super-league",
    "norway-eliteserien",
    "sweden-allsvenskan",
    "denmark-superliga",
    "finland-veikkausliiga",
    "switzerland-super-league",
    "austria-bundesliga",
    "turkiye-super-lig",
    "poland-ekstraklasa",
    "romania-superliga",
    "russia-premier-league",
    "serbia-superliga",
    "international-clubs-uefa-champions-league",
    "international-clubs-uefa-europa-league",
    "international-clubs-uefa-conference-league",
    "brazil-brasileiro-serie-a",
    "brazil-brasileiro-serie-b",
    "argentina-liga-profesional",
    "international-clubs-copa-libertadores",
    "international-clubs-copa-sudamericana",
    "mexico-liga-mx-clausura",
    "usa-mls",
    "international-world-cup",
    "japan-jleague",
    "republic-of-korea-k-league-1",
    "australia-a-league",
    "china-chinese-super-league",
]


def fetch_events_for_league(slug: str) -> list[dict]:
    """Busca eventos de uma liga específica pelo slug."""
    try:
        data = _get("/events", {
            "sport": "football",
            "league": slug,
            "bookmaker": "Bet365",
            "limit": 100,
        })
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("data", data.get("events", []))
    except Exception as e:
        logger.warning(f"fetch_events_for_league {slug}: {e}")
    return []


def _get_league_name(event: dict) -> str:
    """Extrai nome da liga independentemente do formato (str ou dict)."""
    league_raw = event.get("league", event.get("leagueName", ""))
    if isinstance(league_raw, dict):
        return league_raw.get("name", "")
    return league_raw


def fetch_events() -> list[dict]:
    """Busca eventos apenas das ligas alvo, uma a uma."""
    all_events = []
    for slug in LEAGUE_SLUGS:
        events = fetch_events_for_league(slug)
        all_events.extend(events)
        logger.debug(f"  {slug}: {len(events)} eventos")
    logger.info(f"fetch_events: {len(all_events)} eventos nas ligas alvo")
    return all_events


def fetch_odds_multi(event_ids: list[int]) -> list[dict]:
    """Busca odds para cada evento (Bet365 + Sbobet).
    Se Sbobet der 404, tenta só Bet365 — Sbobet ainda não tem o jogo (early bet).
    """
    results = []
    for event_id in event_ids:
        data = None
        # Tenta com Bet365 + Sbobet
        try:
            data = _get("/odds", {"eventId": event_id,
                                  "bookmakers": "Bet365,Sbobet"})
        except Exception as e:
            if "404" in str(e):
                # Sbobet não tem este evento — tenta só Bet365
                try:
                    data = _get("/odds", {"eventId": event_id,
                                          "bookmakers": "Bet365"})
                except Exception as e2:
                    logger.warning(f"fetch_odds {event_id}: {e2}")
            else:
                logger.warning(f"fetch_odds {event_id}: {e}")

        if isinstance(data, dict) and data:
            results.append(data)
        elif isinstance(data, list) and data:
            results.extend(data)
    logger.info(f"fetch_odds_multi: {len(results)} eventos com odds")
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
                if _get_league_name(ev) in ALLOWED_LEAGUES]
    logger.info(f"Eventos nas ligas alvo: {len(relevant)}")

    if not relevant:
        return []

    # Filtra: só jogos entre MIN_KICKOFF_DATE e +21 dias a partir de hoje
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    min_dt = datetime.strptime(MIN_KICKOFF_DATE, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    max_dt = now + timedelta(days=21)

    def _ev_date_ok(ev):
        date_str = ev.get("date", ev.get("startTime", ""))
        try:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return min_dt <= dt <= max_dt
        except Exception:
            return False

    relevant = [ev for ev in relevant if _ev_date_ok(ev)]
    logger.info(f"Eventos na janela de 21 dias: {len(relevant)}")

    # Exclui jogos já enviados — não vale a pena buscar odds de picks já alertados
    import json, hashlib
    sent_cache = set()
    cache_file = "sent_alerts.json"
    try:
        import pathlib
        if pathlib.Path(cache_file).exists():
            sent_cache = set(json.loads(pathlib.Path(cache_file).read_text()).get("sent", []))
    except Exception:
        pass

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
        data = _get("/odds", {"eventId": event_id, "bookmakers": "Sbobet"})
        bms = data.get("bookmakers", {}) if isinstance(data, dict) else {}
        sbo = bms.get("Sbobet", [])
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
