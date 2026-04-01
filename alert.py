"""
alert.py — Alertas Telegram + Report semanal por email.
"""

import os, json, smtplib, logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER        = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


# ─── Telegram ────────────────────────────────────────────────────────────────

def _tg_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram não configurado")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, timeout=15)
    resp.raise_for_status()


# ─── Cálculos de linhas equivalentes ─────────────────────────────────────────

def calc_dnb(ml_odd: float, draw_odd: float) -> float | None:
    """ML → DNB (exclui empate)."""
    try:
        p_home = 1 / ml_odd
        p_draw = 1 / draw_odd
        p_away = 1 - p_home - p_draw
        if p_away <= 0:
            return None
        p_dnb = p_home / (p_home + p_away)
        return round(1 / p_dnb, 2)
    except Exception:
        return None


def calc_ah025(ml_odd: float, draw_odd: float) -> float | None:
    """ML → AH -0.25 (split entre DNB e AH -0.5). Mais difícil que DNB → odd maior."""
    try:
        p_home = 1 / ml_odd
        p_draw = 1 / draw_odd
        p_away = 1 - p_home - p_draw
        if p_away <= 0:
            return None
        # AH 0 (DNB)
        odd_dnb = 1 / (p_home / (p_home + p_away))
        # AH -0.5: empate = meia perda
        p_ah05 = p_home / (p_home + p_away + p_draw / 2)
        if p_ah05 <= 0:
            return None
        odd_ah05 = 1 / p_ah05
        # AH -0.25 = média harmónica
        return round(2 / (1 / odd_dnb + 1 / odd_ah05), 2)
    except Exception:
        return None


def _quarter_harder(main_odd: float, opp_odd: float) -> float | None:
    """Quarter line mais difícil (odd maior) — ex: Over 2.75, Under 2.25, AH -0.25."""
    try:
        p_main  = 1 / main_odd
        p_exact = 0.18 / opp_odd
        p_q     = p_main - p_exact / 2
        if p_q <= 0 or p_q >= 1:
            return None
        return round(2 / (1 / main_odd + 1 / (1 / p_q)), 2)
    except Exception:
        return None


def _quarter_easier(main_odd: float, opp_odd: float) -> float | None:
    """Quarter line mais fácil (odd menor) — ex: Over 2.25, Under 2.75, AH +0.25."""
    try:
        p_main  = 1 / main_odd
        p_exact = 0.18 / opp_odd
        p_q     = p_main + p_exact / 2
        if p_q <= 0 or p_q >= 1:
            return None
        return round(2 / (1 / main_odd + 1 / (1 / p_q)), 2)
    except Exception:
        return None


