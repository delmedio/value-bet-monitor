"""
alert.py — Envio de alertas via Telegram
"""

import os
import requests
from datetime import datetime, timezone


TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_message(text: str) -> bool:
    """Envia mensagem para o Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("ERRO: TELEGRAM_TOKEN ou TELEGRAM_CHAT_ID não definidos")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"Erro ao enviar Telegram: {e}")
        return False


def format_value_bet_alert(
    game: str,
    league: str,
    kickoff: str,
    market: str,
    selection: str,
    bookmaker: str,
    opening_odd: float,
    fair_odd: float,
    min_odd: float,
    edge_pct: float,
    level: str,
) -> str:
    """Formata o alerta de value bet."""
    return (
        f"{level}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏟 <b>{game}</b>\n"
        f"🏆 {league}\n"
        f"⏰ {kickoff}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{market}</b> — {selection}\n"
        f"💰 {bookmaker}: <b>{opening_odd:.3f}</b>\n"
        f"⚖️ Fair: ~{fair_odd:.2f} | Mín: <b>{min_odd:.2f}</b>\n"
        f"📈 Edge: <b>+{edge_pct:.1f}%</b> CLV esperado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Aposta antes que o mercado corrija</i>"
    )


def format_scan_summary(
    total_games: int,
    value_bets: int,
    elite: int,
    strong: int,
    normal: int,
    leagues_scanned: int,
) -> str:
    """Formata o resumo do scan."""
    now = datetime.now(timezone.utc).strftime("%d/%m %H:%M UTC")
    if value_bets == 0:
        return (
            f"🔍 <b>Scan {now}</b>\n"
            f"Ligas: {leagues_scanned} | Jogos: {total_games}\n"
            f"Sem value bets neste scan"
        )
    return (
        f"📊 <b>Scan {now}</b>\n"
        f"Ligas: {leagues_scanned} | Jogos: {total_games}\n"
        f"🔥 Elite: {elite} | ✅ Strong: {strong} | 📊 Value: {normal}"
    )


def send_test_message() -> bool:
    """Envia mensagem de teste para verificar ligação."""
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    text = (
        f"✅ <b>Value Bet Monitor — Online</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Hora: {now}\n"
        f"Modelo: Special One + Andrey2505\n"
        f"Picks calibração: 703\n"
        f"CLV médio esperado: +10.1%\n"
        f"Threshold: Edge ≥ 8%\n"
        f"Ligas monitoradas: 38\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Sistema a funcionar correctamente</i>"
    )
    return send_message(text)


if __name__ == "__main__":
    # Teste directo
    if send_test_message():
        print("✅ Mensagem de teste enviada com sucesso")
    else:
        print("❌ Erro ao enviar mensagem de teste")
