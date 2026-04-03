"""
alert.py — Alertas Telegram + report semanal por email.
"""

import json
import logging
import os
import smtplib
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

import requests


logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


def _tg_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram nao configurado")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    resp.raise_for_status()


def _safe_float(value) -> float | None:
    try:
        num = float(value)
        return num if num > 0 else None
    except Exception:
        return None


def _normalize_two_way_probs(main_odd: float, opp_odd: float) -> tuple[float, float] | None:
    p_main_raw = 1 / main_odd
    p_opp_raw = 1 / opp_odd
    total = p_main_raw + p_opp_raw
    if total <= 0:
        return None
    return p_main_raw / total, p_opp_raw / total


def _estimate_p_exact(line: float | None) -> float:
    abs_line = abs(line or 0.0)
    if abs_line <= 1.5:
        return 0.28
    if abs_line <= 2.5:
        return 0.22
    if abs_line <= 3.5:
        return 0.18
    return 0.14


def _estimate_opp_odd(main_odd: float, margin: float = 0.05) -> float | None:
    p_main_raw = 1 / main_odd
    target_total = 1 + margin
    p_opp_raw = target_total - p_main_raw
    if p_opp_raw <= 0:
        return None
    return 1 / p_opp_raw


def _format_equiv_line(
    label: str,
    equiv_odd: float | None,
    main_odd: float,
    main_min_odd: float,
) -> str | None:
    """
    Calcula mín e edge para linhas equivalentes por proporção com o pick
    principal, em vez de recalibrar cada linha independentemente.
    Isto garante monotonia: linhas mais fáceis → mín mais baixo.
    """
    if equiv_odd is None or equiv_odd <= 1.0:
        return None
    if main_odd <= 0:
        return None

    min_odd = round(main_min_odd * (equiv_odd / main_odd), 3)
    edge = round((equiv_odd / min_odd - 1) * 100, 2)
    return f"• {label} | Mín: {min_odd:.2f} | Edge: {edge:+.2f}%"


def calc_dnb(ml_odd: float, draw_odd: float) -> float | None:
    """ML -> DNB usando probabilidades implícitas do 1X2."""
    try:
        p_home_raw = 1 / ml_odd
        p_draw_raw = 1 / draw_odd
        p_away_raw = max(0.0, 1 - p_home_raw - p_draw_raw)
        total = p_home_raw + p_draw_raw + p_away_raw
        if total <= 0:
            return None

        p_home = p_home_raw / total
        p_away = p_away_raw / total
        p_dnb = p_home / (p_home + p_away)
        if p_dnb <= 0 or p_dnb >= 1:
            return None
        return 1 / p_dnb
    except Exception:
        return None


def calc_ah025_from_ml(ml_odd: float, draw_odd: float) -> float | None:
    """ML -> AH -0.25 combinando DNB com AH -0.5."""
    try:
        p_home_raw = 1 / ml_odd
        p_draw_raw = 1 / draw_odd
        p_away_raw = max(0.0, 1 - p_home_raw - p_draw_raw)
        total = p_home_raw + p_draw_raw + p_away_raw
        if total <= 0:
            return None

        p_home = p_home_raw / total
        p_draw = p_draw_raw / total
        p_away = p_away_raw / total

        p_dnb = p_home / (p_home + p_away)
        p_ah05 = p_home

        if not (0 < p_dnb < 1 and 0 < p_ah05 < 1):
            return None

        odd_dnb = 1 / p_dnb
        odd_ah05 = 1 / p_ah05
        return 2 / (1 / odd_dnb + 1 / odd_ah05)
    except Exception:
        return None


