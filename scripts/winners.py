"""Select the top-N expert versions and put each into experts/winners/
with its source, its results and a generated summary.

Ranking (documented in README.md): canonical time period = the longest
period for which the version has results; across its presets:
    disqualified: fewer than 100 trades or net loss
    score = 20 * (net profit / max drawdown) + 10 * min(profit factor, 5)

Usage:
    uv run scripts/winners.py [--pick 3] [--out results]
                              [--winners-dir experts/winners]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import mt5lib

ROOT = Path(__file__).resolve().parent.parent


def rank_versions(rows: list[dict], versions: dict) -> list[dict]:
    """Rank versions over their independent random windows.

    A version's score = mean of its best preset's score per period window
    (each window is an independent random sample drawn for that epoch).
    Versions without a single qualified window are unranked.
    """
    rows_by_version: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_version.setdefault(row.get("version", ""), []).append(row)

    entry = []
    for version, version_rows in rows_by_version.items():
        info = versions.get(version, {})
        by_period: dict[str, list[dict]] = {}
        for row in version_rows:
            by_period.setdefault(row.get("period", "?"), []).append(row)

        best_per_period = []
        for pid, prows in sorted(by_period.items()):
            scored = [r for r in prows if r.get("score") is not None]
            if scored:
                best = max(scored, key=lambda r: r["score"])
                best_per_period.append({
                    "period": pid,
                    "date_from": prows[0].get("date_from", ""),
                    "date_to": prows[0].get("date_to", ""),
                    "preset": best.get("preset", ""),
                    "net": best.get("net_profit", 0),
                    "pf": best.get("profit_factor", 0),
                    "dd": best.get("max_drawdown", 0),
                    "score": best["score"],
                })
        scored_rows = [r for r in version_rows if r.get("score") is not None]
        if not scored_rows:
            entry.append({"version": version, "ranked": False, "period_best": best_per_period,
                          "net": 0.0, "pf": 0.0, "dd": 0.0, "trades": 0, "score": None,
                          "info": info})
            continue
        score = round(sum(b["score"] for b in best_per_period) / len(best_per_period), 3)
        entry.append({
            "version": version, "ranked": True, "period_best": best_per_period,
            "net": round(sum(r.get("net_profit", 0) for r in scored_rows) / len(scored_rows), 2),
            "pf": round(sum(r.get("profit_factor", 0) for r in scored_rows) / len(scored_rows), 3),
            "dd": max(r.get("max_drawdown", 0) for r in scored_rows),
            "trades": min(r.get("trades", 0) for r in scored_rows),
            "score": score,
            "info": info,
        })
    entry.sort(key=lambda e: (e.get("score") is not None, e.get("score")), reverse=True)
    return entry


def write_winner(root: Path, version: dict, rows: list[dict]) -> Path:
    winners_dir = root / "experts" / "winners" / version["version"]
    winners_dir.mkdir(parents=True, exist_ok=True)
    info = version.get("info") or {}
    source = info.get("source")
    if source:
        candidates = [Path(source)]
        if not Path(source).is_absolute():
            candidates.append(root / Path(source))
        found = next((c for c in candidates if c.exists()), None)
        if found is None:
            raise FileNotFoundError(f"expert source for {version['version']} not found: {source}")
        shutil.copy2(found, winners_dir / "expert.mq5")

    version_rows = [r for r in rows if r.get("version") == version["version"]]
    lines = [
        f"# {version['version']} — winner summary",
        "",
        f"- Rank #{version.get('rank', '?')} — score {version['score']}",
        f"- Score = mean of best-preset scores over {len(version.get('period_best', []))} "
        f"independent windows (matrices: {', '.join(info.get('matrices', [])) or 'unknown'})",
        f"- Net P/L (mean over qualified runs): {version['net']:,.2f} USD",
        f"- Profit factor (mean): {version['pf']:.3f}",
        f"- Max drawdown (worst run): {version['dd']:,.2f} USD",
        f"- Minimum trades across runs: {version['trades']}",
        f"- Expert source: {source or 'unknown'}",
        f"- Expert sha256: {info.get('sha256', 'unknown')}",
        f"- Git revision: {info.get('git_head', info.get('git', 'unknown'))}",
        "",
        "## Best per window",
        "",
        "| window | preset | net P/L | PF | max DD | score |",
        "|---|---|---|---|---|---|",
    ]
    for best in sorted(version.get("period_best", []), key=lambda b: (b.get("date_from") or "")):
        lines.append("| " + " | ".join([
            f"{best['date_from']} → {best['date_to']}",
            str(best.get("preset", "")),
            f"{best.get('net', 0):,.2f}",
            f"{best.get('pf', 0):.2f}",
            f"{best.get('dd', 0):,.2f}",
            f"{best['score']:.1f}",
        ]) + " |")
    lines += ["", "## All results", "",
              "| period | preset | model | tf | trades | PF | net P/L | max DD | DD % | score |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    by_period: dict[str, list[dict]] = {}
    for row in version_rows:
        by_period.setdefault(row.get("period", ""), []).append(row)

    for period_id in sorted(by_period, key=lambda pid: (by_period[pid][0].get("date_from") or "", pid)):
        for row in sorted(by_period[period_id], key=lambda r: str(r.get("preset"))):
            dd_pct = row.get("max_drawdown_pct")
            lines.append("| " + " | ".join([
                period_id,
                str(row.get("preset", "")),
                str(row.get("model", "")),
                str(row.get("period_tf", "")),
                str(row.get("trades", "")),
                f"{row.get('profit_factor', 0):.2f}",
                f"{row.get('net_profit', 0):,.2f}",
                f"{row.get('max_drawdown', 0):,.2f}",
                f"{dd_pct:.1f}" if dd_pct is not None else "",
                f"{row['score']:.1f}" if row.get("score") is not None else "DQ",
            ]) + " |")
    (winners_dir / "summary.md").write_text("\n".join(lines) + "\n")
    table_csv = root / "results" / "table.csv"
    if table_csv.exists():
        shutil.copy2(table_csv, winners_dir / "table.csv")
    return winners_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pick", type=int, default=3)
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    args = parser.parse_args()

    results_root = args.out
    versions_file = results_root / "VERSIONS.json"
    rows = mt5lib.load_rows_fresh(results_root)
    if not rows:
        print("FATAL: no results found — run fleet.py first", file=sys.stderr)
        return 2
    versions = {}
    if versions_file.exists():
        versions = json.loads(versions_file.read_text())

    ranked = rank_versions(rows, versions)
    print("ranking (score = mean of best-preset scores per random window):")
    for rank, version in enumerate([v for v in ranked if v.get("score") is not None], start=1):
        version["rank"] = rank
        windows = len(version.get("period_best", []))
        print(f"  {rank}. {version['version']:<28} score={version['score']:<10} "
              f"windows={windows} net(mean)={version['net']:>12,.2f} PF={version['pf']:<6} trades(min)={version['trades']}")
    dqs = [v["version"] for v in ranked if v.get("score") is None]
    if dqs:
        print(f"  disqualified (no qualified presets): {', '.join(dqs)}")

    chosen = [v for v in ranked if v.get("score") is not None][: args.pick]
    if not chosen:
        print("FATAL: no qualified versions", file=sys.stderr)
        return 2
    for version in chosen:
        path = write_winner(ROOT, version, rows)
        print(f"winner: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
