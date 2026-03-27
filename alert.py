"""
alert.py — Alertas Telegram e report email semanal completo
Report: semana actual (com picks) + histórico acumulado (só resumo por liga)
"""

import os
import smtplib
import logging
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from collections import defaultdict

log = logging.getLogger(__name__)

TELEGRAM_TOKEN     = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "")
GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> bool:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True},
            timeout=10,
        )
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Erro Telegram: {e}")
        return False


def format_alert(game, league, kickoff, market, selection,
                 opening_odd, fair_odd, min_odd, edge_pct, level) -> str:
    return (
        f"{level}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏟 <b>{game}</b>\n"
        f"🏆 {league}\n"
        f"⏰ {kickoff}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>{market}</b> — {selection}\n"
        f"💰 1xBet: <b>{opening_odd:.3f}</b>\n"
        f"⚖️ Fair: ~{fair_odd:.2f} | Mín: <b>{min_odd:.2f}</b>\n"
        f"📈 Edge: <b>+{edge_pct:.1f}%</b> CLV esperado\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Aposta antes que o mercado corrija</i>"
    )


def format_scan_summary(total_games, value_bets, elite, strong, normal, leagues_scanned) -> str:
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
        f"CLV médio esperado: +10.1% | Beat the line: 76%\n"
        f"Threshold: Edge ≥ 10% | Silêncio: 00:00–08:00 UTC\n"
        f"Ligas: 35 | Filtro: jogos ≥ 15 Abr 2026\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Sistema a funcionar correctamente</i>"
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clv_badge(clv: float) -> str:
    if clv >= 15: return "🔥"
    if clv >= 8:  return "✅"
    if clv >= 0:  return "📊"
    return "❌"


def _clv_color(clv: float) -> str:
    return "#27ae60" if clv >= 0 else "#e74c3c"


def _beat_color(pct: float) -> str:
    if pct >= 65: return "#27ae60"
    if pct >= 50: return "#e67e22"
    return "#e74c3c"


def _badge_class(pct: float) -> str:
    if pct >= 65: return "background:#EAF3DE;color:#27500A"
    if pct >= 50: return "background:#FAEEDA;color:#633806"
    return "background:#FCEBEB;color:#791F1F"


def _diagnosis(clv_medio: float, beat_pct: float, n: int) -> tuple[str, str]:
    if n < 5:
        return (
            "⚠️ Amostra insuficiente",
            "Precisamos de mais picks tracked para tirar conclusões. Continua a monitorizar."
        )
    if clv_medio >= 8 and beat_pct >= 65:
        return (
            "🟢 Modelo a funcionar bem",
            "CLV positivo e beat the line acima de 65% confirmam value real. "
            "Continua com os parâmetros actuais."
        )
    if clv_medio >= 5 and beat_pct >= 55:
        return (
            "🟡 Modelo aceitável — margem de melhoria",
            "CLV abaixo do esperado (+10.1% histórico). "
            "Considera aumentar o threshold de edge mínimo de 10% para 12%."
        )
    if clv_medio >= 0 and beat_pct >= 50:
        return (
            "🟠 Modelo neutro — requer atenção",
            "CLV próximo de zero. Revê as ligas com CLV negativo e desactiva-as. "
            "Aumenta o threshold para 13–15%."
        )
    return (
        "🔴 Modelo com problemas — intervenção necessária",
        "CLV negativo indica que as odds já estão corrigidas quando detectadas. "
        "Recomendo pausar e recalibrar."
    )


def _diag_week_label(clv: float, beat: float, n: int) -> str:
    if n < 3: return "⚪ Poucos dados"
    if clv >= 8 and beat >= 65: return "🟢 Bom"
    if clv >= 5 and beat >= 55: return "🟡 Aceitável"
    if clv >= 0 and beat >= 50: return "🟠 Neutro"
    return "🔴 Fraco"


def _pick_row(pick) -> str:
    if pick.clv_real is not None:
        clv_str   = f"{pick.clv_real:+.1f}%"
        close_str = f"{pick.pin_closing_odd:.3f}"
        color     = _clv_color(pick.clv_real)
        emoji     = _clv_badge(pick.clv_real)
    else:
        clv_str = close_str = "—"
        color = "#888"
        emoji = "⏳"
    return f"""
<tr style="border-bottom:0.5px solid #eee">
  <td style="padding:7px 10px;font-size:12px">
    {emoji} <b>{pick.game}</b><br>
    <span style="color:#888;font-size:11px">{pick.league} · {pick.kickoff}</span>
  </td>
  <td style="padding:7px 10px;font-size:12px">{pick.market}<br>
    <span style="color:#555">{pick.selection}</span></td>
  <td style="padding:7px 10px;font-size:13px;text-align:center"><b>{pick.opening_odd:.3f}</b></td>
  <td style="padding:7px 10px;font-size:13px;text-align:center;color:#777">{close_str}</td>
  <td style="padding:7px 10px;font-size:13px;text-align:center;font-weight:bold;color:{color}">{clv_str}</td>
</tr>"""


