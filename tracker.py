"""
tracker.py — Registo de picks e tracking de CLV real via Pinnacle closing odds
Suporta report semanal + histórico acumulado por semana
"""

import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict
from collections import defaultdict

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
    return datetime.now(timezone.utc).timestamp() > kickoff_ts + (3 * 3600)


def track_pending_picks() -> list[Pick]:
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

        pin_odd = None
        sel = pick.selection.lower()
        mkt = pick.market.lower()

        if "match odds" in mkt:
            home = pick.game.split(" vs ")[0].lower().replace(" ", "_")
            away = pick.game.split(" vs ")[-1].lower().replace(" ", "_")
            if home[:6] in sel:
                pin_odd = closing.get(f"pin_close_h2h_{home}")
            elif away[:6] in sel:
                pin_odd = closing.get(f"pin_close_h2h_{away}")
        elif "over" in sel:
            pin_odd = closing.get("pin_close_totals_over")
        elif "under" in sel:
            pin_odd = closing.get("pin_close_totals_under")
        elif "asian handicap" in mkt:
            home = pick.game.split(" vs ")[0].lower().replace(" ", "_")
            away = pick.game.split(" vs ")[-1].lower().replace(" ", "_")
            if home[:6] in sel:
                pin_odd = closing.get(f"pin_close_spreads_{home}")
            else:
                pin_odd = closing.get(f"pin_close_spreads_{away}")

        if pin_odd and pin_odd > 1.0:
            pick.pin_closing_odd = pin_odd
            pick.clv_real = round((pick.opening_odd / pin_odd - 1) * 100, 2)
            pick.tracked_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
            newly_tracked.append(pick)
            log.info(f"CLV real: {pick.clv_real:+.1f}%")

    if newly_tracked:
        save_picks(picks)
    return newly_tracked


def get_week_label(ts: float) -> str:
    """Retorna label da semana no formato 'Semana DD/MM'."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    # Início da semana (segunda-feira)
    monday = dt - timedelta(days=dt.weekday())
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%d/%m')}–{sunday.strftime('%d/%m/%Y')}"


def get_picks_for_report(days: int = 7) -> dict:
    """Picks da semana actual."""
    picks = load_picks()
    cutoff_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    recent = [p for p in picks if p.kickoff_ts >= cutoff_ts]
    tracked = [p for p in recent if p.clv_real is not None]
    pending = [p for p in recent if p.clv_real is None]
    clv_values = [p.clv_real for p in tracked]
    beat = [c for c in clv_values if c > 0]
    return {
        "total_picks":    len(recent),
        "tracked":        tracked,
        "pending":        pending,
        "clv_medio":      round(sum(clv_values) / len(clv_values), 2) if clv_values else 0,
        "beat_line_pct":  round(len(beat) / len(clv_values) * 100, 1) if clv_values else 0,
        "beat_line_count": len(beat),
        "total_tracked":  len(tracked),
    }


def get_all_weekly_stats() -> list[dict]:
    """
    Retorna estatísticas agrupadas por semana — para o histórico acumulado.
    Ordenado da semana mais recente para a mais antiga.
    """
    picks = load_picks()
    tracked = [p for p in picks if p.clv_real is not None]

    if not tracked:
        return []

    # Agrupa por semana
    by_week: dict[str, list] = defaultdict(list)
    for pick in tracked:
        label = get_week_label(pick.kickoff_ts)
        by_week[label].append(pick)

    # Calcula stats por semana
    weekly_stats = []
    for label, week_picks in by_week.items():
        clv_vals = [p.clv_real for p in week_picks]
        beat = sum(1 for c in clv_vals if c > 0)
        clv_med = sum(clv_vals) / len(clv_vals)

        # Stats por liga dentro desta semana
        by_league: dict[str, list] = defaultdict(list)
        for p in week_picks:
            by_league[p.league].append(p)

        weekly_stats.append({
            "label":      label,
            "picks":      week_picks,
            "n":          len(week_picks),
            "clv_medio":  round(clv_med, 2),
            "beat_count": beat,
            "beat_pct":   round(beat / len(clv_vals) * 100, 1),
            "by_league":  dict(by_league),
            # Ordena pelo kickoff mais recente para ordenar semanas
            "sort_ts":    max(p.kickoff_ts for p in week_picks),
        })

    # Mais recente primeiro
    weekly_stats.sort(key=lambda x: x["sort_ts"], reverse=True)
    return weekly_stats


def get_cumulative_stats() -> dict:
    """Estatísticas cumulativas de todos os picks tracked."""
    picks = load_picks()
    tracked = [p for p in picks if p.clv_real is not None]
    if not tracked:
        return {"n": 0, "clv_medio": 0, "beat_pct": 0, "beat_count": 0}
    clv_vals = [p.clv_real for p in tracked]
    beat = sum(1 for c in clv_vals if c > 0)
    return {
        "n":          len(tracked),
        "clv_medio":  round(sum(clv_vals) / len(clv_vals), 2),
        "beat_count": beat,
        "beat_pct":   round(beat / len(clv_vals) * 100, 1),
    }
    