def _quarter_line(main_odd: float, opp_odd: float, line: float | None, harder: bool) -> float | None:
    try:
        normalized = _normalize_two_way_probs(main_odd, opp_odd)
        if normalized is None:
            return None
        p_main, p_opp = normalized
        p_exact = _estimate_p_exact(line) * p_opp
        p_quarter = p_main - p_exact / 2 if harder else p_main + p_exact / 2

        if p_quarter <= 0 or p_quarter >= 1:
            return None

        implied_odd = 1 / p_quarter
        return 2 / (1 / main_odd + 1 / implied_odd)
    except Exception:
        return None


def format_equivalent_lines(
    market: str,
    selection: str,
    opening_odd: float,
    min_odd: float,
    odds_x: float | None = None,
    opp_odd: float | None = None,
) -> str:
    lines: list[str] = []

    if market == "ML" and odds_x:
        dnb = calc_dnb(opening_odd, odds_x)
        ah025 = calc_ah025_from_ml(opening_odd, odds_x)
        dnb_line = _format_equiv_line(f"DNB {selection}", dnb, opening_odd, min_odd)
        ah_line = _format_equiv_line(f"AH {selection} -0.25", ah025, opening_odd, min_odd)
        if dnb_line:
            lines.append(dnb_line)
        if ah_line:
            lines.append(ah_line)

    elif market == "DNB":
        opp = _safe_float(opp_odd) or _estimate_opp_odd(opening_odd)
        harder = _quarter_line(opening_odd, opp, 0.0, harder=True) if opp else None
        easier = _quarter_line(opening_odd, opp, 0.0, harder=False) if opp else None
        hard_line = _format_equiv_line(f"AH {selection} -0.25", harder, opening_odd, min_odd)
        easy_line = _format_equiv_line(f"AH {selection} +0.25", easier, opening_odd, min_odd)
        if hard_line:
            lines.append(hard_line)
        if easy_line:
            lines.append(easy_line)

    elif market == "Totals":
        try:
            direction, raw_line = selection.split(maxsplit=1)
            line = float(raw_line)
            opp = _safe_float(opp_odd) or _estimate_opp_odd(opening_odd)
            if opp:
                easier = _quarter_line(opening_odd, opp, line, harder=False)
                harder = _quarter_line(opening_odd, opp, line, harder=True)

                if direction == "Over":
                    low_line = _format_equiv_line(f"Over {line - 0.25:.2f}", easier, opening_odd, min_odd)
                    high_line = _format_equiv_line(f"Over {line + 0.25:.2f}", harder, opening_odd, min_odd)
                else:
                    low_line = _format_equiv_line(f"Under {line - 0.25:.2f}", harder, opening_odd, min_odd)
                    high_line = _format_equiv_line(f"Under {line + 0.25:.2f}", easier, opening_odd, min_odd)

                if low_line:
                    lines.append(low_line)
                if high_line:
                    lines.append(high_line)
        except Exception:
            pass

    elif market == "Spread":
        try:
            parts = selection.rsplit(" ", 1)
            team = parts[0] if len(parts) > 1 else selection
            line = float(parts[-1])
            opp = _safe_float(opp_odd) or _estimate_opp_odd(opening_odd)
            if opp:
                harder = _quarter_line(opening_odd, opp, line, harder=True)
                easier = _quarter_line(opening_odd, opp, line, harder=False)
                hard_line = _format_equiv_line(f"AH {team} {line - 0.25:+.2f}", harder, opening_odd, min_odd)
                easy_line = _format_equiv_line(f"AH {team} {line + 0.25:+.2f}", easier, opening_odd, min_odd)
                if hard_line:
                    lines.append(hard_line)
                if easy_line:
                    lines.append(easy_line)
        except Exception:
            pass

    if not lines:
        return ""
    return "\n━━━━━━━━━━━━━━━━━━━━\nLinhas equivalentes:\n" + "\n".join(lines)


