"""Fleet health gate: all 6 containers up, MCP reachable, MT5 connected,
history probed. Exit 0 = ready to run the backtest matrix.

Usage:
    uv run scripts/wait.py [--timeout 2400]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import mt5lib

ROOT = Path(__file__).resolve().parent.parent


def datetime_ts(yst: str) -> int:
    return int(datetime.strptime(yst, "%Y.%m.%d").replace(tzinfo=UTC).timestamp())


async def probe_history(i: int, symbol: str, from_ts: int, to_ts: int, timeframe: str) -> int:
    """Return the number of bars available for a one-week window (bounds the payload)."""
    payload = await mt5lib.mcp_call(i, "copy_rates_range", symbol=symbol,
                                    parameters={"timeframe": timeframe,
                                                "date_from": from_ts, "date_to": to_ts})
    data = mt5lib.require_success(payload)
    bars = data if isinstance(data, list) else data.get("rates") or data.get("bars") or []
    return len(bars)


async def check_container(i: int, args: argparse.Namespace, env: dict, matrices: list) -> dict:
    name = mt5lib.container_name(i)
    result = {"container": name, "running": False, "mcp": False, "terminal": False,
              "account_ok": False, "history": {}, "errors": []}

    def stamp(message: str):
        result["errors"].append(message)

    # 1) container running?
    try:
        running = mt5lib.docker(
            "inspect", "--format", "{{.State.Running}}", name, check=False
        ).stdout.decode().strip()
        result["running"] = running == "true"
        if not result["running"]:
            stamp("container not running")
            return result
    except Exception as exc:
        stamp(str(exc))
        return result

    # 2) MCP handshake + terminal + account
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(mt5lib.mcp_url(i), timeout=timedelta(seconds=120)) as streams:
            async with ClientSession(streams[0], streams[1],
                                     read_timeout_seconds=timedelta(seconds=180)) as session:
                init = await session.initialize()
                result["mcp"] = True
                tool_names = [t.name for t in (await session.list_tools()).tools]
                if "mt5_query" not in tool_names:
                    stamp("mt5_query tool missing")
                    return result

                async def query(operation, **extra):
                    call = await session.call_tool("mt5_query", {"operation": operation, **extra})
                    if call.isError:
                        raise RuntimeError("".join(p.text for p in call.content if p.type == "text")[:300])
                    texts = [p.text for p in call.content if p.type == "text"]
                    return mt5lib.require_success(json.loads("\n".join(texts)))

                terminal = await query("terminal_info")
                result["terminal"] = bool(terminal.get("connected"))
                if not result["terminal"]:
                    stamp("terminal not connected")
                    return result
                account = await query("account_info")
                want_login = env.get("MT5_ACCOUNT") or env.get("MT5_LOGIN")
                want_server = env.get("MT5_SERVER")
                if want_login and str(account.get("login")) != str(want_login):
                    stamp(f"account mismatch: {account.get('login')}")
                elif not want_login:
                    stamp("no MT5_ACCOUNT in .env to verify against")
                else:
                    result["account_ok"] = want_server is None or account.get("server") == want_server
                    if not result["account_ok"]:
                        stamp(f"server mismatch: {account.get('server')}")
    except Exception as exc:
        stamp(str(exc))
        return result

    # 3) history probe: one week at the earliest requested date, each timeframe used.
    all_periods = [p for m in matrices for p in m["periods"]]
    earliest = min(all_periods, key=lambda p: p["from"])["from"]
    from_ts = int(datetime_ts(earliest))
    to_ts = from_ts + 7 * 86400
    symbol = next(m["symbol"] for m in matrices)
    timeframes = sorted({p["period"] for m in matrices for p in m["presets"]})
    for timeframe in timeframes:
        try:
            bars = await probe_history(i, symbol, from_ts, to_ts, timeframe)
            result["history"][timeframe] = bars
            if bars < 100:
                stamp(f"insufficient {timeframe} history: {bars} bars")
        except Exception as exc:
            result["history"][timeframe] = 0
            stamp(f"{timeframe} probe failed: {str(exc)[:120]}")
    return result


async def run(args: argparse.Namespace) -> int:
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("FATAL: no hidden .env in project root (copy from mt5-docker/.env and fill in).", file=sys.stderr)
        return 2
    env = mt5lib.read_env(env_file)
    matrices_dir = ROOT / "matrices"
    matrices = mt5lib.load_matrices(matrices_dir) if matrices_dir.is_dir() else None
    if matrices is None:
        print("FATAL: no matrices/ directory or matrix.json", file=sys.stderr)
        return 2

    ids = list(range(1, mt5lib.CONTAINER_COUNT + 1))
    deadline = time.monotonic() + args.timeout
    statuses: list[dict] = []
    while len(statuses) < len(ids) and time.monotonic() < deadline:
        for i in ids:
            if i in [s["index"] for s in statuses]:
                continue
            status = await check_container(i, args, env, matrices)
            status["index"] = i
            ready = (
                status["mcp"] and status["terminal"] and status["account_ok"]
                and not status["errors"]
            )
            if ready:
                statuses.append(status)
                print(f"[ok]   {status['container']}: mcp+terminal, "
                      f"history {status['history']}", flush=True)
                statuses.sort(key=lambda s: s["index"])
                break  # restart the loop; another container may have come up
            message = "; ".join(status["errors"][:2]) or "still starting"
            print(f"[wait] {status['container']}: running={status['running']} "
                  f"mcp={status['mcp']} terminal={status['terminal']} — {message}", flush=True)
        if len(statuses) < len(ids):
            await asyncio.sleep(10)

    if len(statuses) < len(ids):
        missing = [mt5lib.container_name(i) for i in ids if i not in [s["index"] for s in statuses]]
        print(f"\nFAILED after {args.timeout}s — not ready: {', '.join(missing)}", file=sys.stderr)
        return 1
    print(f"\nREADY: {len(ids)}/{len(ids)} containers healthy — mt5-mcp reachable, "
          "terminals connected, history probed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=2400,
                        help="seconds to wait for the whole fleet (default 2400)")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
