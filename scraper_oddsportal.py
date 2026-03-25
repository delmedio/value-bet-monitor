"""
scraper_oddsportal.py — Scraping de odds do OddsPortal
Busca jogos novos nas ligas configuradas e odds de abertura da Bet365
"""

import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from datetime import datetime, timezone

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.oddsportal.com/",
}

# Mapeamento liga → URL OddsPortal
LEAGUE_URLS = {
    "Liga Portugal 1":      "https://www.oddsportal.com/football/portugal/primeira-liga/",
    "Liga Portugal 2":      "https://www.oddsportal.com/football/portugal/segunda-liga/",
    "La Liga":              "https://www.oddsportal.com/football/spain/laliga/",
    "La Liga 2":            "https://www.oddsportal.com/football/spain/laliga2/",
    "Premier League":       "https://www.oddsportal.com/football/england/premier-league/",
    "Championship":         "https://www.oddsportal.com/football/england/championship/",
    "Serie A":              "https://www.oddsportal.com/football/italy/serie-a/",
    "Serie B":              "https://www.oddsportal.com/football/italy/serie-b/",
    "Bundesliga":           "https://www.oddsportal.com/football/germany/bundesliga/",
    "2. Bundesliga":        "https://www.oddsportal.com/football/germany/2-bundesliga/",
    "Eredivisie":           "https://www.oddsportal.com/football/netherlands/eredivisie/",
    "Scottish Premiership": "https://www.oddsportal.com/football/scotland/premiership/",
    "Jupiler Pro League":   "https://www.oddsportal.com/football/belgium/first-division-a/",
    "Super League Greece":  "https://www.oddsportal.com/football/greece/super-league/",
    "Eliteserien":          "https://www.oddsportal.com/football/norway/eliteserien/",
    "Allsvenskan":          "https://www.oddsportal.com/football/sweden/allsvenskan/",
    "Superliga Denmark":    "https://www.oddsportal.com/football/denmark/superliga/",
    "Champions League":     "https://www.oddsportal.com/football/europe/champions-league/",
    "Europa League":        "https://www.oddsportal.com/football/europe/europa-league/",
    "Conference League":    "https://www.oddsportal.com/football/europe/europa-conference-league/",
    "Serie A Brazil":       "https://www.oddsportal.com/football/brazil/serie-a/",
    "Serie B Brazil":       "https://www.oddsportal.com/football/brazil/serie-b/",
    "Primera Division AR":  "https://www.oddsportal.com/football/argentina/primera-division/",
    "Copa Libertadores":    "https://www.oddsportal.com/football/south-america/copa-libertadores/",
    "Copa Sudamericana":    "https://www.oddsportal.com/football/south-america/copa-sudamericana/",
    "Liga MX":              "https://www.oddsportal.com/football/mexico/liga-mx/",
    "MLS":                  "https://www.oddsportal.com/football/usa/mls/",
    "USL Championship":     "https://www.oddsportal.com/football/usa/usl-championship/",
    "CONCACAF Champ. Cup":  "https://www.oddsportal.com/football/north-central-america/concacaf-champions-cup/",
    "J1 League":            "https://www.oddsportal.com/football/japan/j1-league/",
    "A-League":             "https://www.oddsportal.com/football/australia/a-league/",
    "Chinese Super League": "https://www.oddsportal.com/football/china/super-league/",
    "Liga Colombia":        "https://www.oddsportal.com/football/colombia/primera-a/",
    "Primera Chile":        "https://www.oddsportal.com/football/chile/primera-division/",
    "LigaPro Ecuador":      "https://www.oddsportal.com/football/ecuador/liga-pro/",
    "Bundesliga Austria":   "https://www.oddsportal.com/football/austria/bundesliga/",
    "Czech First League":   "https://www.oddsportal.com/football/czech-republic/1-liga/",
    "First League Bulgaria":"https://www.oddsportal.com/football/bulgaria/first-league/",
}


