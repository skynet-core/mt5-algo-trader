"""Generate human-friendly HTML reports from backtest artifacts.

- ``job_report(job_dir)``: one page per job (``report.html``) — summary cards,
  an interactive SVG of every trade (entry/exit price points, open→close
  connector, hover a point/line for full details) and the equity curve.
- ``index_report(results_root)``: ``index.html`` aggregate table linking to
  each job page.

Pure stdlib; called from ``mt5lib`` (non-fatally) after each job and after
aggregation.  Trades come from ``trades.tsv`` — the observation-only event log
the EA writes to ``MQL5/Files`` and we harvest into the job directory.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, UTC
from pathlib import Path

TRADES_REL = "trades.tsv"

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _iso(ts: float | int | None) -> str:
    if not ts:
        return "?"
    dt = datetime.fromtimestamp(float(ts), UTC)
    return dt.strftime("%Y-%m-%d %H:%M")


def load_trades(job_dir: Path) -> list[dict]:
    """Group the event log into one record per position (open + optional close)."""
    path = job_dir / TRADES_REL
    positions: dict[int | str, dict] = {}
    order: list[int | str] = []
    if not path.is_file():
        return []
    for line in path.read_text(errors="replace").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        event, ticket, t, side, price, vol, profit, pos_id = parts[:8]
        try:
            key = int(pos_id) if pos_id.isdigit() else pos_id
            rec = positions.get(key)
            if rec is None:
                rec = {"pos": key, "side": side, "vol": float(vol), "profit": 0.0,
                       "open_t": int(t), "open_price": float(price),
                       "close_t": None, "close_price": None}
                positions[key] = rec
                order.append(key)
            elif event == "open" and rec["open_t"] and int(t) < rec["open_t"]:
                rec["open_t"] = int(t)
                rec["open_price"] = float(price)
                rec["side"] = side
            elif event == "close":
                rec["profit"] += float(profit)
                if int(t) > (rec["close_t"] or 0):
                    rec["close_t"] = int(t)
                    rec["close_price"] = float(price)
        except (ValueError, TypeError):
            continue  # tolerate a truncated final line
    return [positions[k] for k in order]


def job_report(job_dir: Path) -> Path | None:
    meta = _read_json(job_dir / "meta.json")
    summary = _read_json(job_dir / "summary.json")
    raw = _read_json(job_dir / "summary-raw.json")
    if not (meta or summary):
        return None
    trades = load_trades(job_dir)
    doc = _job_page(meta, summary, raw, trades)
    out = job_dir / "report.html"
    out.write_text(doc)
    return out


# ---------------------------------------------------------------------------
# Job page
# ---------------------------------------------------------------------------

_CARD_DEFS = (
    ("Net P/L", lambda s: (lambda: _fmt_money(s.get("net_profit")))(),
     lambda s: "good" if (s.get("net_profit") or 0) >= 0 else "bad", lambda s: s.get("net_profit") is not None),
    ("Profit factor", lambda s: _fmt(s.get("profit_factor"), 2), None, lambda s: s.get("profit_factor") is not None),
    ("Win rate", lambda s: _fmt(s.get("profitable_trades_pct"), 1) + "%", None,
     lambda s: s.get("profitable_trades_pct") is not None),
    ("Trades", lambda s: _fmt(s.get("trades"), 0), None, lambda s: s.get("trades") is not None),
    ("Max drawdown",
     lambda s: f"{_fmt_money(s.get('max_drawdown'))}"
       + (f" ({_fmt(s.get('max_drawdown_pct'), 1)}%)" if s.get("max_drawdown_pct") is not None else ""),
     None, lambda s: s.get("max_drawdown") is not None),
    ("Score", lambda s: _fmt(s.get("score"), 1) if s.get("score") is not None else "DQ",
     lambda s: "bad" if s.get("score") is None else None, lambda s: True),
)


def _fmt(v: float | None, digits: int) -> str:
    if v is None:
        return "–"
    if digits == 0:
        return f"{int(v):,}"
    return f"{v:,.{digits}f}"


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{'-' if v < 0 else ''}{abs(v):,.2f}"


def _cards(summary: dict) -> str:
    out = []
    for label, value_fn, cls_fn, visible in _CARD_DEFS:
        if not visible(summary):
            continue
        cls = cls_fn(summary) if cls_fn else ""
        out.append(f'<div class="card {cls}"><div class="k">{label}</div>'
                   f'<div class="v">{html.escape(value_fn(summary))}</div></div>')
    return "".join(out)


def _avg_trade(raw: dict, needle: str) -> str:
    for key, val in raw.items():
        if needle in key.lower():
            return html.escape(str(val))
    return ""


def _svg(meta: dict, summary: dict, trades: list[dict]) -> str:
    W, H = 1040, 430
    ML, MR, MT, MB = 70, 16, 18, 34
    iw, ih = W - ML - MR, H - MT - MB
    ts = [t["open_t"] for t in trades] + [t["close_t"] for t in trades if t["close_t"]]
    prices = [t["open_price"] for t in trades] + [t["close_price"] for t in trades if t["close_price"]]
    if not ts or not prices:
        return ('<div class="nochart">No trade event data for this job '
                '(trades.tsv missing — pre-logging-harness run).</div>')
    t0, t1 = min(ts), max(ts)
    if t0 == t1:
        t0, t1 = t0 - 3600, t1 + 3600
    pmin, pmax = min(prices), max(prices)
    if pmin == pmax:
        pmin, pmax = pmin - 0.001, pmax + 0.001

    def X(t: float) -> float:
        return ML + (t - t0) / (t1 - t0) * iw

    def Y(p: float) -> float:
        return MT + (1 - (p - pmin) / (pmax - pmin)) * ih

    grid: list[str] = []
    # price grid lines (5)
    for i in range(6):
        p = pmin + (pmax - pmin) * i / 5
        y = Y(p)
        grid.append(f'<line x1="{ML}" x2="{W - MR}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>'
                    f'<text x="{ML - 8}" y="{y + 4:.1f}" class="ax" text-anchor="end">{p:.5f}</text>')
    # time labels (7)
    for i in range(7):
        t = t0 + (t1 - t0) * i / 6
        x = X(t)
        lbl = datetime.fromtimestamp(t, UTC).strftime("%b %d %y")
        grid.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{MT}" y2="{H - MB}" class="grid"/>'
                    f'<text x="{x:.1f}" y="{H - MB + 18}" class="ax" text-anchor="middle">{lbl}</text>')

    body: list[str] = []
    eq: list[tuple[float, float]] = []
    base = float(meta.get("deposit") or 1000)
    running = base
    for i, t in enumerate(trades):
        x0, y0 = X(t["open_t"]), Y(t["open_price"])
        ocls = "b" if t["side"] == "buy" else "s"
        body.append(f'<circle class="pt o {ocls}" data-i="{i}" cx="{x0:.1f}" cy="{y0:.1f}" r="4.5"/>')
        if t["close_t"]:
            x1, y1 = X(t["close_t"]), Y(t["close_price"])
            cls = "win" if t["profit"] >= 0 else "lose"
            body.append(f'<line class="link {cls}" data-i="{i}" x1="{x0:.1f}" y1="{y0:.1f}" '
                        f'x2="{x1:.1f}" y2="{y1:.1f}"/>')
            body.append(f'<circle class="pt c {cls}" data-i="{i}" cx="{x1:.1f}" cy="{y1:.1f}" r="4.5"/>')
            running += t["profit"]
            eq.append((t["close_t"], running))

    if len(eq) >= 2:
        emin, emax = min(v for _, v in eq), max(v for _, v in eq)
        if emin == emax:
            emin, emax = emin - 1, emax + 1

        def EY(v: float) -> float:
            return MT + (1 - (v - emin) / (emax - emin)) * ih

        body.append('<polyline class="equity" points="'
                    + " ".join(f"{X(t):.1f},{EY(v):.1f}" for t, v in sorted(eq)) + '"/>')
        body.append(f'<text class="eq" x="{W - MR - 4:.0f}" y="{MT + 12}" text-anchor="end">'
                    f'equity {base:,.0f} → {running:,.2f}</text>')

    return (f'<div class="chartwrap"><svg id="chart" viewBox="0 0 {W} {H}" width="{W}" height="{H}">'
            f'<rect x="0" y="0" width="{W}" height="{H}" class="bg"/>'
            + "".join(grid) + "".join(body) +
            f"<text class='legend' x='{ML}' y='{H - 4}'>"
            "● entry (up=buy / dn=sell)   ◆ exit   — win/loss line   ~ equity curve   · hover for details</text>"
            "</svg><div id='tip'></div></div>")


def _job_page(meta: dict, summary: dict, raw: dict, trades: list[dict]) -> str:
    title = f"{meta.get('version', '?')} · {meta.get('preset_id', '?')} · {meta.get('period_id', '?')}"
    dates = f"{meta.get('date_from', '?')} → {meta.get('date_to', '?')}"
    model = "every tick (real)" if meta.get("model") == 1 else "every tick (OHLC)"
    meta_line = (
        f"{html.escape(str(meta.get('symbol', '?')))} · {model} · {html.escape(str(meta.get('period_tf', '?')))} bar · "
        f"deposit {html.escape(str(meta.get('deposit', '?')))} · leverage {html.escape(str(meta.get('leverage', '?')))} · "
        f"{html.escape(str(meta.get('container', '?')))}"
    )
    extras = (
        f"avg win { _avg_trade(raw, 'average profit trade') } · "
        f"avg loss { _avg_trade(raw, 'average loss trade') } · "
        f"largest { _avg_trade(raw, 'largest profit trade') } / { _avg_trade(raw, 'largest loss trade') } · "
        f"bars { _avg_trade(raw, 'processed bars') or summary.get('bars', '') }"
    )
    trades_js = json.dumps([
        {
            "i": k,
            "side": t["side"],
            "open": _iso(t["open_t"]),
            "close": _iso(t["close_t"]),
            "open_price": t["open_price"],
            "close_price": t["close_price"],
            "vol": t["vol"],
            "profit": round(t["profit"], 2) if t["close_t"] else None,
            "open_only": t["close_t"] is None,
        }
        for k, t in enumerate(trades)
    ]).replace("</", "<\\/")
    n_open = sum(1 for t in trades if t["close_t"] is None)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
 :root {{ --bg:#0d1220; --panel:#141b2e; --ink:#dfe6f3; --mut:#8b96ad; --good:#2ecc71; --bad:#ff5c5c; --accent:#4da3ff; }}
 * {{ box-sizing:border-box; }}
 body {{ margin:0; padding:28px 36px 60px; background:var(--bg); color:var(--ink);
        font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; }}
 h1 {{ font-size:21px; margin:0 0 4px; }} .sub {{ color:var(--mut); margin-bottom:20px; }}
 .cards {{ display:flex; flex-wrap:wrap; gap:12px; margin-bottom:8px; }}
 .card {{ background:var(--panel); border:1px solid #232d47; border-radius:10px;
         padding:10px 16px; min-width:118px; }}
 .card .k {{ color:var(--mut); font-size:12px; letter-spacing:.04em; text-transform:uppercase; }}
 .card .v {{ font-size:19px; margin-top:3px; font-variant-numeric:tabular-nums; }}
 .card.good .v {{ color:var(--good); }} .card.bad .v {{ color:var(--bad); }}
 .meta2 {{ color:var(--mut); font-size:13px; margin-bottom:18px; }}
 .chartwrap {{ position:relative; background:var(--panel); border:1px solid #232d47;
              border-radius:10px; padding:8px 4px; }}
 svg text {{ font-family:inherit; }}
 .grid {{ stroke:#22304f; stroke-width:1; }} .ax {{ fill:#8b96ad; font-size:11px; }}
 .legend {{ fill:#66718c; font-size:11px; }} .eq {{ fill:#4da3ff; font-size:12px; }}
 .bg {{ fill:#101729; }}
 .link {{ stroke-width:1.3; opacity:.55; stroke-dasharray:4 3; fill:none; }}
 .link.win {{ stroke:#2ecc71; }} .link.lose {{ stroke:#ff5c5c; }}
 .link.hot {{ opacity:1; stroke-width:2.6; stroke-dasharray:none; }}
 .pt {{ stroke-width:1.3; cursor:pointer; }}
 .pt.o.b {{ fill:#4da3ff; stroke:#bcd9ff; }} .pt.o.s {{ fill:#f5a623; stroke:#ffd9a3; }}
 .pt.c.win {{ fill:#2ecc71; stroke:#c8f7da; }} .pt.c.lose {{ fill:#ff5c5c; stroke:#ffc4c4; }}
 .equity {{ fill:none; stroke:#4da3ff; stroke-width:1.6; opacity:.9; }}
 #tip {{ position:absolute; pointer-events:none; display:none; z-index:9;
        background:#0a0e1a; border:1px solid #33415f; border-radius:8px; padding:8px 10px;
        font-size:13px; line-height:1.5; white-space:nowrap; box-shadow:0 6px 24px #000a; }}
 #tip .g {{ color:#2ecc71; }} #tip .r {{ color:#ff5c5c; }} #tip .m {{ color:var(--mut); }}
 .nochart {{ padding:40px; color:var(--mut); text-align:center; }}
 .foot {{ margin-top:14px; color:#5d6880; font-size:12px; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<div class="sub">{dates}</div>
<div class="cards">{_cards(summary)}</div>
<div class="meta2">{meta_line} &nbsp;·&nbsp; {extras}
 &nbsp;·&nbsp; {len(trades)} round-trips{' (+' + str(n_open) + ' still open at test end)' if n_open else ''}</div>
{_svg(meta, summary, trades)}
<div class="foot">generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} ·
job dir <code>{html.escape(str(meta.get('version','')))}/{html.escape(str(meta.get('period_id','')))}/{html.escape(str(meta.get('preset_id','')))}</code></div>
<script>
const trades = {trades_js};
const tip = document.getElementById('tip');
const wrap = document.querySelector('.chartwrap');
function show(i, ev) {{
  const t = trades[i]; if (!t) return;
  const p = t.profit === null ? '<span class="m">still open</span>'
          : (t.profit >= 0 ? '<span class="g">+' + t.profit.toFixed(2) + '</span>'
                           : '<span class="r">' + t.profit.toFixed(2) + '</span>');
  tip.innerHTML = (t.side === 'buy' ? '▲ BUY ' : '▼ SELL ') +
    '<b>' + t.vol + '</b> lots &nbsp; ' + p + '<br>' +
    '<span class="m">open</span>&nbsp;&nbsp; ' + t.open + ' @ ' + t.open_price + '<br>' +
    (t.close ? '<span class="m">close&nbsp;</span> ' + t.close + ' @ ' + t.close_price : '<span class="m">close&nbsp;</span> —');
  tip.style.display = 'block'; move(ev);
  document.querySelectorAll('.link[data-i="' + i + '"]').forEach(l => l.classList.add('hot'));
}}
function move(ev) {{
  const r = wrap.getBoundingClientRect();
  let x = ev.clientX - r.left + 14, y = ev.clientY - r.top + 12;
  if (x + tip.offsetWidth > r.width - 8) x = ev.clientX - r.left - tip.offsetWidth - 14;
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}}
function hide(i) {{
  tip.style.display = 'none';
  if (i !== undefined)
    document.querySelectorAll('.link[data-i="' + i + '"]').forEach(l => l.classList.remove('hot'));
}}
document.querySelectorAll('#chart .pt').forEach(el => {{
  el.addEventListener('mouseenter', ev => show(+el.dataset.i, ev));
  el.addEventListener('mousemove', move);
  el.addEventListener('mouseleave', () => hide(+el.dataset.i));
}});
document.querySelectorAll('#chart .link').forEach(el => {{
  el.addEventListener('mouseenter', ev => show(+el.dataset.i, ev));
  el.addEventListener('mousemove', move);
  el.addEventListener('mouseleave', () => hide(+el.dataset.i));
}});
</script>
</body></html>"""


