"""
tracker.py — Guarda picks e apura CLV real.

CLV real = (odd abertura Bet365 / odd fecho SingBet - 1) * 100
Ex: entrámos a 2.30 na Bet365 e SingBet fecha a 2.10 -> CLV = +9.5%
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)
PICKS_FILE = Path("picks_log.json")


@dataclass
class Pick:
    pick_id: str
    game: str
    league: str
    league_slug: str
    home_team: str
    away_team: str
    market: str
    selection: str
    kickoff: str
    opening_odd: float
    fair_odd: float
    edge_pct: float
    level: str
    bet_href: str
    event_id: int
    historical_event_id: Optional[int] = None
    singbet_open: Optional[float] = None       # SingBet abertura (se já disponível)
    closing_odd_singbet: Optional[float] = None  # SingBet fecho (apurado via histórico)
    clv_real: Optional[float] = None       # CLV real em %
    tracked_at: Optional[str] = None
    # ── Timing fields (super early tracking) ──
    first_seen_at: Optional[str] = None        # UTC ISO: quando o bot viu o evento pela 1a vez
    alerted_at: Optional[str] = None           # UTC ISO: quando o alerta foi enviado
    hours_to_kickoff: Optional[float] = None   # horas entre alerta e kickoff


def make_pick_id(game: str, market: str, selection: str) -> str:
    """
    ID único por pick.
    Totals: um pick por jogo.
    Side markets (ML/DNB/Spread): um pick por jogo.
    Isto evita que o bot envie lados opostos do mesmo encontro em scans diferentes.
    """
    if market == "Totals":
        raw = f"{game}|Totals"
    elif market in ("ML", "DNB", "Spread"):
        raw = f"{game}|SideMarkets"
    else:
        raw = f"{game}|{market}|{selection}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def load_picks() -> list[Pick]:
    if not PICKS_FILE.exists():
        return []
    try:
        raw = json.loads(PICKS_FILE.read_text())
        if not isinstance(raw, list):
            return []
        known = set(Pick.__dataclass_fields__.keys())
        picks = []
        for p in raw:
            if not isinstance(p, dict):
                continue
            filtered = {k: v for k, v in p.items() if k in known}
            if "singbet_open" not in filtered and "sbo_open" in p:
                filtered["singbet_open"] = p.get("sbo_open")
            if "closing_odd_singbet" not in filtered and "closing_odd_sbo" in p:
                filtered["closing_odd_singbet"] = p.get("closing_odd_sbo")
            if "pick_id" not in filtered or "game" not in filtered:
                continue
            game = filtered.get("game", "")
            home_team, away_team = "", ""
            if " vs " in game:
                home_team, away_team = game.split(" vs ", 1)
            filtered.setdefault("league_slug", "")
            filtered.setdefault("home_team", home_team)
            filtered.setdefault("away_team", away_team)
            filtered.setdefault("historical_event_id", None)
            filtered.setdefault("fair_odd", 0.0)
            filtered.setdefault("edge_pct", 0.0)
            filtered.setdefault("singbet_open", None)
            filtered.setdefault("first_seen_at", None)
            filtered.setdefault("alerted_at", None)
            filtered.setdefault("hours_to_kickoff", None)
            picks.append(Pick(**filtered))
        return picks
    except Exception as e:
        logger.error(f"load_picks: {e}")
        return []


def save_picks(picks: list[Pick]) -> None:
    PICKS_FILE.write_text(json.dumps([asdict(p) for p in picks], indent=2))


def save_pick(pick: Pick) -> None:
    picks = load_picks()
    if any(p.pick_id == pick.pick_id for p in picks):
        return
    picks.append(pick)
    save_picks(picks)


def track_pending_picks() -> None:
    """
    Apura CLV real para picks cujo kickoff já passou há 2h+.
    CLV = (opening_odd / closing_singbet - 1) * 100
    """
    from scraper import fetch_singbet_closing_odds

    picks = load_picks()
    changed = False

    for pick in picks:
        if pick.clv_real is not None:
            continue
        try:
            dt = datetime.strptime(pick.kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if datetime.now(timezone.utc) < dt + timedelta(hours=2):
            continue

        singbet, historical_event_id = fetch_singbet_closing_odds(
            event_id=pick.historical_event_id or pick.event_id,
            league_slug=pick.league_slug,
            kickoff=pick.kickoff,
            home_team=pick.home_team,
            away_team=pick.away_team,
        )
        if historical_event_id:
            pick.historical_event_id = historical_event_id
        if not singbet:
            continue

        closing = _find_singbet_closing(pick, singbet)
        if closing and closing > 1.0:
            clv = round((pick.opening_odd / closing - 1) * 100, 2)
            pick.closing_odd_singbet = closing
            pick.clv_real = clv
            pick.tracked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            changed = True
            logger.info(f"CLV tracked: {pick.game} {pick.selection} → {clv:+.1f}%")

    if changed:
        save_picks(picks)


def _find_singbet_closing(pick: Pick, singbet: dict) -> Optional[float]:
    def f(v):
        try:
            return float(v) if v else 0.0
        except Exception:
            return 0.0

    home_team = pick.home_team or pick.game.split(" vs ")[0]

    if pick.market in ("ML", "DNB"):
        ml = singbet.get("ML", {})
        home_in = home_team.lower() in pick.selection.lower()
        return f(ml.get("home" if home_in else "away")) or None

    elif pick.market == "Spread":
        sp = singbet.get("Spread", {})
        home_in = home_team.lower() in pick.selection.lower()
        return f(sp.get("home" if home_in else "away")) or None

    elif pick.market == "Totals":
        tot = singbet.get("Totals", {})
        if "Over" in pick.selection:
            return f(tot.get("over") or tot.get("home")) or None
        return f(tot.get("under") or tot.get("away")) or None

    return None


def get_picks_for_report(days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    picks = load_picks()

    recent = []
    for pick in picks:
        try:
            kickoff_dt = datetime.strptime(pick.kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if kickoff_dt >= since:
            recent.append(pick)

    tracked = [pick for pick in recent if pick.clv_real is not None]
    pending = [pick for pick in recent if pick.clv_real is None]
    total_tracked = len(tracked)
    beat_line_count = sum(1 for pick in tracked if pick.clv_real > 0)
    clv_medio = round(sum(pick.clv_real for pick in tracked) / total_tracked, 2) if tracked else 0.0
    beat_line_pct = round(beat_line_count / total_tracked * 100, 1) if tracked else 0.0

    return {
        "tracked": tracked,
        "pending": pending,
        "total_picks": len(recent),
        "total_tracked": total_tracked,
        "clv_medio": clv_medio,
        "beat_line_count": beat_line_count,
        "beat_line_pct": beat_line_pct,
    }


MIN_HOURS_TO_KICKOFF = 48.0  # picks com menos de 48h são descartados

def timing_band(hours: float | None) -> str:
    """Classifica horas até ao kickoff em bandas de antecedência."""
    if hours is None:
        return "unknown"
    if hours >= 336:   # 14+ dias
        return "14d+"
    if hours >= 168:   # 7-14 dias
        return "7-14d"
    if hours >= 72:    # 3-7 dias
        return "3-7d"
    if hours >= 48:    # 2-3 dias
        return "48-72h"
    return "<48h"      # não devia existir com o filtro ativo


def get_learning_snapshot(min_samples: int = 5) -> dict:
    tracked = [pick for pick in load_picks() if pick.clv_real is not None]
    overall_count = len(tracked)
    overall_avg_clv = round(sum(pick.clv_real for pick in tracked) / overall_count, 2) if tracked else 0.0
    overall_beat_pct = round(sum(1 for pick in tracked if pick.clv_real > 0) / overall_count * 100, 1) if tracked else 0.0

    grouped: dict[str, list[Pick]] = defaultdict(list)
    for pick in tracked:
        grouped[pick.market].append(pick)

    by_market = {}
    for market, picks in grouped.items():
        count = len(picks)
        avg_clv = round(sum(pick.clv_real for pick in picks) / count, 2)
        beat_pct = round(sum(1 for pick in picks if pick.clv_real > 0) / count * 100, 1)
        avg_edge = round(sum(pick.edge_pct for pick in picks) / count, 2)

        if count < min_samples:
            recommendation = "Amostra curta; manter observacao antes de mexer no threshold."
        elif avg_clv >= 5 and beat_pct >= 55:
            recommendation = "Mercado forte; manter prioridade e threshold atual."
        elif avg_clv >= 2:
            recommendation = "Mercado saudavel; continuar a recolher amostra."
        elif avg_clv >= 0:
            recommendation = "Mercado neutro; exige selecao mais cuidadosa."
        else:
            recommendation = "Mercado fraco; convem apertar o minimo ou reduzir exposicao."

        by_market[market] = {
            "tracked": count,
            "avg_clv": avg_clv,
            "beat_line_pct": beat_pct,
            "avg_edge": avg_edge,
            "recommendation": recommendation,
        }

    # ── Análise por timing band ────────────────────────────────────────────
    by_timing: dict[str, list[Pick]] = defaultdict(list)
    for pick in tracked:
        band = timing_band(pick.hours_to_kickoff)
        by_timing[band].append(pick)

    timing_stats = {}
    band_order = ["14d+", "7-14d", "3-7d", "48-72h", "<48h", "unknown"]
    for band in band_order:
        picks_in_band = by_timing.get(band, [])
        count = len(picks_in_band)
        if count == 0:
            continue
        avg_clv = round(sum(p.clv_real for p in picks_in_band) / count, 2)
        beat_pct = round(sum(1 for p in picks_in_band if p.clv_real > 0) / count * 100, 1)
        timing_stats[band] = {
            "tracked": count,
            "avg_clv": avg_clv,
            "beat_line_pct": beat_pct,
        }

    return {
        "tracked_total": overall_count,
        "overall_avg_clv": overall_avg_clv,
        "overall_beat_line_pct": overall_beat_pct,
        "by_market": by_market,
        "by_timing": timing_stats,
    }
