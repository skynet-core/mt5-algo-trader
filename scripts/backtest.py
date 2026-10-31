"""Run one backtest job on one container.

Usage:
    uv run scripts/backtest.py --container mt5-2 \
        --expert experts/AlgoSamurai/1.10.mq5 --name AlgoSamurai-1.10 \
        --matrix matrices/m1.json --preset tick-m15 --period m1-12M-a \
        [--out results] [--timeout 3600]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import mt5lib

ROOT = Path(__file__).resolve().parent.parent


def timeout_for(period: dict) -> int:
    months = mt5lib.period_span(period) / (30.4 * 86400)
    return max(3600, int(months * 600))  # 12M -> 3600s, 24M -> 7200s


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True, help="e.g. mt5-2")
    parser.add_argument("--expert", required=True, type=Path)
    parser.add_argument("--name", required=True, help="compiled expert name, e.g. AlgoSamurai-v1.10")
    parser.add_argument("--preset", required=True, help="preset id from the matrix")
    parser.add_argument("--period", required=True, help="period id from the matrix")
    parser.add_argument("--matrix", type=Path, default=ROOT / "matrices",
                        help="one matrix JSON file containing this preset/period")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    parser.add_argument("--timeout", type=int, default=0)
    args = parser.parse_args()

    matrix = json.loads(args.matrix.read_text())
    matrix["id"] = args.matrix.stem
    preset = next((p for p in matrix["presets"] if p["id"] == args.preset), None)
    period = next((p for p in matrix["periods"] if p["id"] == args.period), None)
    if preset is None or period is None:
        print(f"unknown preset/period in {args.matrix}", file=sys.stderr)
        return 2

    result = mt5lib.run_backtest(
        container=args.container,
        expert_source=args.expert,
        expert_name=args.name,
        version=args.name,
        symbol=matrix["symbol"],
        matrix_id=matrix["id"],
        preset=preset,
        period=period,
        output_root=args.out,
        timeout=args.timeout or timeout_for(period),
    )
    if result.ok:
        print(f"OK  {args.container}: {args.preset}/{args.period} "
              f"trades={result.summary['trades']} PF={result.summary['profit_factor']:.2f} "
              f"net={result.summary['net_profit']:,.2f} score={result.score}")
        return 0
    print(f"FAIL {args.container}: {args.preset}/{args.period}: {result.error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
