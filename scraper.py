"""
scraper.py — Busca odds via The Odds API (plano 100K)
Bookmaker principal: 1xBet (onexbet) — abre jogos muito cedo, AH + Goal Line completos
Pinnacle incluído como referência para CLV
Sport keys validados em 26/03/2026
"""

import os
import time
import logging
import requests
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

LEAGUE_KEYS = {
    "Liga Portugal 1":        "soccer_portugal_primeira_liga",
    "La Liga":                "soccer_spain_la_liga",
    "La Liga 2":              "soccer_spain_segunda_division",
    "Premier League":         "soccer_epl",
    "Championship":           "soccer_efl_champ",
    "League 1":               "soccer_england_league1",
    "Serie A":                "soccer_italy_serie_a",
    "Bundesliga":             "soccer_germany_bundesliga",
    "2. Bundesliga":          "soccer_germany_bundesliga2",
    "Ligue 1":                "soccer_france_ligue_one",
    "Eredivisie":             "soccer_netherlands_eredivisie",
    "Scottish Premiership":   "soccer_spl",
    "Jupiler Pro League":     "soccer_belgium_first_div",
    "Super League Greece":    "soccer_greece_super_league",
    "Eliteserien":            "soccer_norway_eliteserien",
    "Allsvenskan":            "soccer_sweden_allsvenskan",
    "Superliga Denmark":      "soccer_denmark_superliga",
    "Veikkausliiga":          "soccer_finland_veikkausliiga",
    "Swiss Superleague":      "soccer_switzerland_superleague",
    "Bundesliga Austria":     "soccer_austria_bundesliga",
    "Champions League":       "soccer_uefa_champs_league",
    "Europa League":          "soccer_uefa_europa_league",
    "Conference League":      "soccer_uefa_europa_conference_league",
    "Serie A Brazil":         "soccer_brazil_campeonato",
    "Serie B Brazil":         "soccer_brazil_serie_b",
    "Primera Division AR":    "soccer_argentina_primera_division",
    "Copa Libertadores":      "soccer_conmebol_copa_libertadores",
    "Copa Sudamericana":      "soccer_conmebol_copa_sudamericana",
    "Liga MX":                "soccer_mexico_ligamx",
    "MLS":                    "soccer_usa_mls",
    "FIFA World Cup":         "soccer_fifa_world_cup",
    "J1 League":              "soccer_japan_j_league",
    "K League 1":             "soccer_korea_kleague1",
    "A-League":               "soccer_australia_aleague",
    "Chinese Super League":   "soccer_china_superleague",
}


@dataclass
class GameOdds:
    event_id: str
    game: str
    home: str
    away: str
    league: str
    sport_key: str
    kickoff: str
    kickoff_ts: float
    # 1xBet odds
    odds_1: float | None = None
    odds_x: float | None = None
    odds_2: float | None = None
    odds_ou_line: float | None = None
    odds_over: float | None = None
    odds_under: float | None = None
    odds_ah_line: float | None = None
    odds_ah_home: float | None = None
    odds_ah_away: float | None = None
    # Pinnacle (referência CLV)
    pin_1: float | None = None
    pin_x: float | None = None
    pin_2: float | None = None
    bookmaker: str = "1xBet"


def get_remaining(headers: dict) -> int:
    try:
        return int(headers.get("x-requests-remaining", 9999))
    except Exception:
        return 9999


def fetch_league(sport_key: str, league_name: str) -> list[GameOdds]:
    games = []
    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,spreads",
        "bookmakers": "onexbet,pinnacle",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        remaining = get_remaining(r.headers)
        log.info(f"{league_name}: status {r.status_code} | requests restantes: {remaining}")

        if r.status_code == 401:
            log.error("API key inválida")
            return games
        if r.status_code in (404, 422):
            log.warning(f"Liga não disponível: {sport_key}")
            return games
        if r.status_code != 200:
            log.warning(f"Erro {r.status_code} para {league_name}")
            return games

        for ev in r.json():
            game = parse_event(ev, league_name, sport_key)
            if game:
                games.append(game)

    except Exception as e:
        log.error(f"Erro em {league_name}: {e}")

    return games


