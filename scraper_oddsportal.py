"""
scraper_oddsportal.py — Scraping do OddsPortal com Playwright
Busca odds de abertura da Bet365 + linhas alternativas para todas as ligas
"""

import asyncio
import logging
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

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


def parse_odd(text: str) -> float | None:
    try:
        val = float(str(text).strip().replace(",", "."))
        return val if 1.01 < val < 50 else None
    except (ValueError, AttributeError):
        return None


def parse_kickoff(text: str) -> tuple[str, float]:
    try:
        ts = datetime.now(timezone.utc).timestamp() + 86400 * 3
        return text.strip(), ts
    except Exception:
        return text, datetime.now(timezone.utc).timestamp() + 86400


async def scrape_league_page(page: Page, league_name: str, url: str) -> list[GameOdds]:
    games = []
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        try:
            cookie_btn = page.locator("button:has-text('Accept'), button:has-text('I Accept')")
            if await cookie_btn.count() > 0:
                await cookie_btn.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        try:
            await page.wait_for_selector("[class*='eventRow'], [class*='event-row']", timeout=15000)
        except Exception:
            log.warning(f"{league_name}: sem jogos encontrados")
            return games

        rows_data = await page.evaluate("""
            () => {
                const results = [];
                const rows = document.querySelectorAll('[class*="eventRow"], [class*="event-row"], tr[class*="deactivate"]');
                rows.forEach(row => {
                    try {
                        const nameEl = row.querySelector('a[href*="/football/"]');
                        if (!nameEl) return;
                        const name = nameEl.textContent.trim();
                        const url = nameEl.href;
                        const oddEls = row.querySelectorAll('[class*="odds"] p, [class*="odds"] span, td p');
                        const odds = [];
                        oddEls.forEach(el => {
                            const val = parseFloat(el.textContent.trim().replace(',', '.'));
                            if (!isNaN(val) && val > 1.01 && val < 50) odds.push(val);
                        });
                        const timeEl = row.querySelector('[class*="time"], [class*="date"], t-md');
                        const kickoff = timeEl ? timeEl.textContent.trim() : '';
                        if (name && name.includes(' - ')) results.push({ name, url, odds, kickoff });
                    } catch(e) {}
                });
                return results;
            }
        """)

        for row in rows_data:
            try:
                name = row.get("name", "")
                parts = name.split(" - ")
                if len(parts) < 2:
                    continue
                home = parts[0].strip()
                away = parts[1].strip()
                game_name = f"{home} vs {away}"
                game_url = row.get("url", url)
                kickoff_str = row.get("kickoff", "TBD")
                kickoff_str_fmt, kickoff_ts = parse_kickoff(kickoff_str)
                odds = row.get("odds", [])
                game = GameOdds(
                    game=game_name, home=home, away=away,
                    league=league_name, kickoff=kickoff_str_fmt,
                    kickoff_ts=kickoff_ts, url=game_url,
                    odd_1=odds[0] if len(odds) > 0 else None,
                    odd_x=odds[1] if len(odds) > 1 else None,
                    odd_2=odds[2] if len(odds) > 2 else None,
                )
                games.append(game)
            except Exception as e:
                log.debug(f"Erro ao parsear row: {e}")

        log.info(f"{league_name}: {len(games)} jogos encontrados")

    except PlaywrightTimeout:
        log.warning(f"{league_name}: timeout")
    except Exception as e:
        log.error(f"{league_name}: erro — {e}")

    return games


async def scrape_all_leagues_async() -> list[GameOdds]:
    all_games = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )
        await context.route("**/*.{png,jpg,jpeg,gif,svg,css,woff,woff2}",
                          lambda route: route.abort())
        page = await context.new_page()
        for league_name, url in LEAGUE_URLS.items():
            try:
                games = await scrape_league_page(page, league_name, url)
                all_games.extend(games)
                await asyncio.sleep(2)
            except Exception as e:
                log.error(f"Erro em {league_name}: {e}")
        await browser.close()
    log.info(f"Total: {len(all_games)} jogos em {len(LEAGUE_URLS)} ligas")
    return all_games


def scrape_all_leagues() -> list[GameOdds]:
    return asyncio.run(scrape_all_leagues_async())

