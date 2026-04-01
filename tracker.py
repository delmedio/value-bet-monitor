"""
tracker.py — Guarda picks e apura CLV real.

CLV real = odd abertura Bet365 vs odd fecho SBObet.
Se entrámos a 2.30 na Bet365 e a SBO fecha a 2.10 → CLV = +9.5%.
"""

import json, hashlib, logging
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
    market: str
    selection: str
    kickoff: str
    opening_odd: float         # Bet365 abertura (nossa entrada)
    fair_odd: float            # estimado pelo modelo na altura da entrada
    edge_pct: float            # edge estimado na altura da entrada
    level: str
    bet_href: str
    event_id: int
    # SBObet abertura — se já disponível quando enviámos o alerta
    sbo_open: Optional[float] = None
    # SBObet fecho — preenchido após o jogo
    closing_odd_sbo: Optional[float] = None
    # CLV real = (opening_b365 / closing_sbo - 1) * 100
    clv_real: Optional[float] = None
    tracked_at: Optional[str] = None


def make_pick_id(game: str, market: str, selection: str) -> str:
    # Para Totals: ignora Over/Under e linha — um pick por jogo por mercado
    # Para Spread: ignora a linha — um pick por jogo por lado
    # Para ML/DNB: usa selecção completa
    if market == "Totals":
        raw = f"{game}|Totals"
    elif market == "Spread":
        # Extrai só o nome da equipa sem a linha
        team = selection.rsplit(" ", 1)[0] if " " in selection else selection
        raw = f"{game}|Spread|{team}"
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
            # Campos obrigatórios mínimos
            if "pick_id" not in filtered or "game" not in filtered:
                continue
            # Defaults para campos novos que podem não existir
            filtered.setdefault("fair_odd", 0.0)
            filtered.setdefault("edge_pct", 0.0)
            filtered.setdefault("sbo_open", None)
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
    Para cada pick pendente, tenta apurar CLV real.
    Condição: kickoff já passou há pelo menos 2h.
    CLV = (odd_abertura_b365 / odd_fecho_sbo - 1) * 100
    """
    from scraper import fetch_sbo_closing_odds

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

        sbo = fetch_sbo_closing_odds(pick.event_id)
        if not sbo:
            continue

        closing = _find_sbo_closing(pick, sbo)
        if closing and closing > 1.0:
            clv = round((pick.opening_odd / closing - 1) * 100, 2)
            pick.closing_odd_sbo = closing
            pick.clv_real = clv
            pick.tracked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            changed = True
            logger.info(f"CLV tracked: {pick.game} {pick.market} {pick.selection} → CLV={clv:+.1f}%")

    if changed:
        save_picks(picks)


def _find_sbo_closing(pick: Pick, sbo: dict) -> Optional[float]:
    """Encontra a odd SBObet correspondente ao pick."""
    def f(v):
        try:
            return float(v) if v else 0.0
        except Exception:
            return 0.0

    home_team = pick.game.split(" vs ")[0]

    if pick.market == "ML":
        ml = sbo.get("ML", {})
        home_in = home_team.lower() in pick.selection.lower()
        return f(ml.get("home" if home_in else "away")) or None

    elif pick.market == "Spread":
        sp = sbo.get("Spread", {})
        home_in = home_team.lower() in pick.selection.lower()
        return f(sp.get("home" if home_in else "away")) or None

    elif pick.market == "Totals":
        tot = sbo.get("Totals", {})
        if "Over" in pick.selection:
            return f(tot.get("over") or tot.get("home")) or None
        return f(tot.get("under") or tot.get("away")) or None

    return None
