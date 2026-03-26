"""
scraper_oddsportal.py — Scraping do OddsPortal com Playwright
Versão corrigida baseada no HTML real do OddsPortal
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

log = logging.getLogger(__name__)

LEAGUE_URLS = {
    "Liga Portugal 1":      "https://www.oddsportal.com/football/portugal/liga-portugal/",
    "Liga Portugal 2":      "https://www.oddsportal.com/football/portugal/liga-portugal-2/",
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


def parse_game_link(text: str, href: str) -> tuple[str, str, str] | None:
    """
    Extrai hora, home e away do texto do link.
    Formato OddsPortal: '15:30Gil Vicente–AFS' ou '18:00Vitoria Guimaraes–Tondela'
    Separador é '–' (en dash, U+2013) não '-' (hyphen)
    """
    # Remove hora do início (formato HH:MM)
    text = re.sub(r'^\d{1,2}:\d{2}', '', text).strip()

    # Tenta separar por en dash (–) que é o que o OddsPortal usa
    if '–' in text:
        parts = text.split('–', 1)
        home = parts[0].strip()
        away = parts[1].strip()
        if home and away:
            return home, away, f"{home} vs {away}"

    # Fallback: tenta hífen normal
    if ' - ' in text:
        parts = text.split(' - ', 1)
        home = parts[0].strip()
        away = parts[1].strip()
        if home and away:
            return home, away, f"{home} vs {away}"

    return None


def extract_kickoff(text: str, href: str) -> tuple[str, float]:
    """Extrai hora do kickoff do texto do link."""
    match = re.match(r'^(\d{1,2}:\d{2})', text)
    if match:
        kickoff_time = match.group(1)
        # Usa data de amanhã por defeito — será corrigido quando scraper for melhorado
        ts = datetime.now(timezone.utc).timestamp() + 86400
        return kickoff_time, ts
    return "TBD", datetime.now(timezone.utc).timestamp() + 86400 * 3


async def scrape_league_page(page: Page, league_name: str, url: str) -> list[GameOdds]:
    games = []
    try:
        await page.goto(url, wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(3000)

        # Aceitar cookies
        try:
            for selector in ["button:has-text('Accept')", "#onetrust-accept-btn-handler",
                             "button:has-text('I Accept')", "button:has-text('AGREE')"]:
                btn = page.locator(selector)
                if await btn.count() > 0:
                    await btn.first.click()
                    await page.wait_for_timeout(1000)
                    break
        except Exception:
            pass

        # Busca todos os links de jogos
        # O OddsPortal usa URLs como /football/country/league/home-away-HASH/
        game_data = await page.evaluate("""
            () => {
                const results = [];
                const seen = new Set();
                const anchors = document.querySelectorAll('a[href]');

                anchors.forEach(a => {
                    const href = a.href || '';
                    const text = a.textContent.trim();

                    // URL de jogo tem pelo menos 6 partes e termina com hash
                    // ex: /football/portugal/liga-portugal/gil-vicente-afs-8G2j6tEG/
                    const parts = href.split('/').filter(p => p.length > 0);
                    const lastPart = parts[parts.length - 1] || '';

                    // Hash tem 8 caracteres alfanuméricos no fim
                    const hasHash = /[A-Za-z0-9]{8}$/.test(lastPart);

                    if (parts.length >= 5 && hasHash && !seen.has(href)) {
                        // Texto tem formato HH:MMHome–Away
                        const hasTime = /^\d{1,2}:\d{2}/.test(text);
                        const hasDash = text.includes('–') || text.includes(' - ');

                        if (hasTime && hasDash && text.length < 80) {
                            seen.add(href);

                            // Extrai hora
                            const timeMatch = text.match(/^(\d{1,2}:\d{2})/);
                            const kickoff = timeMatch ? timeMatch[1] : 'TBD';

                            // Extrai nome sem hora
                            const nameRaw = text.replace(/^\d{1,2}:\d{2}/, '').trim();

                            results.push({
                                text: nameRaw,
                                url: href,
                                kickoff: kickoff,
                            });
                        }
                    }
                });
                return results;
            }
        """)

        for item in game_data:
            try:
                text = item.get("text", "")
                game_url = item.get("url", url)
                kickoff = item.get("kickoff", "TBD")

                parsed = parse_game_link(text, game_url)
                if not parsed:
                    continue

                home, away, game_name = parsed
                kickoff_ts = datetime.now(timezone.utc).timestamp() + 86400

                game = GameOdds(
                    game=game_name,
                    home=home,
                    away=away,
                    league=league_name,
                    kickoff=kickoff,
                    kickoff_ts=kickoff_ts,
                    url=game_url,
                )
                games.append(game)

            except Exception as e:
                log.debug(f"Erro ao parsear jogo: {e}")

        log.info(f"{league_name}: {len(games)} jogos encontrados")

        # Vai buscar odds de cada jogo individualmente
        games_with_odds = []
        for game in games[:20]:  # máx 20 jogos por liga para não demorar muito
            try:
                odds = await fetch_game_odds(page, game)
                games_with_odds.append(odds)
                await asyncio.sleep(1)
            except Exception as e:
                log.debug(f"Erro ao buscar odds de {game.game}: {e}")
                games_with_odds.append(game)

        return games_with_odds

    except PlaywrightTimeout:
        log.warning(f"{league_name}: timeout")
    except Exception as e:
        log.error(f"{league_name}: erro — {e}")

    return games


async def fetch_game_odds(page: Page, game: GameOdds) -> GameOdds:
    """
    Vai à página do jogo e busca as odds da Bet365.
    """
    try:
        await page.goto(game.url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)

        odds_data = await page.evaluate("""
            () => {
                const result = { h2h: [], totals: [], ah: [] };

                // Procura tabela de odds por bookmaker
                // Bet365 aparece como linha na tabela
                const rows = document.querySelectorAll('tr, div[class*="bookie-row"]');

                rows.forEach(row => {
                    const text = row.textContent || '';
                    if (text.toLowerCase().includes('bet365')) {
                        // Extrai odds desta row
                        const els = row.querySelectorAll('td, span[class*="odds"], p');
                        const odds = [];
                        els.forEach(el => {
                            const val = parseFloat(el.textContent.trim().replace(',', '.'));
                            if (!isNaN(val) && val > 1.01 && val < 30) {
                                odds.push(val);
                            }
                        });
                        if (odds.length >= 2) result.h2h = odds.slice(0, 3);
                    }
                });

                // Fallback: pega odds gerais da página se Bet365 não encontrada
                if (result.h2h.length === 0) {
                    // Procura odds médias/abertura
                    const avgEls = document.querySelectorAll('[class*="avg"], [class*="opening"]');
                    const odds = [];
                    avgEls.forEach(el => {
                        const val = parseFloat(el.textContent.trim().replace(',', '.'));
                        if (!isNaN(val) && val > 1.01 && val < 30) odds.push(val);
                    });
                    if (odds.length >= 2) result.h2h = odds.slice(0, 3);
                }

                return result;
            }
        """)

        h2h = odds_data.get("h2h", [])
        if len(h2h) >= 2:
            game.odd_1 = h2h[0]
            game.odd_x = h2h[1] if len(h2h) > 2 else None
            game.odd_2 = h2h[2] if len(h2h) > 2 else h2h[1]

    except Exception as e:
        log.debug(f"Erro ao buscar odds de {game.game}: {e}")

    return game


async def scrape_all_leagues_async() -> list[GameOdds]:
    all_games = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="Europe/London",
        )
        await context.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2}",
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
