"""Run the full epoch (all matrices: periods x presets) across the 6
containers in parallel and aggregate results into results/TABLE.md + table.csv.

Each container worker runs its jobs one at a time (MT5 only allows one
terminal owner per container); jobs are dispatched round-robin.

Usage:
    uv run scripts/fleet.py --expert experts/AlgoSamurai/1.10.mq5 --name AlgoSamurai-1.10 \
        [--matrix matrices] [--out results]
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import mt5lib
from backtest import timeout_for

ROOT = Path(__file__).resolve().parent.parent


def worker(
    i: int,
    jobs: queue.Queue,
    args: argparse.Namespace,
    outcomes: list[dict],
    lock: threading.Lock,
) -> None:
    name = mt5lib.container_name(i)
    compiled = False
    while True:
        job = jobs.get()
        if job is None:
            jobs.task_done()
            return
        matrix, period, preset = job
        print(f"[{name}] start {period['id']} / {preset['id']} ({matrix['id']})", flush=True)
        started = time.monotonic()
        try:
            if not compiled:
                mt5lib.compile_expert(name, args.expert, args.name)
                compiled = True
            result = mt5lib.run_backtest(
                container=name,
                expert_source=args.expert,
                expert_name=args.name,
                version=args.name,
                symbol=matrix["symbol"],
                matrix_id=matrix["id"],
                preset=preset,
                period=period,
                output_root=args.out,
                timeout=timeout_for(period),
            )
        except Exception as exc:  # noqa: BLE001
            result = mt5lib.JobResult(False, args.name, period["id"], preset["id"], name,
                                      error=f"worker: {exc}")
        with lock:
            outcomes.append(result)
        elapsed = int(time.monotonic() - started)
        if result.ok:
            print(f"[{name}] OK   {period['id']}/{preset['id']} in {elapsed}s — "
                  f"trades={result.summary['trades']} PF={result.summary['profit_factor']:.2f} "
                  f"net={result.summary['net_profit']:,.2f}", flush=True)
        else:
            print(f"[{name}] FAIL {period['id']}/{preset['id']} in {elapsed}s — {result.error}", flush=True)
        jobs.task_done()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expert", required=True, type=Path)
    parser.add_argument("--name", required=True, help="version tag, e.g. AlgoSamurai-v1.10")
    parser.add_argument("--matrix", type=Path, default=ROOT / "matrices",
                        help="directory of matrix JSON files, or a single matrix file")
    parser.add_argument("--out", type=Path, default=ROOT / "results")
    parser.add_argument("--workers", type=int, default=mt5lib.CONTAINER_COUNT)
    args = parser.parse_args()

    if not args.expert.exists():
        print(f"FATAL: expert not found: {args.expert}", file=sys.stderr)
        return 2
    matrices = mt5lib.load_matrices(args.matrix)
    job_list = [
        (m, period, preset)
        for m in matrices
        for period in m["periods"]
        for preset in m["presets"]
    ]
    print(f"matrices: {[m['id'] for m in matrices]} -> "
          f"{len(job_list)} jobs, {args.workers} workers", flush=True)

    jobs: queue.Queue = queue.Queue()
    for job in job_list:
        jobs.put(job)
    for _ in range(args.workers):
        jobs.put(None)

    outcomes: list[mt5lib.JobResult] = []
    lock = threading.Lock()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    threads = [
        threading.Thread(target=worker, args=(i, jobs, args, outcomes, lock),
                         name=f"worker-mt5-{i}")
        for i in range(1, args.workers + 1)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    finished = datetime.now(UTC).isoformat(timespec="seconds")
    ok = sum(1 for r in outcomes if r.ok)
    print(f"\nfleet done: {ok}/{len(job_list)} jobs OK", flush=True)

    rows = mt5lib.load_rows_fresh(args.out)
    table = mt5lib.write_tables(args.out, rows)
    best = mt5lib.best_row([r for r in rows if r.get("version") == args.name])
    mt5lib.update_versions(args.out, {
        "version": args.name,
        "source": str(args.expert),
        "sha256": mt5lib.sha256_of(args.expert),
        "matrices": [m["id"] for m in matrices],
        "git_head": mt5lib.git_head(args.expert.parent),
        "started": started,
        "finished": finished,
        "jobs_total": len(job_list),
        "jobs_ok": ok,
        "jobs_failed": len(job_list) - ok,
        "best": best,
    })
    print(f"table: {table}")
    return 0 if ok == len(job_list) else 3


if __name__ == "__main__":
    raise SystemExit(main())
