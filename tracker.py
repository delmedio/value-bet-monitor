"""
tracker.py — Registo de picks e tracking de CLV real via Pinnacle closing odds
"""

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)
PICKS_FILE = Path("picks_log.json")


@dataclass
class Pick:
    id: str
    event_id: str
    sport_key: str
    game: str
    league: str
    kickoff: str
    kickoff_ts: float
    market: str
    selection: str
    bookmaker: str
    opening_odd: float
    fair_odd: float
    min_odd: float
    edge_pct: float
    level: str
    alerted_at: str
    # Preenchido após o jogo
    pin_closing_odd: float | None = None
    clv_real: float | None = None
    tracked_at: str | None = None


def make_pick_id(game: str, market: str, selection: str, odd: float) -> str:
    raw = f"{game}|{market}|{selection}|{odd:.3f}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def load_picks() -> list[Pick]:
    if not PICKS_FILE.exists():
        return []
    try:
        data = json.loads(PICKS_FILE.read_text())
        return [Pick(**p) for p in data.get("picks", [])]
    except Exception as e:
        log.error(f"Erro ao carregar picks: {e}")
        return []


def save_picks(picks: list[Pick]) -> None:
    PICKS_FILE.write_text(json.dumps(
        {"picks": [asdict(p) for p in picks]},
        indent=2, ensure_ascii=False
    ))


def save_pick(pick: Pick) -> None:
    picks = load_picks()
    if not any(p.id == pick.id for p in picks):
        picks.append(pick)
        save_picks(picks)


def is_ready_to_track(kickoff_ts: float) -> bool:
    """Jogo terminou há pelo menos 3 horas."""
    return datetime.now(timezone.utc).timestamp() > kickoff_ts + (3 * 3600)


def track_pending_picks() -> list[Pick]:
    """Busca closing odds da Pinnacle para picks pendentes."""
    from scraper import fetch_closing_odds

    picks = load_picks()
    newly_tracked = []

    for pick in picks:
        if pick.clv_real is not None:
            continue
        if not is_ready_to_track(pick.kickoff_ts):
            continue
        if not pick.event_id or not pick.sport_key:
            continue

        log.info(f"Tracking: {pick.game} | {pick.selection}")
        closing = fetch_closing_odds(pick.event_id, pick.sport_key)

        if not closing:
            continue

        # Determina qual odd de fecho usar baseado no mercado/selecção
        pin_odd = None
        sel = pick.selection.lower()
        mkt = pick.market.lower()

        if "match odds" in mkt or "1x2" in mkt:
            if pick.game.split(" vs ")[0].lower() in sel:
                pin_odd = closing.get(f"pin_close_{pick.game.split(' vs ')[0].lower().replace(' ', '_')}")
            elif "away" in sel or pick.game.split(" vs ")[-1].lower() in sel:
                pin_odd = closing.get(f"pin_close_{pick.game.split(' vs ')[-1].lower().replace(' ', '_')}")
        elif "over" in sel:
            pin_odd = closing.get("pin_close_over")
        elif "under" in sel:
            pin_odd = closing.get("pin_close_under")
        elif "asian handicap" in mkt or "ah" in mkt:
            if pick.game.split(" vs ")[0].lower() in sel:
                pin_odd = closing.get(f"pin_close_ah_{pick.game.split(' vs ')[0].lower().replace(' ', '_')}")
            else:
                pin_odd = closing.get(f"pin_close_ah_{pick.game.split(' vs ')[-1].lower().replace(' ', '_')}")

        if pin_odd and pin_odd > 1.0:
            pick.pin_closing_odd = pin_odd
            pick.clv_real = round((pick.opening_odd / pin_odd - 1) * 100, 2)
            pick.tracked_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
            newly_tracked.append(pick)
            log.info(f"CLV real: {pick.clv_real:+.1f}% ({pick.opening_odd:.3f} → {pin_odd:.3f})")

    if newly_tracked:
        save_picks(picks)

    return newly_tracked


def get_picks_for_report(days: int = 7) -> dict:
    picks = load_picks()
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    recent = [p for p in picks if p.kickoff_ts >= cutoff_ts]
    tracked = [p for p in recent if p.clv_real is not None]
    pending = [p for p in recent if p.clv_real is None]
    clv_values = [p.clv_real for p in tracked]
    beat = [c for c in clv_values if c > 0]
    return {
        "total_picks": len(recent),
        "tracked": tracked,
        "pending": pending,
        "clv_medio": round(sum(clv_values) / len(clv_values), 2) if clv_values else 0,
        "beat_line_pct": round(len(beat) / len(clv_values) * 100, 1) if clv_values else 0,
        "beat_line_count": len(beat),
        "total_tracked": len(tracked),
    }