def _format_hours_to_kickoff(hours: float | None) -> str:
    if hours is None:
        return ""
    days = hours / 24
    if hours >= 336:    # 14d+
        return f"\n🕐 {days:.0f}d antes do KO — Super Early"
    if hours >= 168:    # 7-14d
        return f"\n🕐 {days:.0f}d antes do KO — Early"
    if hours >= 72:     # 3-7d
        return f"\n🕐 {days:.0f}d antes do KO"
    return f"\n🕐 {hours:.0f}h antes do KO"


def send_alert(vb, hours_to_kickoff: float | None = None) -> None:
    eq = format_equivalent_lines(
        market=vb.market,
        selection=vb.selection,
        opening_odd=vb.odds_b365,
        min_odd=vb.min_odd,
        odds_x=vb.odds_x,
        opp_odd=vb.opp_odd,
    )
    href_line = f'\n🔗 <a href="{vb.bet_href}">Apostar na Bet365</a>' if vb.bet_href else ""
    timing_line = _format_hours_to_kickoff(hours_to_kickoff)

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
        f"{timing_line}"
        f"{href_line}"
        f"{eq}"
    )
    _tg_send(text)
    logger.info("Alerta enviado: %s %s %s (%.0fh antes KO)", vb.game, vb.market, vb.selection, hours_to_kickoff or 0)


def send_scan_summary(
    scan_label: str,
    total_leagues: int,
    total_games: int,
    elite: int,
    strong: int,
    value: int,
) -> None:
    text = (
        f"📊 Scan {scan_label}\n"
        f"Ligas: {total_leagues} | Jogos: {total_games}\n"
        f"🔥 Elite: {elite} | ✅ Strong: {strong} | 📊 Value: {value}"
    )
    _tg_send(text)


def send_scan_error(error_msg: str) -> None:
    text = f"⚠️ Scan falhou\n━━━━━━━━━━━━━━━━━━━━\n{error_msg}"
    _tg_send(text)


def _send_email(
    subject: str,
    html_body: str,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> None:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.warning("Gmail nao configurado")
        return
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(html_body, "html"))

    for filename, content, subtype in attachments or []:
        part = MIMEApplication(content, _subtype=subtype)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())


def _league_table_html(by_key: dict) -> str:
    if not by_key:
        return "<tr><td colspan='4' style='text-align:center;color:#888'>Sem dados esta semana</td></tr>"

    rows = []
    for key, picks in sorted(by_key.items(), key=lambda item: len(item[1]), reverse=True):
        tracked = [pick for pick in picks if pick.clv_real is not None]
        picks_count = len(picks)
        if tracked:
            avg_clv = round(sum(pick.clv_real for pick in tracked) / len(tracked), 1)
            beat_pct = round(sum(1 for pick in tracked if pick.clv_real > 0) / len(tracked) * 100)
            clv_str = f"{avg_clv:+.1f}%"
            beat_str = f"{beat_pct}%"
        else:
            clv_str = "Pendente"
            beat_str = "—"

        rows.append(
            f"<tr><td>{escape(str(key))}</td><td style='text-align:center'>{picks_count}</td>"
            f"<td style='text-align:center'>{clv_str}</td>"
            f"<td style='text-align:center'>{beat_str}</td></tr>"
        )
    return "\n".join(rows)


def _timing_table_html(by_timing: dict) -> str:
    if not by_timing:
        return "<tr><td colspan='3' style='text-align:center;color:#888'>Ainda sem dados de timing</td></tr>"

    band_order = ["14d+", "7-14d", "3-7d", "48-72h", "<48h", "unknown"]
    rows = []
    for band in band_order:
        stats = by_timing.get(band)
        if not stats:
            continue
        clv_str = f"{stats['avg_clv']:+.1f}%"
        beat_str = f"{stats['beat_line_pct']}%"
        rows.append(
            f"<tr><td>{band}</td>"
            f"<td style='text-align:center'>{stats['tracked']}</td>"
            f"<td style='text-align:center'>{clv_str}</td>"
            f"<td style='text-align:center'>{beat_str}</td></tr>"
        )
    return "\n".join(rows) if rows else "<tr><td colspan='4' style='text-align:center;color:#888'>Ainda sem dados de timing</td></tr>"


