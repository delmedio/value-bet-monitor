"""
scraper_oddsapi.py — Busca odds via The Odds API
Substitui o scraper do OddsPortal que requeria Playwright
Documentação: https://the-odds-api.com/
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

# Mapeamento liga → sport_key da Odds API
LEAGUE_KEYS = {
    "Liga Portugal 1":      "soccer_portugal_primeira_liga",
    "Liga Portugal 2":      "soccer_portugal_segunda_liga",
    "La Liga":              "soccer_spain_la_liga",
    "La Liga 2":            "soccer_spain_segunda_division",
    "Premier League":       "soccer_epl",
    "Championship":         "soccer_england_championship",
    "Serie A":              "soccer_italy_serie_a",
    "Serie B":              "soccer_italy_serie_b",
    "Bundesliga":           "soccer_germany_bundesliga",
    "2. Bundesliga":        "soccer_germany_bundesliga2",
    "Eredivisie":           "soccer_netherlands_eredivisie",
    "Scottish Premiership": "soccer_scotland_premiership",
    "Jupiler Pro League":   "soccer_belgium_first_div",
    "Super League Greece":  "soccer_greece_super_league",
    "Eliteserien":          "soccer_norway_eliteserien",
    "Allsvenskan":          "soccer_sweden_allsvenskan",
    "Superliga Denmark":    "soccer_denmark_superliga",
    "Champions League":     "soccer_uefa_champs_league",
    "Europa League":        "soccer_uefa_europa_league",
    "Conference League":    "soccer_uefa_europa_conference_league",
    "Serie A Brazil":       "soccer_brazil_campeonato",
    "Serie B Brazil":       "soccer_brazil_serie_b",
    "Primera Division AR":  "soccer_argentina_primera_division",
    "Copa Libertadores":    "soccer_conmebol_copa_libertadores",
    "Copa Sudamericana":    "soccer_conmebol_copa_sudamericana",
    "Liga MX":              "soccer_mexico_ligamx",
    "MLS":                  "soccer_usa_mls",
    "CONCACAF Champ. Cup":  "soccer_concacaf_champions_cup",
    "J1 League":            "soccer_japan_j_league",
    "A-League":             "soccer_australia_aleague",
    "Chinese Super League": "soccer_china_superleague",
    "Liga Colombia":        "soccer_colombia_primera_a",
    "Primera Chile":        "soccer_chile_primera_division",
    "LigaPro Ecuador":      "soccer_ecuador_liga_pro",
    "Bundesliga Austria":   "soccer_austria_bundesliga",
    "Czech First League":   "soccer_czech_republic_first_league",
    "First League Bulgaria":"soccer_bulgaria_first_professional_league",
}

# Bookmakers a buscar — Bet365 é o alvo, Pinnacle como referência
BOOKMAKERS = "bet365,pinnacle"


@dataclass
class GameOdds:
    game: str
    home: str
    away: str
    league: str
    kickoff: str
    kickoff_ts: float
    url: str
    odd_1: float | None = None
    odd_x: float | None = None
    odd_2: float | None = None
    ou_line: float | None = None
    ou_over: float | None = None
    ou_under: float | None = None
    ah_line: float | None = None
    ah_home: float | None = None
    ah_away: float | None = None
    bookmaker: str = "Bet365"


def get_remaining_requests() -> int | None:
    """Verifica quantos requests restam no free tier."""
    url = f"{BASE_URL}/sports"
    try:
        r = requests.get(url, params={"apiKey": ODDS_API_KEY}, timeout=10)
        remaining = r.headers.get("x-requests-remaining")
        used = r.headers.get("x-requests-used")
        if remaining:
            log.info(f"Odds API — requests restantes: {remaining} | usados: {used}")
            return int(remaining)
    except Exception as e:
        log.warning(f"Erro ao verificar requests: {e}")
    return None


def fetch_odds_for_league(sport_key: str, league_name: str) -> list[GameOdds]:
    """Busca odds de uma liga via Odds API."""
    games = []

    url = f"{BASE_URL}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu",
        "markets": "h2h,totals,spreads",
        "bookmakers": BOOKMAKERS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    try:
        r = requests.get(url, params=params, timeout=15)

        # Log requests restantes
        remaining = r.headers.get("x-requests-remaining", "?")
        log.info(f"{league_name}: status {r.status_code} | requests restantes: {remaining}")

        if r.status_code == 401:
            log.error("API key inválida ou expirada")
            return games
        elif r.status_code == 422:
            log.warning(f"Liga não disponível na API: {sport_key}")
            return games
        elif r.status_code != 200:
            log.warning(f"Erro {r.status_code} para {league_name}")
            return games

        events = r.json()

        for event in events:
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            game_name = f"{home} vs {away}"

            # Kickoff
            commence = event.get("commence_time", "")
            try:
                dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                kickoff_str = dt.strftime("%d %b %Y %H:%M")
                kickoff_ts = dt.timestamp()
            except Exception:
                kickoff_str = commence
                kickoff_ts = datetime.now(timezone.utc).timestamp() + 86400

            # URL OddsPortal para tracking posterior
            home_slug = home.lower().replace(" ", "-")
            away_slug = away.lower().replace(" ", "-")
            op_url = f"https://www.oddsportal.com/football/{home_slug}-{away_slug}/"

            game = GameOdds(
                game=game_name,
                home=home,
                away=away,
                league=league_name,
                kickoff=kickoff_str,
                kickoff_ts=kickoff_ts,
                url=op_url,
            )

            # Procura odds da Bet365
            for bookmaker in event.get("bookmakers", []):
                if bookmaker.get("key") != "bet365":
                    continue

                for market in bookmaker.get("markets", []):
                    key = market.get("key")
                    outcomes = market.get("outcomes", [])

                    if key == "h2h":
                        for o in outcomes:
                            if o["name"] == home:
                                game.odd_1 = o["price"]
                            elif o["name"] == "Draw":
                                game.odd_x = o["price"]
                            elif o["name"] == away:
                                game.odd_2 = o["price"]

                    elif key == "totals":
                        for o in outcomes:
                            if o["name"] == "Over":
                                game.ou_over = o["price"]
                                game.ou_line = o.get("point")
                            elif o["name"] == "Under":
                                game.ou_under = o["price"]

                    elif key == "spreads":
                        for o in outcomes:
                            if o["name"] == home:
                                game.ah_home = o["price"]
                                game.ah_line = o.get("point")
                            elif o["name"] == away:
                                game.ah_away = o["price"]

            games.append(game)

    except Exception as e:
        log.error(f"Erro ao buscar {league_name}: {e}")

    return games


def scrape_all_leagues() -> list[GameOdds]:
    """Busca odds de todas as ligas configuradas."""
    if not ODDS_API_KEY:
        log.error("ODDS_API_KEY não definida")
        return []

    # Verifica requests disponíveis antes de começar
    remaining = get_remaining_requests()
    if remaining is not None and remaining < 10:
        log.warning(f"Poucos requests restantes: {remaining}. A parar para não esgotar.")
        return []

    all_games = []
    for league_name, sport_key in LEAGUE_KEYS.items():
        games = fetch_odds_for_league(sport_key, league_name)
        all_games.extend(games)
        time.sleep(0.5)  # Pequena pausa entre requests

    log.info(f"Total: {len(all_games)} jogos em {len(LEAGUE_KEYS)} ligas")
    return all_games