def _league_table(by_league: dict) -> str:
    rows = ""
    sorted_leagues = sorted(
        [(l, p) for l, p in by_league.items() if any(x.clv_real is not None for x in p)],
        key=lambda x: sum(p.clv_real for p in x[1] if p.clv_real is not None) / max(sum(1 for p in x[1] if p.clv_real is not None), 1),
        reverse=True
    )
    for league, picks in sorted_leagues:
        tracked = [p for p in picks if p.clv_real is not None]
        if not tracked: continue
        clv_vals = [p.clv_real for p in tracked]
        clv_med  = sum(clv_vals) / len(clv_vals)
        beat     = sum(1 for c in clv_vals if c > 0)
        beat_pct = beat / len(clv_vals) * 100
        rows += f"""
<tr style="border-bottom:0.5px solid #eee">
  <td style="padding:7px 10px;font-size:13px"><b>{league}</b></td>
  <td style="padding:7px 10px;font-size:13px;text-align:center">{len(tracked)}</td>
  <td style="padding:7px 10px;font-size:13px;text-align:center;font-weight:bold;color:{_clv_color(clv_med)}">{clv_med:+.1f}%</td>
  <td style="padding:7px 10px;font-size:13px;text-align:center">
    <span style="display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:500;{_badge_class(beat_pct)}">{beat}/{len(tracked)} ({beat_pct:.0f}%)</span>
  </td>
</tr>"""
    return rows or '<tr><td colspan="4" style="padding:12px;color:#999;text-align:center;font-size:12px">Sem dados esta semana</td></tr>'


def _history_section(weekly_stats: list) -> str:
    if not weekly_stats:
        return ""
    weeks_html = ""
    for week in weekly_stats:
        label    = week["label"]
        n        = week["n"]
        clv_med  = week["clv_medio"]
        beat_cnt = week["beat_count"]
        beat_pct = week["beat_pct"]
        by_league = week["by_league"]
        diag_lbl = _diag_week_label(clv_med, beat_pct, n)
        clv_col  = "#2ecc71" if clv_med >= 0 else "#e74c3c"
        league_rows = _league_table(by_league)
        weeks_html += f"""
<div style="margin-bottom:14px;border:0.5px solid #ddd;border-radius:8px;overflow:hidden">
  <div style="background:#2c3e50;padding:10px 14px;display:flex;align-items:center;gap:14px;flex-wrap:wrap">
    <span style="color:white;font-weight:500;font-size:13px">📅 Semana {label}</span>
    <span style="color:#ccc;font-size:12px">{n} picks</span>
    <span style="font-weight:500;font-size:13px;color:{clv_col}">{clv_med:+.1f}% CLV</span>
    <span style="color:#ccc;font-size:12px">{beat_cnt}/{n} beat ({beat_pct:.0f}%)</span>
    <span style="color:#bdc3c7;font-size:11px">{diag_lbl}</span>
  </div>
  <table style="width:100%;border-collapse:collapse">
    <thead><tr style="background:#f5f5f5">
      <th style="padding:6px 10px;text-align:left;font-size:11px;color:#666">Liga</th>
      <th style="padding:6px 10px;text-align:center;font-size:11px;color:#666">Picks</th>
      <th style="padding:6px 10px;text-align:center;font-size:11px;color:#666">CLV médio</th>
      <th style="padding:6px 10px;text-align:center;font-size:11px;color:#666">Beat the line</th>
    </tr></thead>
    <tbody>{league_rows}</tbody>
  </table>
</div>"""
    return f"""
<div style="margin-top:28px">
  <h2 style="font-size:15px;font-weight:500;margin:0 0 12px;padding-bottom:6px;border-bottom:2px solid #2c3e50">
    📚 Histórico acumulado por semana
  </h2>
  {weeks_html}
</div>"""


# ── Report semanal ────────────────────────────────────────────────────────────

