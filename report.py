"""
report.py — Geração e envio de report semanal por email
Inclui todos os picks da semana com CLV real (odd abertura vs fecho BetInAsia)
"""

import os
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta

from tracker import get_picks_for_report, Pick

log = logging.getLogger(__name__)

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def clv_emoji(clv: float) -> str:
    if clv >= 15:
        return "🔥"
    elif clv >= 8:
        return "✅"
    elif clv >= 0:
        return "📊"
    else:
        return "❌"


def format_pick_row_html(pick: Pick) -> str:
    """Formata uma linha de pick para o report HTML."""
    if pick.clv_real is not None:
        clv_str = f"{pick.clv_real:+.1f}%"
        closing_str = f"{pick.closing_odd_betinasia:.3f}"
        emoji = clv_emoji(pick.clv_real)
        clv_color = "#27ae60" if pick.clv_real >= 0 else "#e74c3c"
    else:
        clv_str = "Pendente"
        closing_str = "—"
        emoji = "⏳"
        clv_color = "#888"

    return f"""
        <tr style="border-bottom: 1px solid #eee;">
            <td style="padding: 8px 10px; font-size: 13px;">{emoji} <b>{pick.game}</b><br>
                <span style="color: #666; font-size: 11px;">{pick.league} · {pick.kickoff}</span>
            </td>
            <td style="padding: 8px 10px; font-size: 12px;">{pick.market}<br>
                <span style="color: #333;">{pick.selection}</span>
            </td>
            <td style="padding: 8px 10px; font-size: 13px; text-align: center;">
                <b>{pick.opening_odd:.3f}</b>
            </td>
            <td style="padding: 8px 10px; font-size: 13px; text-align: center; color: #666;">
                {closing_str}
            </td>
            <td style="padding: 8px 10px; font-size: 13px; text-align: center; font-weight: bold; color: {clv_color};">
                {clv_str}
            </td>
        </tr>
    """


def generate_report_html(data: dict, period_label: str) -> str:
    """Gera o HTML completo do report."""
    tracked = data["tracked"]
    pending = data["pending"]
    clv_medio = data["clv_medio"]
    beat_pct = data["beat_line_pct"]
    beat_count = data["beat_line_count"]
    total_tracked = data["total_tracked"]

    # Ordena por kickoff
    all_picks = sorted(tracked + pending, key=lambda p: p.kickoff_ts, reverse=True)

    picks_rows = "".join(format_pick_row_html(p) for p in all_picks)

    clv_color = "#27ae60" if clv_medio >= 0 else "#e74c3c"
    clv_display = f"{clv_medio:+.1f}%" if total_tracked > 0 else "—"
    beat_display = f"{beat_pct:.0f}%" if total_tracked > 0 else "—"

    return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; color: #222; }}
        .header {{ background: #1a1a2e; color: white; padding: 20px 24px; border-radius: 8px 8px 0 0; }}
        .header h1 {{ margin: 0; font-size: 20px; }}
        .header p {{ margin: 4px 0 0; color: #aaa; font-size: 13px; }}
        .kpis {{ display: flex; gap: 12px; padding: 16px 0; }}
        .kpi {{ flex: 1; background: #f8f9fa; border-radius: 8px; padding: 14px; text-align: center; }}
        .kpi-value {{ font-size: 22px; font-weight: bold; }}
        .kpi-label {{ font-size: 11px; color: #666; margin-top: 4px; text-transform: uppercase; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
        th {{ background: #f0f0f0; padding: 9px 10px; text-align: left; font-size: 12px; color: #555; }}
        .footer {{ margin-top: 20px; font-size: 11px; color: #999; text-align: center; padding: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🎯 Value Bet Monitor — Report</h1>
        <p>{period_label}</p>
    </div>

    <div style="padding: 16px 0;">
        <div class="kpis">
            <div class="kpi">
                <div class="kpi-value">{data['total_picks']}</div>
                <div class="kpi-label">Total picks</div>
            </div>
            <div class="kpi">
                <div class="kpi-value" style="color: {clv_color};">{clv_display}</div>
                <div class="kpi-label">CLV médio real</div>
            </div>
            <div class="kpi">
                <div class="kpi-value">{beat_display}</div>
                <div class="kpi-label">Beat the line</div>
            </div>
            <div class="kpi">
                <div class="kpi-value">{beat_count}/{total_tracked}</div>
                <div class="kpi-label">Picks tracked</div>
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Jogo</th>
                    <th>Mercado</th>
                    <th style="text-align:center">Abertura</th>
                    <th style="text-align:center">Fecho (BIA)</th>
                    <th style="text-align:center">CLV real</th>
                </tr>
            </thead>
            <tbody>
                {picks_rows}
            </tbody>
        </table>

        {"<p style='color:#888; font-size:12px; margin-top:12px;'>⏳ Picks pendentes: aguardam fecho do jogo para tracking.</p>" if pending else ""}
    </div>

    <div class="footer">
        Modelo calibrado com 703 picks reais · Special One + Andrey2505<br>
        CLV esperado: +10.1% · Beat the line histórico: 76%
    </div>
</body>
</html>
    """


def send_report_email(days: int = 7) -> bool:
    """Gera e envia o report semanal por email."""
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.error("GMAIL_USER ou GMAIL_APP_PASSWORD não definidos")
        return False

    data = get_picks_for_report(days=days)

    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=days)).strftime("%d/%m")
    week_end = now.strftime("%d/%m/%Y")
    period_label = f"{week_start} a {week_end}"

    subject = f"📊 Value Bet Report — {period_label} ({data['total_picks']} picks)"
    html_body = generate_report_html(data, period_label)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
        log.info(f"Report enviado para {GMAIL_USER}")
        return True
    except Exception as e:
        log.error(f"Erro ao enviar email: {e}")
        return False


if __name__ == "__main__":
    if send_report_email():
        print("✅ Report enviado com sucesso")
    else:
        print("❌ Erro ao enviar report")
