"""
monitor.py — Script principal do Value Bet Monitor
Odds API 100K | 37 ligas | Filtro: jogos a partir de 15 Abr 2026
"""

import os
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from model import is_value_bet, MIN_KICKOFF_DATE
from scraper import fetch_all_leagues, GameOdds
from tracker import save_pick, track_pending_picks, make_pick_id, Pick
from alert import send_telegram, format_alert, format_scan_summary, send_test_message, send_weekly_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

CACHE_FILE = Path("sent_alerts.json")
MAX_ALERTS_PER_SCAN = 15


def load_cache() -> set:
    if CACHE_FILE.exists():
        try:
            return set(json.loads(CACHE_FILE.read_text()).get("sent", []))
        except Exception:
            return set()
    return set()


def save_cache(cache: set) -> None:
    CACHE_FILE.write_text(json.dumps({"sent": list(cache)[-500:]}))


def analyse_game(game: GameOdds) -> list[dict]:
    """Analisa todos os mercados de um jogo. Retorna só o melhor por jogo."""
    candidates = []

    # Match Odds — sem empates
    for odd, selection in [(game.b365_1, game.home), (game.b365_2, game.away)]:
        if odd:
            r = is_value_bet(odd)
            if r:
                candidates.append((r["edge_pct"], "Match Odds", selection, odd, r))

    # Over/Under
    if game.b365_ou_line:
        for odd, side in [(game.b365_over, "Over"), (game.b365_under, "Under")]:
            if odd:
                r = is_value_bet(odd)
                if r:
                    candidates.append((r["edge_pct"], "Over/Under",
                                      f"{side} {game.b365_ou_line}", odd, r))

    # Asian Handicap
    if game.b365_ah_line:
        home_line = game.b365_ah_line
        away_line = -game.b365_ah_line
        for odd, team, line in [
            (game.b365_ah_home, game.home, home_line),
            (game.b365_ah_away, game.away, away_line),
        ]:
            if odd:
                r = is_value_bet(odd)
                if r:
                    sign = "+" if line >= 0 else ""
                    candidates.append((r["edge_pct"], "Asian Handicap",
                                      f"{team} {sign}{line}", odd, r))

    if not candidates:
        return []

    # Melhor mercado por jogo
    candidates.sort(key=lambda x: x[0], reverse=True)
    edge, market, selection, odd, result = candidates[0]

    return [{
        "event_id": game.event_id,
        "sport_key": game.sport_key,
        "game": game.game,
        "league": game.league,
        "kickoff": game.kickoff,
        "kickoff_ts": game.kickoff_ts,
        "market": market,
        "selection": selection,
        "opening_odd": odd,
        **result,
    }]


def run_monitor(test_mode: bool = False, report_mode: bool = False) -> None:
    log.info("=" * 50)
    log.info("VALUE BET MONITOR — A iniciar")
    log.info("=" * 50)

    if test_mode:
        send_test_message()
        return

    if report_mode:
        log.info("A gerar report semanal...")
        send_weekly_report(days=7)
        return

    # Tracking de picks pendentes (CLV real via Pinnacle)
    log.info("A verificar picks pendentes...")
    try:
        newly_tracked = track_pending_picks()
        for pick in newly_tracked:
            emoji = "✅" if pick.clv_real >= 0 else "❌"
            send_telegram(
                f"{emoji} <b>CLV Real apurado</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"🏟 {pick.game}\n"
                f"📌 {pick.market} — {pick.selection}\n"
                f"💰 Abertura: {pick.opening_odd:.3f}\n"
                f"📉 Fecho Pinnacle: {pick.pin_closing_odd:.3f}\n"
                f"📈 CLV real: <b>{pick.clv_real:+.1f}%</b>"
            )
    except Exception as e:
        log.error(f"Erro no tracking: {e}")

    # Busca odds
    sent_cache = load_cache()
    log.info(f"A buscar odds (filtro: jogos ≥ {MIN_KICKOFF_DATE})...")

    try:
        games = fetch_all_leagues(min_kickoff_date=MIN_KICKOFF_DATE)
    except Exception as e:
        log.error(f"Erro crítico: {e}")
        send_telegram(f"⚠️ Erro no monitor: {e}")
        return

    log.info(f"Jogos encontrados: {len(games)}")

    # Análise
    all_vbs = []
    for game in games:
        all_vbs.extend(analyse_game(game))

    # Remove duplicados
    new_vbs = []
    for vb in all_vbs:
        key = make_pick_id(vb["game"], vb["market"], vb["selection"], vb["opening_odd"])
        if key not in sent_cache:
            new_vbs.append((key, vb))

    new_vbs.sort(key=lambda x: x[1]["edge_pct"], reverse=True)
    log.info(f"Value bets novas: {len(new_vbs)}")

    # Envia alertas
    sent = elite = strong = normal = 0
    for key, vb in new_vbs[:MAX_ALERTS_PER_SCAN]:
        msg = format_alert(
            game=vb["game"], league=vb["league"], kickoff=vb["kickoff"],
            market=vb["market"], selection=vb["selection"],
            opening_odd=vb["opening_odd"], fair_odd=vb["fair_odd"],
            min_odd=vb["min_odd"], edge_pct=vb["edge_pct"], level=vb["level"],
        )
        if send_telegram(msg):
            sent_cache.add(key)
            sent += 1
            save_pick(Pick(
                id=key,
                event_id=vb["event_id"],
                sport_key=vb["sport_key"],
                game=vb["game"], league=vb["league"],
                kickoff=vb["kickoff"], kickoff_ts=vb["kickoff_ts"],
                market=vb["market"], selection=vb["selection"],
                bookmaker="Bet365",
                opening_odd=vb["opening_odd"], fair_odd=vb["fair_odd"],
                min_odd=vb["min_odd"], edge_pct=vb["edge_pct"], level=vb["level"],
                alerted_at=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M"),
            ))
            if "Elite" in vb["level"]: elite += 1
            elif "Strong" in vb["level"]: strong += 1
            else: normal += 1

    save_cache(sent_cache)
    send_telegram(format_scan_summary(
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
