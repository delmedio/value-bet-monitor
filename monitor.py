"""
monitor.py — Orquestrador principal.
"""

import os, json, logging
from pathlib import Path
from datetime import datetime, timezone

from scraper import fetch_value_bets, ValueBet
from tracker import make_pick_id, save_pick, load_picks, track_pending_picks, Pick
from alert import send_alert, send_scan_summary, send_weekly_report, send_export

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CACHE_FILE = Path("sent_alerts.json")


def load_cache() -> set:
    if CACHE_FILE.exists():
        try:
            return set(json.loads(CACHE_FILE.read_text()).get("sent", []))
        except Exception:
            pass
    return set()


def save_cache(cache: set) -> None:
    CACHE_FILE.write_text(json.dumps({"sent": list(cache)[-500:]}))


def _market_bucket(market: str) -> str | None:
    if market in ("ML", "DNB", "Spread"):
        return "side"
    if market == "Totals":
        return "totals"
    return None


def run_normal():
    logger.info("=== Scan iniciado ===")
    now_utc    = datetime.now(timezone.utc)
    scan_label = now_utc.strftime("%d/%m %H:%M UTC")

    try:
        track_pending_picks()
    except Exception as e:
        logger.warning(f"track_pending_picks: {e}")

    try:
        value_bets = fetch_value_bets()
    except Exception as e:
        logger.error(f"fetch_value_bets: {e}")
        value_bets = []

    sent_cache = load_cache()
    existing_picks = load_picks()
    counts     = {"Elite": 0, "Strong": 0, "Value": 0}
    new_alerts = 0

    for vb in value_bets:
        pick_id = make_pick_id(vb.game, vb.market, vb.selection)
        if pick_id in sent_cache:
            continue
        bucket = _market_bucket(vb.market)
        if bucket and any(
            p.game == vb.game and _market_bucket(p.market) == bucket
            for p in existing_picks
        ):
            continue

        try:
            send_alert(vb)
        except Exception as e:
            logger.error(f"send_alert: {e}")
            continue

        pick = Pick(
            pick_id=pick_id,
            game=vb.game,
            league=vb.league,
            market=vb.market,
            selection=vb.selection,
            kickoff=vb.kickoff,
            opening_odd=vb.odds_b365,
            fair_odd=vb.fair_odd,
            edge_pct=vb.edge_pct,
            level=vb.level,
            bet_href=vb.bet_href,
            event_id=vb.event_id,
            sbo_open=vb.odds_sbo,
        )
        save_pick(pick)
        existing_picks.append(pick)
        sent_cache.add(pick_id)
        new_alerts += 1

        key = vb.level.split()[-1]
        counts[key] = counts.get(key, 0) + 1

    save_cache(sent_cache)

    try:
        send_scan_summary(
            scan_label=scan_label,
            total_leagues=len({vb.league for vb in value_bets}),
            total_games=len(value_bets),
            elite=counts["Elite"],
            strong=counts["Strong"],
            value=counts["Value"],
        )
    except Exception as e:
        logger.error(f"send_scan_summary: {e}")

    logger.info(f"Scan: {new_alerts} novos alertas, {len(value_bets)} jogos analisados")


def run_test():
    try:
        vbs = fetch_value_bets()
    except Exception as e:
        logger.error(f"{e}")
        return
    logger.info(f"TEST: {len(vbs)} value bets")
    for vb in vbs[:15]:
        logger.info(f"  {vb.level} {vb.game} | {vb.market} {vb.selection} @ {vb.odds_b365} (SBO={vb.odds_sbo}) edge={vb.edge_pct}%")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test",   action="store_true")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--export", action="store_true")
    args = parser.parse_args()

    if args.test:      run_test()
    elif args.report:  send_weekly_report()
    elif args.export:  send_export()
    else:              run_normal()
