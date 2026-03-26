"""
tracker.py — Registo de picks enviados e tracking de CLV real
Guarda cada value bet alertada e depois busca odd de fecho da BetInAsia no OddsPortal
"""

import json
import logging
import time
import random
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)

PICKS_FILE = Path("picks_log.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.oddsportal.com/",
}


@dataclass
class Pick:
    id: str
    game: str
    league: str
    kickoff: str          # "DD/MM/YYYY HH:MM"
    kickoff_ts: float     # timestamp UTC
    market: str
    selection: str
    bookmaker: str
    opening_odd: float
    fair_odd: float
    min_odd: float
    edge_pct: float
    level: str
    alerted_at: str       # quando foi enviado o alerta
    oddsportal_url: str
    # Preenchido após o jogo
    closing_odd_betinasia: float | None = None
    clv_real: float | None = None
    tracked_at: str | None = None
    result: str | None = None  # "win" | "loss" | "push" | None


def load_picks() -> list[Pick]:
    """Carrega todos os picks registados."""
    if not PICKS_FILE.exists():
        return []
    try:
        data = json.loads(PICKS_FILE.read_text())
        return [Pick(**p) for p in data.get("picks", [])]
    except Exception as e:
        log.error(f"Erro ao carregar picks: {e}")
        return []


def save_picks(picks: list[Pick]) -> None:
    """Guarda todos os picks."""
    data = {"picks": [asdict(p) for p in picks]}
    PICKS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def save_pick(pick: Pick) -> None:
    """Adiciona um pick novo ao registo."""
    picks = load_picks()
    # Evita duplicados
    if not any(p.id == pick.id for p in picks):
        picks.append(pick)
        save_picks(picks)
        log.info(f"Pick guardado: {pick.game} | {pick.market} | {pick.selection}")


def make_pick_id(game: str, market: str, selection: str, odd: float) -> str:
    """Cria ID único para um pick."""
    import hashlib
    raw = f"{game}|{market}|{selection}|{odd:.3f}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]


def is_game_finished(kickoff_ts: float) -> bool:
    """Verifica se o jogo já terminou (kickoff + 2.5 horas)."""
    now = datetime.now(timezone.utc).timestamp()
    return now > kickoff_ts + (2.5 * 3600)


def is_ready_to_track(kickoff_ts: float) -> bool:
    """
    Verifica se é altura de ir buscar o fecho.
    Vai buscar 3 horas após o kickoff (jogo terminado + odds estabilizadas).
    """
    now = datetime.now(timezone.utc).timestamp()
    return now > kickoff_ts + (3 * 3600)


def fetch_closing_odd_betinasia(oddsportal_url: str, market: str, selection: str) -> float | None:
    """
    Vai ao OddsPortal buscar a odd de fecho da BetInAsia para um mercado/selecção específicos.
    BetInAsia é usada como referência por ser clone da Pinnacle.
    """
    try:
        time.sleep(random.uniform(2, 4))
        session = requests.Session()
        r = session.get(oddsportal_url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            log.warning(f"Status {r.status_code} ao buscar fecho: {oddsportal_url}")
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        # Procura tabela de odds com BetInAsia
        # OddsPortal lista bookmakers em rows — BetInAsia tem class específica ou texto
        bookie_rows = soup.select("tr[class*='aver'], div[class*='bookie']")

        for row in bookie_rows:
            text = row.get_text(strip=True).lower()
            if "betinasia" in text or "bet-in-asia" in text:
                # Extrai as odds desta row
                odd_els = row.select("td span, td a")
                odds = []
                for el in odd_els:
                    try:
                        val = float(el.get_text(strip=True).replace(",", "."))
                        if 1.01 < val < 50:
                            odds.append(val)
                    except ValueError:
                        continue

                if odds:
                    # Para mercados de 2 outcomes (AH, O/U): primeira odd = lado 1, segunda = lado 2
                    # Para 1X2: 3 odds
                    log.info(f"BetInAsia odds encontradas: {odds}")

                    # Determina qual odd corresponde à selecção
                    if "over" in selection.lower() or "home" in selection.lower():
                        return odds[0] if odds else None
                    elif "under" in selection.lower() or "away" in selection.lower():
                        return odds[-1] if odds else None
                    else:
                        return odds[0] if odds else None

        log.warning(f"BetInAsia não encontrada em: {oddsportal_url}")
        return None

    except Exception as e:
        log.error(f"Erro ao buscar fecho BetInAsia: {e}")
        return None


def calculate_clv_real(opening_odd: float, closing_odd: float) -> float:
    """CLV real = (odd_abertura / odd_fecho - 1) × 100"""
    if closing_odd <= 0:
        return 0.0
    return round((opening_odd / closing_odd - 1) * 100, 2)


def track_pending_picks() -> list[Pick]:
    """
    Processa todos os picks pendentes de tracking.
    Para picks cujo jogo já terminou, vai buscar a odd de fecho.
    Retorna lista de picks recém-tracked.
    """
    picks = load_picks()
    newly_tracked = []

    for pick in picks:
        # Já foi tracked
        if pick.closing_odd_betinasia is not None:
            continue

        # Jogo ainda não terminou
        if not is_ready_to_track(pick.kickoff_ts):
            continue

        log.info(f"A fazer tracking: {pick.game} | {pick.market} | {pick.selection}")

        closing_odd = fetch_closing_odd_betinasia(
            pick.oddsportal_url,
            pick.market,
            pick.selection,
        )

        if closing_odd:
            pick.closing_odd_betinasia = closing_odd
            pick.clv_real = calculate_clv_real(pick.opening_odd, closing_odd)
            pick.tracked_at = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M")
            newly_tracked.append(pick)
            log.info(f"CLV real: {pick.clv_real:.1f}% ({pick.opening_odd:.3f} → {closing_odd:.3f})")
        else:
            log.warning(f"Não foi possível obter odd de fecho para: {pick.game}")

        time.sleep(random.uniform(3, 5))

    if newly_tracked:
        save_picks(picks)

    return newly_tracked


def get_picks_for_report(days: int = 7) -> dict:
    """
    Retorna picks das últimas N dias organizados para o report.
    """
    picks = load_picks()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()

    recent = [p for p in picks if p.kickoff_ts >= cutoff_ts]
    tracked = [p for p in recent if p.clv_real is not None]
    pending = [p for p in recent if p.clv_real is None]

    clv_values = [p.clv_real for p in tracked if p.clv_real is not None]
    beat_line = [c for c in clv_values if c > 0]

    return {
        "total_picks": len(recent),
        "tracked": tracked,
        "pending": pending,
        "clv_medio": round(sum(clv_values) / len(clv_values), 2) if clv_values else 0,
        "beat_line_pct": round(len(beat_line) / len(clv_values) * 100, 1) if clv_values else 0,
        "beat_line_count": len(beat_line),
        "total_tracked": len(tracked),
    }
