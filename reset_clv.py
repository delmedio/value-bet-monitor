"""
reset_clv.py — Limpa CLV e fecho de todos os picks para re-tracking.

Mantém intactos: game, market, selection, opening_odd, fair_odd, edge_pct,
level, kickoff, event_id, league, etc.

Limpa: closing_odd_singbet, clv_real, tracked_at.

Depois de correr este script, o próximo scan normal com track_pending_picks()
vai recalcular os CLVs com a lógica corrigida.

Uso:
  python reset_clv.py                # reset de todos os picks
  python reset_clv.py --dry-run      # mostra o que faria sem alterar
"""

import json
import sys
from pathlib import Path

PICKS_FILE = Path("picks_log.json")


def reset():
    dry_run = "--dry-run" in sys.argv

    if not PICKS_FILE.exists():
        print("picks_log.json nao encontrado.")
        return

    raw = json.loads(PICKS_FILE.read_text())
    if not isinstance(raw, list):
        print("Formato inesperado.")
        return

    reset_count = 0
    for pick in raw:
        if not isinstance(pick, dict):
            continue
        had_clv = pick.get("clv_real") is not None
        if had_clv:
            reset_count += 1
            game = pick.get("game", "?")
            market = pick.get("market", "?")
            selection = pick.get("selection", "?")
            old_closing = pick.get("closing_odd_singbet", "?")
            old_clv = pick.get("clv_real", "?")
            print(f"  RESET: {game} | {market} {selection} | fecho={old_closing} clv={old_clv}")

            if not dry_run:
                pick["closing_odd_singbet"] = None
                pick["clv_real"] = None
                pick["tracked_at"] = None

    if dry_run:
        print(f"\n[DRY RUN] {reset_count} picks seriam resetados. Nada foi alterado.")
    else:
        PICKS_FILE.write_text(json.dumps(raw, indent=2))
        print(f"\n{reset_count} picks resetados. Proximo scan normal vai re-trackear.")


if __name__ == "__main__":
    reset()
