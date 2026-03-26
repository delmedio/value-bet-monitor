"""
monitor.py — Script principal do Value Bet Monitor
Corre de 30 em 30 minutos via GitHub Actions
Detecta value bets em 38 ligas e envia alertas Telegram
Guarda picks e faz tracking de CLV real via BetInAsia (OddsPortal)
"""

import os
import json
import logging
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from model import is_value_bet, MIN_EDGE_PCT
from alert import send_message, format_value_bet_alert, format_scan_summary, send_test_message
from scraper_oddsportal import scrape_all_leagues, GameOdds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# Ficheiro de cache para evitar alertas duplicados
CACHE_FILE = Path("sent_alerts.json")
# Máximo de alertas por scan (para não spammar)
MAX_ALERTS_PER_SCAN = 10


def load_cache() -> set:
    """Carrega cache de alertas já enviados."""
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            return set(data.get("sent", []))
        except Exception:
            return set()
    return set()


def save_cache(cache: set) -> None:
    """Guarda cache de alertas enviados."""
    # Mantém só os últimos 500 para não crescer indefinidamente
    cache_list = list(cache)[-500:]
    CACHE_FILE.write_text(json.dumps({"sent": cache_list}))


def make_alert_key(game: str, market: str, selection: str, odd: float) -> str:
    """Cria chave única para um alerta."""
    raw = f"{game}|{market}|{selection}|{odd:.3f}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def analyse_game(game: GameOdds) -> list[dict]:
    """
    Analisa um jogo e retorna lista de value bets encontradas.
    Analisa os mercados 1X2 (Match Odds).
    A análise de O/U e AH será feita pelo scraper da Stake.
    """
    value_bets = []

    # Match Odds — analisa as 3 odds
    markets_to_check = [
        (game.odd_1, "Match Odds", game.home),
        (game.odd_x, "Match Odds", "Empate"),
        (game.odd_2, "Match Odds", game.away),
    ]

    for odd, market, selection in markets_to_check:
        if odd is None:
            continue
        result = is_value_bet(odd)
        if result:
            value_bets.append({
                "game": game.game,
                "league": game.league,
                "kickoff": game.kickoff,
                "market": market,
                "selection": selection,
                "bookmaker": game.bookmaker,
                "url": game.url,
                **result,
            })

    return value_bets


def run_monitor(test_mode: bool = False) -> None:
    """Função principal do monitor."""
    log.info("=" * 50)
    log.info("VALUE BET MONITOR — A iniciar scan")
    log.info("=" * 50)

    if test_mode:
        log.info("MODO TESTE — a enviar mensagem de teste")
        send_test_message()
        return

    # Carrega cache
    sent_cache = load_cache()
    log.info(f"Cache carregado: {len(sent_cache)} alertas anteriores")

    # Scraping
    log.info("A fazer scraping das ligas...")
    try:
        games = scrape_all_leagues()
    except Exception as e:
        log.error(f"Erro crítico no scraping: {e}")
        send_message(f"⚠️ Erro no monitor: {e}")
        return

    log.info(f"Total jogos encontrados: {len(games)}")

    # Análise
    all_value_bets = []
    for game in games:
        vbs = analyse_game(game)
        all_value_bets.extend(vbs)

    # Filtra já enviados
    new_value_bets = []
    for vb in all_value_bets:
        key = make_alert_key(vb["game"], vb["market"], vb["selection"], vb["opening_odd"])
        if key not in sent_cache:
            new_value_bets.append((key, vb))

    log.info(f"Value bets novas: {len(new_value_bets)}")

    # Ordena por edge descendente
    new_value_bets.sort(key=lambda x: x[1]["edge_pct"], reverse=True)

    # Envia alertas (máximo MAX_ALERTS_PER_SCAN)
    sent_count = 0
    elite_count = 0
    strong_count = 0
    normal_count = 0

    for key, vb in new_value_bets[:MAX_ALERTS_PER_SCAN]:
        msg = format_value_bet_alert(
            game=vb["game"],
            league=vb["league"],
            kickoff=vb["kickoff"],
            market=vb["market"],
            selection=vb["selection"],
            bookmaker=vb["bookmaker"],
            opening_odd=vb["opening_odd"],
            fair_odd=vb["fair_odd"],
            min_odd=vb["min_odd"],
            edge_pct=vb["edge_pct"],
            level=vb["level"],
        )

        if send_message(msg):
            sent_cache.add(key)
            sent_count += 1
            if "Elite" in vb["level"]:
                elite_count += 1
            elif "Strong" in vb["level"]:
                strong_count += 1
            else:
                normal_count += 1

    # Guarda cache actualizado
    save_cache(sent_cache)

    # Resumo — só envia se houver value bets ou for a primeira vez
    summary = format_scan_summary(
        total_games=len(games),
        value_bets=sent_count,
        elite=elite_count,
        strong=strong_count,
        normal=normal_count,
        leagues_scanned=len(set(g.league for g in games)),
    )
    send_message(summary)

    log.info(f"Scan completo: {sent_count} alertas enviados")
    log.info("=" * 50)


if __name__ == "__main__":
    import sys
    test_mode = "--test" in sys.argv
    run_monitor(test_mode=test_mode)
    