def _learning_rows(learning: dict) -> str:
    by_market = learning.get("by_market", {})
    if not by_market:
        return "<tr><td colspan='8' style='text-align:center;color:#888'>Ainda sem picks tracked suficientes</td></tr>"

    labels = {
        "ML": "Match Odds",
        "DNB": "Draw No Bet",
        "Spread": "Asian Handicap",
        "Totals": "Over/Under",
    }
    rows = []
    for market, stats in sorted(by_market.items(), key=lambda item: item[1]["tracked"], reverse=True):
        dev = stats.get("avg_deviation")
        mae = stats.get("mae")
        dev_str = f"{dev:+.3f}" if dev is not None else "—"
        mae_str = f"{mae:.3f}" if mae is not None else "—"
        rows.append(
            "<tr>"
            f"<td>{labels.get(market, market)}</td>"
            f"<td style='text-align:center'>{stats['tracked']}</td>"
            f"<td style='text-align:center'>{stats['avg_clv']:+.1f}%</td>"
            f"<td style='text-align:center'>{stats['beat_line_pct']}%</td>"
            f"<td style='text-align:center'>{stats['avg_edge']:+.1f}%</td>"
            f"<td style='text-align:center'>{dev_str}</td>"
            f"<td style='text-align:center'>{mae_str}</td>"
            f"<td>{escape(stats['recommendation'])}</td>"
            "</tr>"
        )
    return "\n".join(rows)