# ---------------------------------------------------------------------------
# Aggregate index
# ---------------------------------------------------------------------------


def index_report(results_root: Path) -> Path | None:
    versions = sorted(p for p in results_root.iterdir() if p.is_dir()) if results_root.is_dir() else []
    rows: list[dict] = []
    for vdir in versions:
        for pdir in sorted(p for p in vdir.iterdir() if p.is_dir()):
            for sdir in sorted(p for p in pdir.iterdir() if p.is_dir()):
                s = _read_json(sdir / "summary.json")
                m = _read_json(sdir / "meta.json")
                if not s:
                    continue
                has_report = (sdir / "report.html").is_file()
                rel = f"{vdir.name}/{pdir.name}/{sdir.name}"
                rows.append({"v": vdir.name, "p": pdir.name, "s": sdir.name,
                             "from": m.get("date_from", ""), "to": m.get("date_to", ""),
                             **s, "link": (f"{rel}/report.html" if has_report else None),
                             "rel": rel})
    if not rows:
        return None

    def cell(v):
        if v is None:
            return "–"
        if isinstance(v, (int, float)):
            return f"{v:,.2f}"
        return html.escape(str(v))

    body = []
    cur_v = None
    for r in sorted(rows, key=lambda r: (r["v"], r["p"], r["s"])):
        if r["v"] != cur_v:
            cur_v = r["v"]
            body.append(f'<tr class="vh"><td colspan="11">{html.escape(cur_v)}</td></tr>')
        net_cls = "g" if (r.get("net_profit") or 0) >= 0 else "r"
        score = cell(r.get("score")) if r.get("score") is not None else '<span class="dq">DQ</span>'
        href = f'<a class="job" href="{html.escape(r["link"])}">report ↗</a>' if r["link"] else ""
        body.append(
            "<tr>"
            f'<td>{html.escape(r["p"])}</td>'
            f'<td class="m">{html.escape(r["p"])}: {html.escape(r["from"])} → {html.escape(r["to"])}</td>'
            f'<td>{html.escape(r["s"])}</td>'
            f'<td>{cell(r.get("trades"))}</td>'
            f'<td>{cell(r.get("profit_factor"))}</td>'
            f'<td class="{net_cls}">{cell(r.get("net_profit"))}</td>'
            f'<td>{cell(r.get("max_drawdown"))}</td>'
            f'<td>{cell(r.get("max_drawdown_pct"))}%</td>'
            f'<td>{cell(r.get("profitable_trades_pct"))}%</td>'
            f'<td>{score}</td>'
            f'<td>{href}</td>'
            "</tr>")
    ok_jobs = sum(1 for r in rows if r.get("net_profit") is not None)
    pos = sum(1 for r in rows if (r.get("net_profit") or 0) > 0)
    versions_n = len(versions)
    return _write_index(results_root, versions_n, len(rows), ok_jobs, pos, "".join(body))