@dataclass
class GameOdds:
    game: str
    home: str
    away: str
    league: str
    kickoff: str
    kickoff_dt: datetime
    url: str
    odd_1: float | None = None
    odd_x: float | None = None
    odd_2: float | None = None
    bookmaker: str = "Bet365"


def fetch_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    """Faz fetch de uma página com retry."""
    for attempt in range(3):
        try:
            time.sleep(random.uniform(1.5, 3.0))
            r = session.get(url, headers=HEADERS, timeout=20)
            if r.status_code == 200:
                return BeautifulSoup(r.text, "html.parser")
            elif r.status_code == 429:
                log.warning(f"Rate limited em {url}, aguardando...")
                time.sleep(10)
            else:
                log.warning(f"Status {r.status_code} em {url}")
        except Exception as e:
            log.warning(f"Tentativa {attempt+1} falhou para {url}: {e}")
            time.sleep(5)
    return None


def parse_odd(text: str) -> float | None:
    """Converte string de odd para float."""
    try:
        clean = text.strip().replace(",", ".")
        val = float(clean)
        return val if 1.01 < val < 50 else None
    except (ValueError, AttributeError):
        return None


def scrape_league(league_name: str, url: str, session: requests.Session) -> list[GameOdds]:
    """Faz scraping de uma liga no OddsPortal."""
    games = []
    soup = fetch_page(url, session)
    if not soup:
        return games

    # OddsPortal usa estrutura de tabela com rows de jogos
    # Cada jogo tem: data, equipas, odds 1X2
    try:
        rows = soup.select("div.eventRow, tr.deactivate, div[class*='eventRow']")
        if not rows:
            # Tenta estrutura alternativa
            rows = soup.select("[class*='border-black-borders']")

        for row in rows:
            try:
                # Nome do jogo
                participants = row.select("a[class*='participant'], div[class*='participant']")
                if len(participants) >= 2:
                    home = participants[0].get_text(strip=True)
                    away = participants[1].get_text(strip=True)
                elif participants:
                    name_parts = participants[0].get_text(strip=True).split(" - ")
                    if len(name_parts) == 2:
                        home, away = name_parts
                    else:
                        continue
                else:
                    continue

                game_name = f"{home} vs {away}"

                # Link do jogo
                link_el = row.select_one("a[href*='/football/']")
                game_url = f"https://www.oddsportal.com{link_el['href']}" if link_el else url

                # Kickoff
                time_el = row.select_one("[class*='time'], [class*='date']")
                kickoff_str = time_el.get_text(strip=True) if time_el else "TBD"

                # Odds 1X2
                odd_els = row.select("div[class*='odds-wrap'] span, td[class*='odds-nowrap'] span, p[class*='avg']")
                odds = [parse_odd(el.get_text()) for el in odd_els if parse_odd(el.get_text())]

                game = GameOdds(
                    game=game_name,
                    home=home,
                    away=away,
                    league=league_name,
                    kickoff=kickoff_str,
                    kickoff_dt=datetime.now(timezone.utc),
                    url=game_url,
                    odd_1=odds[0] if len(odds) > 0 else None,
                    odd_x=odds[1] if len(odds) > 1 else None,
                    odd_2=odds[2] if len(odds) > 2 else None,
                )
                games.append(game)

            except Exception as e:
                log.debug(f"Erro ao parsear row: {e}")
                continue

    except Exception as e:
        log.warning(f"Erro ao parsear {league_name}: {e}")

    log.info(f"{league_name}: {len(games)} jogos encontrados")
    return games


def scrape_all_leagues() -> list[GameOdds]:
    """Faz scraping de todas as ligas configuradas."""
    session = requests.Session()
    session.headers.update(HEADERS)

    all_games = []
    for league_name, url in LEAGUE_URLS.items():
        try:
            games = scrape_league(league_name, url, session)
            all_games.extend(games)
            # Pausa entre ligas para não ser bloqueado
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            log.error(f"Erro em {league_name}: {e}")

    log.info(f"Total: {len(all_games)} jogos em {len(LEAGUE_URLS)} ligas")
    return all_games