def send_weekly_report() -> None:
    from tracker import get_learning_snapshot, load_picks

    picks = load_picks()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    week_picks = []
    for pick in picks:
        try:
            kickoff_dt = datetime.strptime(pick.kickoff, "%d/%m/%Y %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue
        if kickoff_dt >= week_ago:
            week_picks.append(pick)

    tracked = [pick for pick in week_picks if pick.clv_real is not None]
    avg_clv = round(sum(pick.clv_real for pick in tracked) / len(tracked), 1) if tracked else None
    beat_pct = round(sum(1 for pick in tracked if pick.clv_real > 0) / len(tracked) * 100) if tracked else None

    if avg_clv is None:
        diag_icon, diag_msg = "⏳", "Ainda sem amostra suficiente esta semana. O tracker continua a aprender."
    elif avg_clv >= 5 and (beat_pct or 0) >= 55:
        diag_icon, diag_msg = "🟢", "Semana forte: o modelo continua alinhado com o fecho de referencia (Sbobet/Stake)."
    elif avg_clv >= 2 or (beat_pct or 0) >= 50:
        diag_icon, diag_msg = "🟡", "Semana aceitavel: ha value, mas vale continuar a monitorizar por mercado."
    elif avg_clv >= 0:
        diag_icon, diag_msg = "🟠", "CLV ainda positivo, mas fraco. Convem rever os mercados menos eficientes."
    else:
        diag_icon, diag_msg = "🔴", "CLV semanal negativo. E melhor apertar a selecao ate o historico recuperar."

    by_league = defaultdict(list)
    by_market = defaultdict(list)
    for pick in week_picks:
        by_league[pick.league].append(pick)
        label = {
            "ML": "Match Odds",
            "DNB": "Draw No Bet",
            "Spread": "Asian Handicap",
            "Totals": "Over/Under",
        }.get(pick.market, pick.market)
        by_market[label].append(pick)

    picks_rows = []
    for pick in sorted(week_picks, key=lambda item: item.kickoff):
        if pick.closing_odd_reference:
            source = f" ({pick.closing_bookmaker})" if pick.closing_bookmaker else ""
            closing = f"{pick.closing_odd_reference:.3f}{source}"
        else:
            closing = "Pendente"
        fair_str = f"{pick.fair_odd:.3f}" if pick.fair_odd else "—"
        clv_str = f"{pick.clv_real:+.1f}%" if pick.clv_real is not None else "—"
        # Desvio: fair - fecho real (positivo = modelo conservador, negativo = modelo agressivo)
        if pick.fair_odd and pick.closing_odd_reference:
            deviation = round(pick.fair_odd - pick.closing_odd_reference, 3)
            dev_str = f"{deviation:+.3f}"
        else:
            dev_str = "—"
        label = {
            "ML": "Match Odds",
            "DNB": "DNB",
            "Spread": "AH",
            "Totals": "OU",
        }.get(pick.market, pick.market)
        picks_rows.append(
            "<tr>"
            f"<td>{escape(pick.game)}</td>"
            f"<td>{escape(label)} {escape(pick.selection)}</td>"
            f"<td style='text-align:center'>{pick.opening_odd:.3f}</td>"
            f"<td style='text-align:center'>{fair_str}</td>"
            f"<td style='text-align:center'>{closing}</td>"
            f"<td style='text-align:center'>{dev_str}</td>"
            f"<td style='text-align:center'>{clv_str}</td>"
            "</tr>"
        )

    all_tracked = [pick for pick in picks if pick.clv_real is not None]
    total_tracked = len(all_tracked)
    overall_clv = round(sum(pick.clv_real for pick in all_tracked) / total_tracked, 1) if all_tracked else None
    overall_btl = round(sum(1 for pick in all_tracked if pick.clv_real > 0) / total_tracked * 100) if all_tracked else None
    learning = get_learning_snapshot()

    clv_val = f"{avg_clv:+.1f}%" if avg_clv is not None else "—"
    beat_val = f"{beat_pct}%" if beat_pct is not None else "—"
    overall_clv_str = f"{overall_clv:+.1f}%" if overall_clv is not None else "—"
    overall_btl_str = f"{overall_btl}%" if overall_btl is not None else "—"
    week_str = now.strftime("%d/%m/%Y")

    css = """
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fa;margin:0;padding:20px}
    .container{max-width:760px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08)}
    .header{background:linear-gradient(135deg,#1a1a2e 0%,#16213e 100%);padding:28px 32px;color:#fff}
    .header h1{margin:0;font-size:20px;font-weight:600}
    .header p{margin:6px 0 0;opacity:.75;font-size:13px}
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
    td{padding:8px 10px;border-bottom:1px solid #f0f0f0;color:#333;vertical-align:top}
    tr:last-child td{border-bottom:none}
    .footer{padding:16px 28px;background:#f8f9fa;text-align:center;font-size:11px;color:#aaa}
    """

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{css}</style></head><body>
<div class="container">
  <div class="header">
    <h1>📊 Value Bet Monitor — Report Semanal</h1>
    <p>Semana ate {week_str} · Abertura: Bet365 · Fecho tracked: melhor odd entre Sbobet e Stake via historical odds da odds-api.io</p>
  </div>
  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-value">{len(week_picks)}</div><div class="kpi-label">Picks</div></div>
    <div class="kpi"><div class="kpi-value">{len(tracked)}</div><div class="kpi-label">Tracked</div></div>
    <div class="kpi"><div class="kpi-value">{clv_val}</div><div class="kpi-label">CLV medio</div></div>
    <div class="kpi"><div class="kpi-value">{beat_val}</div><div class="kpi-label">Beat the line</div></div>
  </div>
  <div class="section">
    <div class="diag"><span style="font-size:20px">{diag_icon}</span><span>{escape(diag_msg)}</span></div>
    <h2>📊 CLV por liga — esta semana</h2>
    <table>
      <thead><tr><th>Liga</th><th>Picks</th><th style="text-align:center">CLV medio</th><th style="text-align:center">Beat the line</th></tr></thead>
      <tbody>{_league_table_html(dict(by_league))}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>📌 CLV por mercado — esta semana</h2>
    <table>
      <thead><tr><th>Mercado</th><th>Picks</th><th style="text-align:center">CLV medio</th><th style="text-align:center">Beat the line</th></tr></thead>
      <tbody>{_league_table_html(dict(by_market))}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>🧠 Aprendizagem do modelo</h2>
    <table>
      <thead><tr><th>Mercado</th><th>Tracked</th><th style="text-align:center">CLV medio</th><th style="text-align:center">Beat line</th><th style="text-align:center">Edge medio</th><th style="text-align:center">Desvio Fair</th><th style="text-align:center">MAE</th><th>Recomendacao</th></tr></thead>
      <tbody>{_learning_rows(learning)}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>🕐 CLV por antecedencia (timing)</h2>
    <table>
      <thead><tr><th>Antecedencia</th><th>Picks</th><th style="text-align:center">CLV medio</th><th style="text-align:center">Beat the line</th></tr></thead>
      <tbody>{_timing_table_html(learning.get("by_timing", {}))}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>🎯 Detalhe por jogo — esta semana</h2>
    <table>
      <thead><tr><th>Jogo</th><th>Mercado</th><th style="text-align:center">Abertura</th><th style="text-align:center">Fair</th><th style="text-align:center">Fecho Ref</th><th style="text-align:center">Desvio</th><th style="text-align:center">CLV real</th></tr></thead>
      <tbody>{''.join(picks_rows) or "<tr><td colspan='7' style='text-align:center;color:#888'>Sem dados esta semana</td></tr>"}</tbody>
    </table>
  </div>
  <div class="section">
    <h2>📈 Historico acumulado</h2>
    <table>
      <thead><tr><th>Picks totais tracked</th><th style="text-align:center">CLV medio total</th><th style="text-align:center">Beat the line total</th></tr></thead>
      <tbody><tr><td style="text-align:center">{total_tracked}</td><td style="text-align:center">{overall_clv_str}</td><td style="text-align:center">{overall_btl_str}</td></tr></tbody>
    </table>
  </div>
  <div class="footer">Value Bet Monitor · gerado automaticamente</div>
</div>
</body></html>"""

    _send_email(subject=f"📊 Value Bet Report — {week_str}", html_body=html)
    logger.info("Report semanal enviado")


def send_export() -> None:
    from tracker import get_learning_snapshot, load_picks

    picks = load_picks()
    generated_at = datetime.now(timezone.utc)
    data = {
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M UTC"),
        "learning": get_learning_snapshot(),
        "picks": [
            {
                "pick_id": pick.pick_id,
                "game": pick.game,
                "league": pick.league,
                "league_slug": pick.league_slug,
                "market": pick.market,
                "selection": pick.selection,
                "kickoff": pick.kickoff,
                "opening_odd": pick.opening_odd,
                "fair_odd": pick.fair_odd,
                "edge_pct": pick.edge_pct,
                "historical_event_id": pick.historical_event_id,
                "singbet_open": pick.singbet_open,
                "closing_odd_reference": pick.closing_odd_reference,
                "closing_bookmaker": pick.closing_bookmaker,
                "clv_real": pick.clv_real,
                "tracked_at": pick.tracked_at,
                "first_seen_at": pick.first_seen_at,
                "alerted_at": pick.alerted_at,
                "hours_to_kickoff": pick.hours_to_kickoff,
            }
            for pick in picks
        ],
    }
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    filename = f"value_bet_export_{generated_at.strftime('%Y%m%d_%H%MUTC')}.json"
    _send_email(
        subject="📦 Value Bet Monitor — Export picks_log",
        html_body=(
            f"<p>Segue em anexo o export JSON do Value Bet Monitor.</p>"
            f"<p>Ficheiro: <b>{escape(filename)}</b><br>"
            f"Picks: <b>{len(picks)}</b></p>"
        ),
        attachments=[(filename, payload.encode("utf-8"), "json")],
    )