def format_equivalent_lines(market: str, selection: str,
                             opening_odd: float,
                             odds_x: float | None = None,
                             opp_odd: float | None = None) -> str:
    lines = []

    if market == "DNB":
        # DNB = AH 0
        # AH -0.25: empate perde metade → mais difícil → odd MAIOR
        # AH +0.25: empate ganha metade → mais fácil → odd MENOR
        try:
            opp_dnb = round(1 / (1 - 1 / opening_odd), 3)
            harder  = _quarter_harder(opening_odd, opp_dnb)
            easier  = _quarter_easier(opening_odd, opp_dnb)
            if harder:
                lines.append(f"• AH {selection} -0.25: {harder:.2f}")
            if easier:
                lines.append(f"• AH {selection} +0.25: {easier:.2f}")
        except Exception:
            pass

    elif market == "ML":
        if odds_x:
            dnb   = calc_dnb(opening_odd, odds_x)
            ah025 = calc_ah025(opening_odd, odds_x)
            team  = selection
            if dnb:
                lines.append(f"• DNB {team}: {dnb:.2f}")
            if ah025:
                lines.append(f"• AH {team} -0.25: {ah025:.2f}")

    elif market == "Totals":
        try:
            parts     = selection.split()
            direction = parts[0]
            line      = float(parts[1])
            opp       = opp_odd or round(1 / (1 - 1 / opening_odd - 0.025), 2)

            if direction == "Over":
                # Over 2.25: mais fácil → odd menor
                # Over 2.75: mais difícil → odd maior
                easier = _quarter_easier(opening_odd, opp)
                harder = _quarter_harder(opening_odd, opp)
                if easier:
                    lines.append(f"• Over {line - 0.25}: {easier:.2f}")
                if harder:
                    lines.append(f"• Over {line + 0.25}: {harder:.2f}")
            else:
                # Under 2.25: mais difícil → odd maior
                # Under 2.75: mais fácil → odd menor
                harder = _quarter_harder(opening_odd, opp)
                easier = _quarter_easier(opening_odd, opp)
                if harder:
                    lines.append(f"• Under {line - 0.25}: {harder:.2f}")
                if easier:
                    lines.append(f"• Under {line + 0.25}: {easier:.2f}")
        except Exception:
            pass

    elif market == "Spread":
        try:
            parts = selection.rsplit(" ", 1)
            line  = float(parts[-1])
            team  = parts[0] if len(parts) > 1 else selection
            opp   = opp_odd or round(1 / (1 - 1 / opening_odd - 0.01), 2)
            # AH -0.25 mais difícil → odd maior; AH +0.25 mais fácil → odd menor
            harder = _quarter_harder(opening_odd, opp)
            easier = _quarter_easier(opening_odd, opp)
            if harder:
                lines.append(f"• AH {team} {line - 0.25:+.2f}: {harder:.2f}")
            if easier:
                lines.append(f"• AH {team} {line + 0.25:+.2f}: {easier:.2f}")
        except Exception:
            pass

    if not lines:
        return ""
    return "\n━━━━━━━━━━━━━━━━━━━━\nLinhas equivalentes:\n" + "\n".join(lines)


# ─── Alerta principal ─────────────────────────────────────────────────────────

def send_alert(vb) -> None:
    eq = format_equivalent_lines(
        market=vb.market,
        selection=vb.selection,
        opening_odd=vb.odds_b365,
        odds_x=vb.odds_x,
        opp_odd=vb.opp_odd,
    )
    href_line = f'\n🔗 <a href="{vb.bet_href}">Apostar na Bet365</a>' if vb.bet_href else ""

    text = (
        f"{vb.level}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏟 {vb.game}\n"
        f"🏆 {vb.league}\n"
        f"⏰ {vb.kickoff}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 {vb.market} — {vb.selection}\n"
        f"💰 Bet365: {vb.odds_b365:.3f}\n"
        f"⚖️ Fair: ~{vb.fair_odd} | Mín: {vb.min_odd}\n"
        f"📈 Edge: +{vb.edge_pct}% CLV esperado"
        f"{href_line}"
        f"{eq}"
    )
    _tg_send(text)
    logger.info(f"Alerta enviado: {vb.game} {vb.market} {vb.selection}")


def send_scan_summary(scan_label: str, total_leagues: int,
                      total_games: int, elite: int,
                      strong: int, value: int) -> None:
    text = (
        f"📊 Scan {scan_label}\n"
        f"Ligas: {total_leagues} | Jogos: {total_games}\n"
        f"🔥 Elite: {elite} | ✅ Strong: {strong} | 📊 Value: {value}"
    )
    _tg_send(text)


# ─── Report semanal ───────────────────────────────────────────────────────────

def _league_table_html(by_key: dict) -> str:
    if not by_key:
        return "<tr><td colspan='4' style='text-align:center;color:#888'>Sem dados esta semana</td></tr>"
    rows = []
    for key, picks in sorted(by_key.items(), key=lambda x: len(x[1]), reverse=True):
        n       = len(picks)
        tracked = [p for p in picks if p.clv_real is not None]
        if tracked:
            avg_clv = round(sum(p.clv_real for p in tracked) / len(tracked), 1)
            btl     = round(sum(1 for p in tracked if p.clv_real > 0) / len(tracked) * 100)
            clv_str = f"+{avg_clv}%" if avg_clv > 0 else f"{avg_clv}%"
            btl_str = f"{btl}%"
        else:
            clv_str = "Pendente"
            btl_str = "—"
        rows.append(
            f"<tr><td>{key}</td><td style='text-align:center'>{n}</td>"
            f"<td style='text-align:center'>{clv_str}</td>"
            f"<td style='text-align:center'>{btl_str}</td></tr>"
        )
    return "\n".join(rows)


