"""
scraper.py — Scraper via odds-api.io

Fluxo:
  1. Full scan: /v3/events por slug de liga -> eventos das ligas alvo
  2. Incremental scan: /v3/odds/updated -> apenas eventos alterados
  3. /v3/odds/multi -> odds Bet365 + SingBet em batch
  4. Modelo analisa Bet365 -> detecta early value (SingBet ainda fechada)
  5. Sbobet + Stake ficam reservadas para tracking do melhor CLV
"""

import json
import os
import time
import logging
import unicodedata
from pathlib import Path

import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

from model import is_value_bet, min_kickoff_date

logger = logging.getLogger(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_IO_KEY", "")
BASE_URL     = "https://api.odds-api.io/v3"
STATE_FILE   = Path("odds_state.json")

LEAGUES = [
    ("Portugal - Liga Portugal", "portugal-liga-portugal"),
    ("Portugal - Liga Portugal 2", "portugal-liga-portugal-2"),
    ("Spain - LaLiga", "spain-laliga"),
    ("Spain - LaLiga 2", "spain-laliga-2"),
    ("England - Premier League", "england-premier-league"),
    ("England - Championship", "england-championship"),
    ("England - League One", "england-league-one"),
    ("England - League Two", "england-league-two"),
    ("Italy - Serie A", "italy-serie-a"),
    ("Italy - Serie B", "italy-serie-b"),
    ("Germany - Bundesliga", "germany-bundesliga"),
    ("Germany - 2. Bundesliga", "germany-2-bundesliga"),
    ("France - Ligue 1", "france-ligue-1"),
    ("France - Ligue 2", "france-ligue-2"),
    ("Netherlands - Eredivisie", "netherlands-eredivisie"),
    ("Scotland - Premiership", "scotland-premiership"),
    ("Scotland - Championship", "scotland-championship"),
    ("Belgium - Pro League", "belgium-pro-league"),
    ("Greece - Super League", "greece-super-league"),
    ("Norway - Eliteserien", "norway-eliteserien"),
    ("Sweden - Allsvenskan", "sweden-allsvenskan"),
    ("Denmark - Superliga", "denmark-superliga"),
    ("Finland - Veikkausliiga", "finland-veikkausliiga"),
    ("Switzerland - Super League", "switzerland-super-league"),
    ("Austria - Bundesliga", "austria-bundesliga"),
    ("Turkiye - Super Lig", "turkiye-super-lig"),
    ("Poland - Ekstraklasa", "poland-ekstraklasa"),
    ("Romania - Superliga", "romania-superliga"),
    ("Russia - Premier League", "russia-premier-league"),
    ("Serbia - Superliga", "serbia-superliga"),
    ("International Clubs - UEFA Champions League", "international-clubs-uefa-champions-league"),
    ("International Clubs - UEFA Europa League", "international-clubs-uefa-europa-league"),
    ("International Clubs - UEFA Conference League", "international-clubs-uefa-conference-league"),
    ("Brazil - Brasileiro Serie A", "brazil-brasileiro-serie-a"),
    ("Brazil - Brasileiro Serie B", "brazil-brasileiro-serie-b"),
    ("Argentina - Liga Profesional", "argentina-liga-profesional"),
    ("International Clubs - Copa Libertadores", "international-clubs-copa-libertadores"),
    ("International Clubs - Copa Sudamericana", "international-clubs-copa-sudamericana"),
    ("Mexico - Liga MX, Clausura", "mexico-liga-mx-clausura"),
    ("USA - MLS", "usa-mls"),
    ("International - World Cup", "international-world-cup"),
    ("Japan - J.League", "japan-jleague"),
    ("Republic of Korea - K-League 1", "republic-of-korea-k-league-1"),
    ("Australia - A-League", "australia-a-league"),
    ("China - Chinese Super League", "china-chinese-super-league"),
]

ALLOWED_LEAGUES = {name for name, _ in LEAGUES}
LEAGUE_SLUGS = [slug for _, slug in LEAGUES]


@dataclass
class ValueBet:
    game: str
    home_team: str
    away_team: str
    league: str
    league_slug: str
    kickoff: str
    market: str       # ML / DNB / Spread / Totals
    selection: str
    odds_b365: float
    fair_odd: float
    min_odd: float
    edge_pct: float
    level: str
    event_id: int
    hdp: Optional[float] = None
    line: Optional[float] = None
    odds_x: Optional[float] = None   # empate (para ML/DNB)
    opp_odd: Optional[float] = None  # odd oposta OU (para quarter lines)
    bet_href: str = ""
    odds_singbet: Optional[float] = None  # SingBet abertura (para CLV)


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


def _float(val) -> float:
    try:
        return float(val) if val else 0.0
    except Exception:
        return 0.0


def _kickoff_str(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso


def _get_league_name(event: dict) -> str:
    raw = event.get("league", event.get("leagueName", ""))
    if isinstance(raw, dict):
        return raw.get("name", "")
    return raw


def _extract_markets(bookmaker_markets: list) -> dict:
    result = {}
    for mkt in bookmaker_markets:
        name = mkt.get("name", "")
        odds_list = mkt.get("odds", [])
        if odds_list:
            result[name] = {**odds_list[0], "href": mkt.get("href", "")}
    return result


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _chunked(items: list[int], size: int) -> list[list[int]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _normalize_name(name: str) -> str:
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("-", " ").replace(".", " ")
    return " ".join(text.split())


def _historical_range(kickoff: str) -> tuple[str, str] | tuple[None, None]:
    try:
        dt = datetime.strptime(kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
    except Exception:
        return None, None

    start = (dt - timedelta(hours=18)).isoformat().replace("+00:00", "Z")
    end = (dt + timedelta(hours=18)).isoformat().replace("+00:00", "Z")
    return start, end


def fetch_historical_event_id(
    league_slug: str,
    kickoff: str,
    home_team: str,
    away_team: str,
) -> int | None:
    if not league_slug:
        return None

    range_from, range_to = _historical_range(kickoff)
    if not range_from or not range_to:
        return None

    try:
        data = _get(
            "/historical/events",
            {
                "sport": "football",
                "league": league_slug,
                "from": range_from,
                "to": range_to,
            },
        )
    except Exception as e:
        logger.warning(f"fetch_historical_event_id {league_slug}: {e}")
        return None

    if isinstance(data, dict):
        data = data.get("data", data.get("events", []))
    if not isinstance(data, list):
        return None

    target_home = _normalize_name(home_team)
    target_away = _normalize_name(away_team)
    matches = []
    for event in data:
        if (
            _normalize_name(event.get("home", "")) == target_home
            and _normalize_name(event.get("away", "")) == target_away
        ):
            matches.append(event)

    if not matches:
        return None

    try:
        kickoff_dt = datetime.strptime(kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
        matches.sort(
            key=lambda event: abs(
                datetime.fromisoformat(event.get("date", "").replace("Z", "+00:00")) - kickoff_dt
            )
        )
    except Exception:
        pass

    event_id = matches[0].get("id")
    try:
        return int(event_id) if event_id is not None else None
    except Exception:
        return None


def _extract_bookmaker_odds(data: dict, bookmaker_name: str) -> dict:
    """
    Extrai odds por mercado. Para Spread e Totals guarda todas as linhas
    (pode haver múltiplos handicaps/lines), acessíveis via _all.
    """
    bookmakers = data.get("bookmakers", {}) if isinstance(data, dict) else {}
    bookmaker_rows = bookmakers.get(bookmaker_name, [])
    result = {}
    for mkt in bookmaker_rows:
        name = mkt.get("name", "")
        odds_list = mkt.get("odds", [])
        if odds_list:
            result[name] = odds_list[0]
            # Guardar todas as linhas para match preciso por handicap/line
            if name in ("Spread", "Totals", "Draw No Bet") and len(odds_list) > 1:
                result[f"{name}_all"] = odds_list
    return result


def _analyse_event(event_data: dict) -> Optional[ValueBet]:
    """
    Analisa um evento e devolve o melhor pick (maior edge, SingBet ainda fechada).
    Candidatos por ordem: AH principal → DNB real → ML
    Totals: Over vs Under — escolhe o de maior edge.
    """
    bms      = event_data.get("bookmakers", {})
    b365_raw = bms.get("Bet365", [])
    singbet_raw  = bms.get("SingBet", [])

    if not b365_raw:
        return None

    b365 = _extract_markets(b365_raw)
    singbet  = _extract_markets(singbet_raw) if singbet_raw else {}

    home  = event_data.get("home", "")
    away  = event_data.get("away", "")
    game  = f"{home} vs {away}"

    league_raw = event_data.get("league", event_data.get("leagueName", ""))
    league = league_raw.get("name", "") if isinstance(league_raw, dict) else league_raw
    league_slug = league_raw.get("slug", "") if isinstance(league_raw, dict) else event_data.get("leagueSlug", "")

    kickoff  = _kickoff_str(event_data.get("date", event_data.get("startTime", "")))
    event_id = event_data.get("id", 0)

    if league not in ALLOWED_LEAGUES:
        return None

    # Verifica data
    try:
        dt = datetime.strptime(kickoff, "%d/%m/%Y %H:%M")
        min_dt = datetime.strptime(min_kickoff_date(), "%Y-%m-%d")
        if dt < min_dt:
            return None
    except Exception:
        pass

    best_edge = 0.0
    best_vb: Optional[ValueBet] = None

    MKT_TYPE = {"ML": "ML", "DNB": "DNB", "Spread": "AH", "Totals": "OU"}

    # ── ML / DNB / AH por equipa ─────────────────────────────────────────────
    b_ml  = b365.get("ML", {})
    b_ah  = b365.get("Spread", {})
    b_dnb = b365.get("Draw No Bet", {})
    singbet_ml = singbet.get("ML", {})
    singbet_ah = singbet.get("Spread", {})
    singbet_dnb = singbet.get("Draw No Bet", {})

    draw_odd = _float(b_ml.get("draw")) or None

    for side, team in [("home", home), ("away", away)]:
        candidates = []
        dnb_odd = _float(b_dnb.get(side))

        # 1. AH principal
        ah_odd = _float(b_ah.get(side))
        ah_hdp = _float(b_ah.get("hdp") or 0)
        if ah_odd:
            singbet_odd = _float(singbet_ah.get(side)) or None
            if ah_hdp == 0:
                # AH 0 e equivalente a DNB. Se a API ja trouxer DNB, evitamos
                # alertas duplicados para o mesmo jogo.
                if not dnb_odd:
                    candidates.append((ah_odd, "DNB", team,
                                       None, b_ah.get("href", ""), singbet_odd))
            else:
                sign = f"{ah_hdp:+.2f}"
                candidates.append((ah_odd, "Spread", f"{team} {sign}".strip(),
                                    ah_hdp, b_ah.get("href", ""), singbet_odd))

        # 2. Draw No Bet real da API
        if dnb_odd:
            singbet_odd = _float(singbet_dnb.get(side)) or None
            candidates.append((dnb_odd, "DNB", team,
                                None, b_dnb.get("href", ""), singbet_odd))

        # 3. ML directo
        ml_odd = _float(b_ml.get(side))
        if ml_odd:
            singbet_odd = _float(singbet_ml.get(side)) or None
            candidates.append((ml_odd, "ML", team,
                                None, b_ml.get("href", ""), singbet_odd))

        for odd, mkt, sel, hdp_val, href, singbet_odd in candidates:
            # Só early bets — SingBet ainda não abriu
            if singbet_odd is not None:
                continue
            result = is_value_bet(odd, market=MKT_TYPE.get(mkt, "ML"), league=league)
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, league_slug=league_slug, kickoff=kickoff,
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
                    odds_singbet=None,
                )

    # ── Over/Under (Totals) — um pick por jogo ────────────────────────────────
    b_ou   = b365.get("Totals", {})
    singbet_ou = singbet.get("Totals", {})
    if b_ou:
        line      = _float(b_ou.get("max") or b_ou.get("hdp") or 0)
        over_odd  = _float(b_ou.get("over") or b_ou.get("home"))
        under_odd = _float(b_ou.get("under") or b_ou.get("away"))
        href_ou   = b_ou.get("href", "")

        for direction, odd, opp in [("Over", over_odd, under_odd),
                                    ("Under", under_odd, over_odd)]:
            singbet_key = "over" if direction == "Over" else "under"
            singbet_odd = _float(singbet_ou.get(singbet_key) or 0) or None
            if singbet_odd is not None:
                continue
            result = is_value_bet(odd, market="OU", league=league)
            if result and result["edge_pct"] > best_edge:
                best_edge = result["edge_pct"]
                best_vb = ValueBet(
                    game=game, home_team=home, away_team=away,
                    league=league, league_slug=league_slug, kickoff=kickoff,
                    market="Totals", selection=f"{direction} {line}",
                    odds_b365=odd,
                    fair_odd=result["fair_odd"],
                    min_odd=result["min_odd"],
                    edge_pct=result["edge_pct"],
                    level=result["level"],
                    event_id=event_id,
                    line=line, opp_odd=opp,
                    bet_href=href_ou,
                    odds_singbet=None,
                )

    return best_vb


def fetch_events_for_league(slug: str) -> list[dict]:
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


def fetch_events() -> list[dict]:
    all_events = []
    for slug in LEAGUE_SLUGS:
        events = fetch_events_for_league(slug)
        all_events.extend(events)
    logger.info(f"fetch_events: {len(all_events)} eventos nas ligas alvo")
    return all_events


def fetch_updated_event_ids(since_ts: int) -> list[int]:
    """
    Usa /odds/updated para ir buscar apenas eventos alterados desde o ultimo
    scan recente. A API espera o sport slug em minusculas: football.
    """
    try:
        data = _get(
            "/odds/updated",
            {
                "sport": "football",
                "since": since_ts,
                "bookmaker": "Bet365",
            },
        )
    except Exception as e:
        logger.warning(f"fetch_updated_event_ids: {e}")
        return []

    if isinstance(data, dict):
        data = data.get("data", data.get("events", []))
    if not isinstance(data, list):
        return []

    event_ids = []
    for item in data:
        event_id = item.get("id") or item.get("eventId")
        if event_id:
            try:
                event_ids.append(int(event_id))
            except Exception:
                continue
    logger.info(f"fetch_updated_event_ids: {len(event_ids)} eventos alterados")
    return event_ids


def fetch_odds_multi(event_ids: list[int]) -> list[dict]:
    """Busca odds em batch via /odds/multi sem perder mercados."""
    results = []
    for batch in _chunked(event_ids, 10):
        data = None
        try:
            data = _get(
                "/odds/multi",
                {
                    "eventIds": ",".join(str(event_id) for event_id in batch),
                    "bookmakers": "Bet365,SingBet",
                },
            )
        except Exception as e:
            logger.warning(f"fetch_odds_multi {batch}: {e}")
            continue

        if isinstance(data, list) and data:
            results.extend(data)
        elif isinstance(data, dict) and data:
            results.append(data)

    logger.info(f"fetch_odds_multi: {len(results)} eventos com odds")
    return results


def fetch_value_bets() -> list[ValueBet]:
    """Detecta early value bets na Bet365 usando o modelo calibrado."""
    now = datetime.now(timezone.utc)
    min_dt = datetime.strptime(min_kickoff_date(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    max_dt = now + timedelta(days=35)
    state = _load_state()

    def _date_ok(ev: dict) -> bool:
        try:
            dt = datetime.fromisoformat(
                ev.get("date", ev.get("startTime", "")).replace("Z", "+00:00")
            )
            return min_dt <= dt <= max_dt
        except Exception:
            return False

    event_ids: list[int] = []
    use_incremental = False
    last_since = state.get("last_updated_since")

    if isinstance(last_since, int):
        age_seconds = int(now.timestamp()) - last_since
        if 0 <= age_seconds <= 55:
            event_ids = fetch_updated_event_ids(last_since)
            use_incremental = True

    if not use_incremental:
        events = fetch_events()
        if not events:
            return []

        relevant = [ev for ev in events if _get_league_name(ev) in ALLOWED_LEAGUES]
        logger.info(f"Eventos nas ligas alvo: {len(relevant)}")
        if not relevant:
            return []

        relevant = [ev for ev in relevant if _date_ok(ev)]
        logger.info(f"Eventos na janela: {len(relevant)}")
        event_ids = [ev.get("id") for ev in relevant if ev.get("id")]
    elif not event_ids:
        logger.info("Scan incremental sem alteracoes")
        state["last_updated_since"] = int(now.timestamp())
        _save_state(state)
        return []

    odds_data = fetch_odds_multi(event_ids)
    odds_data = [
        ev for ev in odds_data
        if _get_league_name(ev) in ALLOWED_LEAGUES and _date_ok(ev)
    ]

    value_bets = []
    for event_odds in odds_data:
        try:
            vb = _analyse_event(event_odds)
            if vb:
                value_bets.append(vb)
        except Exception as e:
            logger.warning(f"_analyse_event: {e}")

    state["last_updated_since"] = int(now.timestamp())
    _save_state(state)
    logger.info(f"Value bets encontradas: {len(value_bets)}")
    return value_bets


REFERENCE_BOOKMAKERS = ("Sbobet", "Stake")


def _fetch_historical_bookmaker_odds(event_id: int, bookmaker_name: str) -> dict:
    data = _get("/historical/odds", {"eventId": event_id, "bookmakers": bookmaker_name})
    return _extract_bookmaker_odds(data, bookmaker_name)


def fetch_reference_closing_odds(
    event_id: int,
    league_slug: str = "",
    kickoff: str = "",
    home_team: str = "",
    away_team: str = "",
) -> tuple[dict, int | None]:
    """
    Busca odds históricas de fecho da Sbobet e Stake para apurar CLV real.
    Primeiro tenta o event_id já guardado; se falhar, resolve o id histórico
    através de /historical/events.
    """
    direct: dict[str, dict] = {}
    for bookmaker_name in REFERENCE_BOOKMAKERS:
        try:
            odds = _fetch_historical_bookmaker_odds(event_id, bookmaker_name)
            if odds:
                direct[bookmaker_name] = odds
        except Exception as e:
            logger.warning(f"fetch_reference_closing_odds direct {event_id} {bookmaker_name}: {e}")
    if direct:
        return direct, event_id

    historical_event_id = fetch_historical_event_id(
        league_slug=league_slug,
        kickoff=kickoff,
        home_team=home_team,
        away_team=away_team,
    )
    if not historical_event_id:
        return {}, None

    historical: dict[str, dict] = {}
    for bookmaker_name in REFERENCE_BOOKMAKERS:
        try:
            odds = _fetch_historical_bookmaker_odds(historical_event_id, bookmaker_name)
            if odds:
                historical[bookmaker_name] = odds
        except Exception as e:
            logger.warning(f"fetch_reference_closing_odds historical {historical_event_id} {bookmaker_name}: {e}")
    return historical, historical_event_id