def parse_event(ev: dict, league_name: str, sport_key: str) -> GameOdds | None:
    try:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")
        dt = datetime.fromisoformat(
            ev.get("commence_time", "").replace("Z", "+00:00"))

        game = GameOdds(
            event_id=ev.get("id", ""),
            game=f"{home} vs {away}",
            home=home, away=away,
            league=league_name, sport_key=sport_key,
            kickoff=dt.strftime("%d/%m/%Y %H:%M"),
            kickoff_ts=dt.timestamp(),
        )

        for bm in ev.get("bookmakers", []):
            key = bm.get("key", "")
            for market in bm.get("markets", []):
                mk = market.get("key", "")
                outcomes = market.get("outcomes", [])

                if key == "onexbet":
                    if mk == "h2h":
                        for o in outcomes:
                            if o["name"] == home:      game.odds_1 = o["price"]
                            elif o["name"] == "Draw":  game.odds_x = o["price"]
                            elif o["name"] == away:    game.odds_2 = o["price"]
                    elif mk == "totals":
                        for o in outcomes:
                            if o["name"] == "Over":
                                game.odds_over = o["price"]
                                game.odds_ou_line = o.get("point")
                            elif o["name"] == "Under":
                                game.odds_under = o["price"]
                    elif mk == "spreads":
                        for o in outcomes:
                            if o["name"] == home:
                                game.odds_ah_home = o["price"]
                                game.odds_ah_line = o.get("point")
                            elif o["name"] == away:
                                game.odds_ah_away = o["price"]

                elif key == "pinnacle":
                    if mk == "h2h":
                        for o in outcomes:
                            if o["name"] == home:      game.pin_1 = o["price"]
                            elif o["name"] == "Draw":  game.pin_x = o["price"]
                            elif o["name"] == away:    game.pin_2 = o["price"]

        return game
    except Exception as e:
        log.debug(f"Erro ao parsear evento: {e}")
        return None


def fetch_closing_odds(event_id: str, sport_key: str) -> dict | None:
    """Busca odds históricas (fecho) da Pinnacle."""
    url = f"{BASE_URL}/sports/{sport_key}/events/{event_id}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,spreads",
        "bookmakers": "pinnacle",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return None
        result = {}
        for bm in r.json().get("bookmakers", []):
            if bm.get("key") != "pinnacle":
                continue
            for market in bm.get("markets", []):
                mk = market.get("key")
                for o in market.get("outcomes", []):
                    name = o["name"].lower().replace(" ", "_")
                    result[f"pin_close_{mk}_{name}"] = o["price"]
                    if o.get("point") is not None:
                        result[f"pin_close_{mk}_line"] = o["point"]
        return result if result else None
    except Exception as e:
        log.error(f"Erro ao buscar fecho: {e}")
        return None


def fetch_all_leagues(min_kickoff_date: str | None = None) -> list[GameOdds]:
    if not ODDS_API_KEY:
        log.error("ODDS_API_KEY não definida")
        return []

    try:
        r = requests.get(f"{BASE_URL}/sports",
                        params={"apiKey": ODDS_API_KEY}, timeout=10)
        remaining = get_remaining(r.headers)
        log.info(f"Requests disponíveis: {remaining}")
        if remaining < 50:
            log.warning("Poucos requests — a parar")
            return []
    except Exception as e:
        log.warning(f"Erro ao verificar requests: {e}")

    min_ts = None
    if min_kickoff_date:
        try:
            min_ts = datetime.strptime(
                min_kickoff_date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            pass

    all_games = []
    for league_name, sport_key in LEAGUE_KEYS.items():
        games = fetch_league(sport_key, league_name)
        if min_ts:
            games = [g for g in games if g.kickoff_ts >= min_ts]
        all_games.extend(games)
        time.sleep(0.3)

    log.info(f"Total: {len(all_games)} jogos em {len(LEAGUE_KEYS)} ligas")
    return all_games