def send_weekly_report() -> None:
    from tracker import load_picks

    picks    = load_picks()
    now      = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    week_picks = []
    for p in picks:
        try:
            dt = datetime.strptime(p.kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
            if dt >= week_ago:
                week_picks.append(p)
        except Exception:
            pass

    total   = len(week_picks)
    tracked = [p for p in week_picks if p.clv_real is not None]
    avg_clv = round(sum(p.clv_real for p in tracked) / len(tracked), 1) if tracked else None
    btl_pct = round(sum(1 for p in tracked if p.clv_real > 0) / len(tracked) * 100) if tracked else None

    if avg_clv is None:
        diag_icon, diag_msg = "⏳", "Aguardar primeiros resultados tracked."
    elif avg_clv >= 5 and btl_pct >= 55:
        diag_icon, diag_msg = "🟢", "Modelo a funcionar bem. Manter threshold."
    elif avg_clv >= 2 or btl_pct >= 50:
        diag_icon, diag_msg = "🟡", "Resultado aceitável. Continuar a monitorizar."
    elif avg_clv >= 0:
        diag_icon, diag_msg = "🟠", "CLV positivo mas fraco. Considerar aumentar threshold."
    else:
        diag_icon, diag_msg = "🔴", "CLV negativo. Rever calibração."

    by_league = defaultdict(list)
    by_market = defaultdict(list)
    for p in week_picks:
        by_league[p.league].append(p)
        label = {"ML": "Match Odds", "DNB": "Draw No Bet",
                 "Spread": "Asian Handicap", "Totals": "Over/Under"}.get(p.market, p.market)
        by_market[label].append(p)

    league_rows = _league_table_html(dict(by_league))
    market_rows = _league_table_html(dict(by_market))

    picks_rows = ""
    for p in sorted(week_picks, key=lambda x: x.kickoff):
        closing = f"{p.closing_odd_sbo:.3f}" if p.closing_odd_sbo else "Pendente"
        clv_str = (f"+{p.clv_real}%" if p.clv_real and p.clv_real > 0
                   else f"{p.clv_real}%" if p.clv_real is not None else "—")
        label   = {"ML": "Match Odds", "DNB": "DNB", "Spread": "AH",
                   "Totals": "OU"}.get(p.market, p.market)
        picks_rows += (
            f"<tr><td>{p.game}</td><td>{label} {p.selection}</td>"
            f"<td style='text-align:center'>{p.opening_odd:.3f}</td>"
            f"<td style='text-align:center'>{closing}</td>"
            f"<td style='text-align:center'>{clv_str}</td></tr>\n"
        )

    all_tracked  = [p for p in picks if p.clv_real is not None]
    total_tracked = len(all_tracked)
    overall_clv  = round(sum(p.clv_real for p in all_tracked) / total_tracked, 1) if all_tracked else None
    overall_btl  = round(sum(1 for p in all_tracked if p.clv_real > 0) / total_tracked * 100) if all_tracked else None

    clv_val         = f"+{avg_clv}%" if avg_clv and avg_clv > 0 else (f"{avg_clv}%" if avg_clv is not None else "—")
    btl_val         = f"{btl_pct}%" if btl_pct is not None else "—"
    overall_clv_str = f"+{overall_clv}%" if overall_clv and overall_clv > 0 else (f"{overall_clv}%" if overall_clv is not None else "—")
    overall_btl_str = f"{overall_btl}%" if overall_btl is not None else "—"
    week_str        = now.strftime("%d/%m/%Y")

    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;margin:0;padding:20px}
    .container{max-width:700px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)}
    .header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);padding:28px 32px;color:#fff}
    .header h1{margin:0;font-size:20px;font-weight:600}
    .header p{margin:6px 0 0;opacity:.7;font-size:13px}
    .kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:0;border-bottom:1px solid #eee}
    .kpi{padding:18px 16px;text-align:center;border-right:1px solid #eee}
    .kpi:last-child{border-right:none}
    .kpi-value{font-size:22px;font-weight:700;color:#1a1a2e}
    .kpi-label{font-size:11px;color:#888;margin-top:3px;text-transform:uppercase;letter-spacing:.5px}
    .section{padding:20px 28px}
    h2{font-size:14px;font-weight:600;margin:0 0 12px;color:#1a1a2e;padding-bottom:6px;border-bottom:2px solid #f0f0f0}
    .diag{display:flex;align-items:flex-start;gap:10px;padding:12px 16px;background:#f8f9fa;border-radius:8px;margin-bottom:16px;font-size:13px}
    table{width:100%;border-collapse:collapse;font-size:13px}
    th{background:#f8f9fa;padding:8px 10px;text-align:left;color:#555;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px}
    td{padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#333}
    tr:last-child td{border-bottom:none}
    .footer{padding:16px 28px;background:#f8f9fa;text-align:center;font-size:11px;color:#aaa}
    """

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div class="container">
  <div class="header">
    <h1>📊 Value Bet Monitor — Report Semanal</h1>
    <p>Semana até {week_str} · Odds: Bet365 abertura · CLV: Sbobet fecho</p>
  </div>
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-value">{total}</div><div class="kpi-label">Picks</div></div>
    <div class="kpi"><div class="kpi-value">{len(tracked)}</div><div class="kpi-label">Tracked</div></div>
    <div class="kpi"><div class="kpi-value">{clv_val}</div><div class="kpi-label">CLV médio</div></div>
    <div class="kpi"><div class="kpi-value">{btl_val}</div><div class="kpi-label">Beat the line</div></div>
  </div>
  <div class="section">
    <div class="diag"><span style="font-size:20px">{diag_icon}</span><span>{diag_msg}</span></div>
    <h2>📊 CLV por liga — esta semana</h2>
    <table>
      <thead><tr><th>Liga</th><th>Picks</th><th style="text-align:center">CLV médio</th><th style="text-align:center">Beat the line</th></tr></thead>
      <tbody>{league_rows}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>📌 CLV por mercado — esta semana</h2>
    <table>
      <thead><tr><th>Mercado</th><th>Picks</th><th style="text-align:center">CLV médio</th><th style="text-align:center">Beat the line</th></tr></thead>
      <tbody>{market_rows}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>🎯 Detalhe por jogo — esta semana</h2>
    <table>
      <thead><tr><th>Jogo</th><th>Mercado</th><th style="text-align:center">Abertura</th><th style="text-align:center">Fecho SBO</th><th style="text-align:center">CLV real</th></tr></thead>
      <tbody>{picks_rows or "<tr><td colspan='5' style='text-align:center;color:#888'>Sem dados esta semana</td></tr>"}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>📈 Histórico acumulado</h2>
    <table>
      <thead><tr><th>Picks totais tracked</th><th style="text-align:center">CLV médio total</th><th style="text-align:center">Beat the line total</th></tr></thead>
      <tbody><tr>
        <td style="text-align:center">{total_tracked}</td>
        <td style="text-align:center">{overall_clv_str}</td>
        <td style="text-align:center">{overall_btl_str}</td>
      </tr></tbody>
    </table>
  </div>
  <div class="footer">Value Bet Monitor · gerado automaticamente</div>
</div>
</body></html>"""

    _send_email(subject=f"📊 Value Bet Report — {week_str}", html_body=html)
    logger.info("Report semanal enviado")


def _send_email(subject: str, html_body: str) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning("Gmail não configurado")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())


def send_export() -> None:
    from tracker import load_picks
    picks = load_picks()
    data  = json.dumps([{
        "pick_id":          p.pick_id,
        "game":             p.game,
        "league":           p.league,
        "market":           p.market,
        "selection":        p.selection,
        "kickoff":          p.kickoff,
        "opening_odd":      p.opening_odd,
        "edge_pct":         p.edge_pct,
        "closing_odd_sbo":  p.closing_odd_sbo,
        "clv_real":         p.clv_real,
    } for p in picks], indent=2)
    _send_email(
        subject="📦 Value Bet Monitor — Export picks_log",
        html_body=f"<pre style='font-family:monospace;font-size:12px'>{data}</pre>",
    )