def _write_index(root: Path, versions_n: int, jobs: int, ok_jobs: int, pos: int, body: str) -> Path:
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Backtest index</title>
<style>
 body {{ margin:0; padding:28px 36px 60px; background:#0d1220; color:#dfe6f3;
       font:15px/1.45 -apple-system,'Segoe UI',Roboto,sans-serif; }}
 h1 {{ font-size:20px; margin:0 0 4px; }} .sub {{ color:#8b96ad; margin-bottom:18px; }}
 table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
 th,td {{ padding:7px 10px; border-bottom:1px solid #232d47; text-align:right; }}
 th:first-child,td:first-child,td:nth-child(2),th:nth-child(2) {{ text-align:left; }}
 th {{ color:#8b96ad; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
 tr.vh td {{ background:#141b2e; color:#9fb0cc; font-weight:600; text-align:left; }}
 .g {{ color:#2ecc71; }} .r {{ color:#ff5c5c; }} .m {{ color:#8b96ad; font-size:13px; }}
 .dq {{ color:#5d6880; }} a.job {{ color:#4da3ff; text-decoration:none; }} a.job:hover {{ text-decoration:underline; }}
 .cards {{ display:flex; gap:12px; margin-bottom:18px; }}
 .card {{ background:#141b2e; border:1px solid #232d47; border-radius:10px; padding:10px 16px; }}
 .card .k {{ color:#8b96ad; font-size:12px; }} .card .v {{ font-size:18px; margin-top:2px; }}
</style></head><body>
<h1>Backtest index</h1><div class="sub">results/ · grouped by expert version · link each job to its full report</div>
<div class="cards">
 <div class="card"><div class="k">versions</div><div class="v">{versions_n}</div></div>
 <div class="card"><div class="k">jobs</div><div class="v">{jobs}</div></div>
 <div class="card"><div class="k">jobs OK</div><div class="v">{ok_jobs}</div></div>
 <div class="card"><div class="k">net-positive jobs</div><div class="v">{pos}</div></div>
</div>
<table><thead><tr>
 <th>period</th><th>window</th><th>preset</th><th>trades</th><th>PF</th>
 <th>net</th><th>maxDD</th><th>DD %</th><th>win %</th><th>score</th><th>report</th>
 </tr></thead><tbody>
 {body}
 </tbody></table>
<div class="sub" style="margin-top:14px">generated {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</div>
</body></html>"""
    out = root / "index.html"
    out.write_text(doc)
    return out
