"""
alert.py — Alertas Telegram e report email
"""

import os
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

log = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("Telegram não configurado")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Erro Telegram: {e}")
        return False


def format_alert(game: str, league: str, kickoff: str, market: str,
                 selection: str, opening_odd: float, fair_odd: float,
                 min_odd: float, edge_pct: float, level: str) -> str:
    return (
        f"{level}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏟 <b>{game}</b>\n"
        f"🏆 {league}\n"
        f"⏰ {kickoff}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{market}</b> — {selection}\n"
        f"💰 Bet365: <b>{opening_odd:.3f}</b>\n"
        f"⚖️ Fair: ~{fair_odd:.2f} | Mín: <b>{min_odd:.2f}</b>\n"
        f"📈 Edge: <b>+{edge_pct:.1f}%</b> CLV esperado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Aposta antes que o mercado corrija</i>"
    )


def format_scan_summary(total_games: int, value_bets: int,
                        elite: int, strong: int, normal: int,
                        leagues_scanned: int) -> str:
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
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    return send_telegram(
        f"✅ <b>Value Bet Monitor — Online</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Hora: {now}\n"
        f"Modelo: Special One + Andrey2505 (703 picks)\n"
        f"CLV médio esperado: +10.1%\n"
        f"Threshold: Edge ≥ 10%\n"
        f"Ligas: 37 | Filtro: jogos ≥ 15 Abr 2026\n"
        f"Fonte: The Odds API (100K/mês)\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Sistema a funcionar correctamente</i>"
    )


# ── Report email ──────────────────────────────────────────────────────────────

def _clv_emoji(clv: float) -> str:
    if clv >= 15: return "🔥"
    if clv >= 8:  return "✅"
    if clv >= 0:  return "📊"
    return "❌"


def _pick_row(pick) -> str:
    if pick.clv_real is not None:
        clv_str = f"{pick.clv_real:+.1f}%"
        close_str = f"{pick.pin_closing_odd:.3f}"
        color = "#27ae60" if pick.clv_real >= 0 else "#e74c3c"
        emoji = _clv_emoji(pick.clv_real)
    else:
        clv_str, close_str, color, emoji = "Pendente", "—", "#888", "⏳"

    return f"""
<tr style="border-bottom:1px solid #eee">
  <td style="padding:8px 10px;font-size:13px">
    {emoji} <b>{pick.game}</b><br>
    <span style="color:#666;font-size:11px">{pick.league} · {pick.kickoff}</span>
  </td>
  <td style="padding:8px 10px;font-size:12px">{pick.market}<br>{pick.selection}</td>
  <td style="padding:8px 10px;font-size:13px;text-align:center"><b>{pick.opening_odd:.3f}</b></td>
  <td style="padding:8px 10px;font-size:13px;text-align:center;color:#666">{close_str}</td>
  <td style="padding:8px 10px;font-size:13px;text-align:center;font-weight:bold;color:{color}">{clv_str}</td>
</tr>"""


def send_weekly_report(days: int = 7) -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.error("Gmail não configurado")
        return False

    from tracker import get_picks_for_report
    data = get_picks_for_report(days=days)

    now = datetime.now(timezone.utc)
    period = f"{(now - timedelta(days=days)).strftime('%d/%m')} a {now.strftime('%d/%m/%Y')}"
    subject = f"📊 Value Bet Report — {period} ({data['total_picks']} picks)"

    clv_color = "#27ae60" if data["clv_medio"] >= 0 else "#e74c3c"
    clv_display = f"{data['clv_medio']:+.1f}%" if data["total_tracked"] > 0 else "—"
    beat_display = f"{data['beat_line_pct']:.0f}%" if data["total_tracked"] > 0 else "—"

    all_picks = sorted(data["tracked"] + data["pending"], key=lambda p: p.kickoff_ts, reverse=True)
    rows = "".join(_pick_row(p) for p in all_picks)
    pending_note = "<p style='color:#888;font-size:12px;margin-top:12px'>⏳ Picks pendentes: aguardam fecho.</p>" if data["pending"] else ""

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:Arial,sans-serif;max-width:700px;margin:0 auto;color:#222}}
.hdr{{background:#1a1a2e;color:white;padding:20px 24px;border-radius:8px 8px 0 0}}
.hdr h1{{margin:0;font-size:20px}}.hdr p{{margin:4px 0 0;color:#aaa;font-size:13px}}
.kpis{{display:flex;gap:12px;padding:16px 0}}
.kpi{{flex:1;background:#f8f9fa;border-radius:8px;padding:14px;text-align:center}}
.kv{{font-size:22px;font-weight:bold}}.kl{{font-size:11px;color:#666;margin-top:4px;text-transform:uppercase}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th{{background:#f0f0f0;padding:9px 10px;text-align:left;font-size:12px;color:#555}}
.ftr{{margin-top:20px;font-size:11px;color:#999;text-align:center;padding:12px}}</style></head>
<body>
<div class="hdr"><h1>🎯 Value Bet Monitor — Report</h1><p>{period}</p></div>
<div style="padding:16px 0">
<div class="kpis">
  <div class="kpi"><div class="kv">{data['total_picks']}</div><div class="kl">Total picks</div></div>
  <div class="kpi"><div class="kv" style="color:{clv_color}">{clv_display}</div><div class="kl">CLV médio real</div></div>
  <div class="kpi"><div class="kv">{beat_display}</div><div class="kl">Beat the line</div></div>
  <div class="kpi"><div class="kv">{data['beat_line_count']}/{data['total_tracked']}</div><div class="kl">Tracked</div></div>
</div>
<table><thead><tr>
  <th>Jogo</th><th>Mercado</th>
  <th style="text-align:center">Abertura</th>
  <th style="text-align:center">Fecho (PIN)</th>
  <th style="text-align:center">CLV real</th>
</tr></thead><tbody>{rows}</tbody></table>
{pending_note}
</div>
<div class="ftr">Modelo calibrado com 703 picks · Special One + Andrey2505<br>
CLV esperado: +10.1% · Beat the line histórico: 76%</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
        log.info(f"Report enviado para {GMAIL_USER}")
        return True
    except Exception as e:
        log.error(f"Erro email: {e}")
        return False
