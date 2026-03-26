"""
monitor.py — Script principal do Value Bet Monitor
Corre de 30 em 30 minutos via GitHub Actions
Usa The Odds API para buscar odds (Bet365 + Pinnacle)
"""

import os
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from model import is_value_bet
from alert import send_message, format_value_bet_alert, format_scan_summary, send_test_message
from scraper_oddsapi import scrape_all_leagues, GameOdds
from tracker import save_pick, track_pending_picks, make_pick_id, Pick

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CACHE_FILE = Path("sent_alerts.json")
MAX_ALERTS_PER_SCAN = 10


def load_cache() -> set:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            return set(data.get("sent", []))
        except Exception:
            return set()
    return set()


def save_cache(cache: set) -> None:
    cache_list = list(cache)[-500:]
    CACHE_FILE.write_text(json.dumps({"sent": cache_list}))


def analyse_game(game: GameOdds) -> list[dict]:
    """Analisa todos os mercados de um jogo e retorna value bets."""
    value_bets = []

    # Match Odds 1X2
    for odd, market, selection in [
        (game.odd_1, "Match Odds", game.home),
        (game.odd_x, "Match Odds", "Empate"),
        (game.odd_2, "Match Odds", game.away),
    ]:
        if odd:
            result = is_value_bet(odd)
            if result:
                value_bets.append({
                    "game": game.game, "league": game.league,
                    "kickoff": game.kickoff, "kickoff_ts": game.kickoff_ts,
                    "market": market, "selection": selection,
                    "bookmaker": "Bet365", "url": game.url, **result,
                })

    # Over/Under
    if game.ou_line:
        for odd, side in [(game.ou_over, "Over"), (game.ou_under, "Under")]:
            if odd:
                result = is_value_bet(odd)
                if result:
                    value_bets.append({
                        "game": game.game, "league": game.league,
                        "kickoff": game.kickoff, "kickoff_ts": game.kickoff_ts,
                        "market": "Over/Under",
                        "selection": f"{side} {game.ou_line}",
                        "bookmaker": "Bet365", "url": game.url, **result,
                    })

    # Asian Handicap
    if game.ah_line:
        for odd, team, line in [
            (game.ah_home, game.home, game.ah_line),
            (game.ah_away, game.away, -game.ah_line),
        ]:
            if odd:
                result = is_value_bet(odd)
                if result:
                    sign = "+" if line >= 0 else ""
                    value_bets.append({
                        "game": game.game, "league": game.league,
                        "kickoff": game.kickoff, "kickoff_ts": game.kickoff_ts,
                        "market": "Asian Handicap",
                        "selection": f"{team} {sign}{line}",
                        "bookmaker": "Bet365", "url": game.url, **result,
                    })

    return value_bets


def run_monitor(test_mode: bool = False, report_mode: bool = False) -> None:
    log.info("=" * 50)
    log.info("VALUE BET MONITOR — A iniciar")
    log.info("=" * 50)

    # Modo teste
    if test_mode:
        log.info("MODO TESTE")
        send_test_message()
        return

    # Modo report semanal
    if report_mode:
        log.info("MODO REPORT — a gerar report semanal")
        from report import send_report_email
        success = send_report_email(days=7)
        if success:
            log.info("Report enviado com sucesso")
        else:
            log.error("Erro ao enviar report")
        return

    # Tracking de picks pendentes
    log.info("A verificar picks pendentes...")
    try:
        newly_tracked = track_pending_picks()
        for pick in newly_tracked:
            emoji = "✅" if pick.clv_real >= 0 else "❌"
            send_message(
                f"{emoji} <b>CLV Real apurado</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏟 {pick.game}\n"
                f"📌 {pick.market} — {pick.selection}\n"
                f"💰 Abertura: {pick.opening_odd:.3f}\n"
                f"📉 Fecho BetInAsia: {pick.closing_odd_betinasia:.3f}\n"
                f"📈 CLV real: <b>{pick.clv_real:+.1f}%</b>"
            )
    except Exception as e:
        log.error(f"Erro no tracking: {e}")

    # Scraping via Odds API
    sent_cache = load_cache()
    log.info("A buscar odds via Odds API...")

    try:
        games = scrape_all_leagues()
    except Exception as e:
        log.error(f"Erro crítico: {e}")
        send_message(f"⚠️ Erro no monitor: {e}")
        return

    log.info(f"Jogos encontrados: {len(games)}")

    # Análise
    all_value_bets = []
    for game in games:
        all_value_bets.extend(analyse_game(game))

    # Filtra duplicados
    new_vbs = []
    for vb in all_value_bets:
        key = make_pick_id(vb["game"], vb["market"], vb["selection"], vb["opening_odd"])
        if key not in sent_cache:
            new_vbs.append((key, vb))

    new_vbs.sort(key=lambda x: x[1]["edge_pct"], reverse=True)
    log.info(f"Value bets novas: {len(new_vbs)}")

    # Envia alertas
    sent = elite = strong = normal = 0
    for key, vb in new_vbs[:MAX_ALERTS_PER_SCAN]:
        msg = format_value_bet_alert(
            game=vb["game"], league=vb["league"], kickoff=vb["kickoff"],
            market=vb["market"], selection=vb["selection"], bookmaker=vb["bookmaker"],
            opening_odd=vb["opening_odd"], fair_odd=vb["fair_odd"],
            min_odd=vb["min_odd"], edge_pct=vb["edge_pct"], level=vb["level"],
        )
        if send_message(msg):
            sent_cache.add(key)
            sent += 1
            pick = Pick(
                id=key, game=vb["game"], league=vb["league"],
                kickoff=vb["kickoff"], kickoff_ts=vb["kickoff_ts"],
                market=vb["market"], selection=vb["selection"],
                bookmaker=vb["bookmaker"], opening_odd=vb["opening_odd"],
                fair_odd=vb["fair_odd"], min_odd=vb["min_odd"],
                edge_pct=vb["edge_pct"], level=vb["level"],
                alerted_at=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M"),
                oddsportal_url=vb["url"],
            )
            save_pick(pick)
            if "Elite" in vb["level"]: elite += 1
            elif "Strong" in vb["level"]: strong += 1
            else: normal += 1

    save_cache(sent_cache)

    send_message(format_scan_summary(
        total_games=len(games), value_bets=sent,
        elite=elite, strong=strong, normal=normal,
        leagues_scanned=len(set(g.league for g in games)),
    ))

    log.info(f"Concluído: {sent} alertas enviados")


if __name__ == "__main__":
    run_monitor(
        test_mode="--test" in sys.argv,
        report_mode="--report" in sys.argv,
    )