def send_weekly_report(days: int = 7) -> bool:
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        log.error("Gmail não configurado")
        return False

    from tracker import get_picks_for_report, get_all_weekly_stats, get_cumulative_stats
    data         = get_picks_for_report(days=days)
    weekly_stats = get_all_weekly_stats()
    cumulative   = get_cumulative_stats()

    now    = datetime.now(timezone.utc)
    period = f"{(now - timedelta(days=days)).strftime('%d/%m')} a {now.strftime('%d/%m/%Y')}"
    subject = f"📊 Value Bet Report — {period} ({data['total_picks']} picks)"

    clv_med    = data["clv_medio"]
    beat_pct   = data["beat_line_pct"]
    beat_count = data["beat_line_count"]
    n_tracked  = data["total_tracked"]
    diag, rec  = _diagnosis(clv_med, beat_pct, n_tracked)

    all_picks   = sorted(data["tracked"] + data["pending"], key=lambda p: p.kickoff_ts, reverse=True)
    picks_rows  = "".join(_pick_row(p) for p in all_picks)
    pending_note = (
        f"<p style='color:#888;font-size:12px;margin-top:8px'>"
        f"⏳ {len(data['pending'])} pick(s) pendentes — fecho ainda não disponível.</p>"
    ) if data["pending"] else ""

    by_league_week: dict = defaultdict(list)
    for p in data["tracked"]:
        by_league_week[p.league].append(p)
    league_rows_week = _league_table(dict(by_league_week))

    history_html = _history_section(weekly_stats)

    cum_n    = cumulative["n"]
    cum_clv  = cumulative["clv_medio"]
    cum_beat = cumulative["beat_pct"]
    clv_disp = f"{clv_med:+.1f}%" if n_tracked > 0 else "—"
    beat_disp = f"{beat_pct:.0f}%" if n_tracked > 0 else "—"

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
body{{font-family:Arial,sans-serif;max-width:720px;margin:0 auto;color:#222;font-size:13px}}
table{{width:100%;border-collapse:collapse}}
th{{background:#f0f0f0;padding:7px 10px;text-align:left;font-size:11px;color:#555;font-weight:600}}
</style></head><body>

<div style="background:#1a1a2e;color:white;padding:18px 22px;border-radius:8px 8px 0 0">
  <h1 style="margin:0;font-size:18px;font-weight:500;color:white">🎯 Value Bet Monitor — Report Semanal</h1>
  <p style="margin:3px 0 0;font-size:12px;color:#aaa">{period}</p>
</div>

<div style="padding:14px 0">

  <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px">
    <div style="background:#f8f9fa;border-radius:8px;padding:10px 12px;text-align:center">
      <div style="font-size:20px;font-weight:500">{data['total_picks']}</div>
      <div style="font-size:10px;color:#888;margin-top:2px;text-transform:uppercase">Picks semana</div>
    </div>
    <div style="background:#f8f9fa;border-radius:8px;padding:10px 12px;text-align:center">
      <div style="font-size:20px;font-weight:500;color:{_clv_color(clv_med)}">{clv_disp}</div>
      <div style="font-size:10px;color:#888;margin-top:2px;text-transform:uppercase">CLV médio</div>
    </div>
    <div style="background:#f8f9fa;border-radius:8px;padding:10px 12px;text-align:center">
      <div style="font-size:20px;font-weight:500">{beat_disp}</div>
      <div style="font-size:10px;color:#888;margin-top:2px;text-transform:uppercase">Beat the line</div>
    </div>
    <div style="background:#f8f9fa;border-radius:8px;padding:10px 12px;text-align:center">
      <div style="font-size:20px;font-weight:500">{beat_count}/{n_tracked}</div>
      <div style="font-size:10px;color:#888;margin-top:2px;text-transform:uppercase">Tracked</div>
    </div>
  </div>

  <div style="background:#f8f9fa;border-left:3px solid #3498db;border-radius:0 8px 8px 0;padding:10px 14px;margin-bottom:14px">
    <div style="font-weight:500;font-size:13px;margin-bottom:4px">{diag}</div>
    <div style="font-size:12px;color:#555;line-height:1.5">{rec}</div>
  </div>

  <div style="background:#EAF3DE;border-radius:8px;padding:10px 16px;display:flex;gap:24px;margin-bottom:16px">
    <div><div style="font-size:18px;font-weight:500;color:#27500A">{cum_n}</div><div style="font-size:10px;color:#3B6D11;text-transform:uppercase">Total histórico</div></div>
    <div><div style="font-size:18px;font-weight:500;color:{_clv_color(cum_clv)}">{cum_clv:+.1f}%</div><div style="font-size:10px;color:#3B6D11;text-transform:uppercase">CLV acumulado</div></div>
    <div><div style="font-size:18px;font-weight:500;color:#27500A">{cum_beat:.0f}%</div><div style="font-size:10px;color:#3B6D11;text-transform:uppercase">Beat acumulado</div></div>
  </div>

  <h2 style="font-size:14px;font-weight:500;margin:16px 0 8px;padding-bottom:5px;border-bottom:1.5px solid #eee">
    📊 CLV por liga — esta semana
  </h2>
  <table>
    <thead><tr><th>Liga</th><th>Picks</th><th style="text-align:center">CLV médio</th><th style="text-align:center">Beat the line</th></tr></thead>
    <tbody>{league_rows_week}</tbody>
  </table>

  <h2 style="font-size:14px;font-weight:500;margin:20px 0 8px;padding-bottom:5px;border-bottom:1.5px solid #eee">
    🎯 Detalhe por jogo — esta semana
  </h2>
  <table>
    <thead><tr><th>Jogo</th><th>Mercado</th><th style="text-align:center">Abertura</th><th style="text-align:center">Fecho PIN</th><th style="text-align:center">CLV real</th></tr></thead>
    <tbody>{picks_rows}</tbody>
  </table>
  {pending_note}

  {history_html}

</div>

<div style="margin-top:20px;font-size:10px;color:#aaa;text-align:center;padding-top:10px;border-top:0.5px solid #eee">
  Modelo calibrado com 703 picks · Special One + Andrey2505 · CLV esperado: +10.1% · Beat the line histórico: 76%
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
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

