"""Shared helpers for the 6-container MT5 backtest fleet.

Container layout (see docker-compose.yml), one per container mt5-<i>:

  package MCP (mt5-mcp 0.6.3):  http://127.0.0.1:{8000+i}/mcp
  native MT5 MCP proxy:         http://127.0.0.1:{23000+i}/mcp
  MetaEditor MCP proxy:         http://127.0.0.1:{24000+i}/mcp

The package MCP endpoint (`mt5_query` tool) serves health, terminal and
account queries and history probes. The Strategy Tester itself is driven
inside the container through MT5's documented `/config` interface — the
same mechanism as upstream `mt5-docker` tests (tests/validate.py) — with
live trading explicitly disabled in the merged tester config.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

MT5 = "/home/plasma/.wine/drive_c/MT5"
CONTAINER_COUNT = 6
WINE_ENV_KEYS = (
    "DISPLAY",
    "XAUTHORITY",
    "WAYLAND_DISPLAY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "WINEPREFIX",
    "WINEDEBUG",
    "WINEDLLOVERRIDES",
)

# ---------------------------------------------------------------------------
# docker / file helpers
# ---------------------------------------------------------------------------


def container_name(i: int) -> str:
    return f"mt5-{i}"


def mcp_url(i: int) -> str:
    return f"http://127.0.0.1:{8000 + i}/mcp"


def docker(*args: str, timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *args], capture_output=True, timeout=timeout)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).decode(errors="replace")[-500:]
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {detail}")
    return result


def read_text(path: Path) -> str:
    data = path.read_bytes()
    return data.decode(
        "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig",
        errors="replace",
    )


def win_python(container: str, code: str, timeout: int = 120, check: bool = True) -> subprocess.CompletedProcess:
    """Run Python code with Windows Python inside the Wine prefix as plasma."""
    return wine(container, r"C:\Python\python.exe", "-c", code, timeout=timeout, check=check)


def wine(
    container: str,
    *args: str,
    timeout: int = 600,
    check: bool = True,
) -> subprocess.CompletedProcess:
    """Run a Wine command as the plasma user with the session environment."""
    env_file = docker("exec", container, "cat", "/run/plasma/mt5-environment.json", check=False)
    exports: list[str] = []
    if env_file.returncode == 0:
        env = json.loads(env_file.stdout)
        exports = [f"{key}={env[key]}" for key in WINE_ENV_KEYS if key in env]
    return docker(
        "exec", "-u", "plasma", container, "env", *exports, "wine", *args,
        timeout=timeout, check=check,
    )


def close_terminal(container: str, timeout: int = 60) -> None:
    """Send WM_CLOSE to any running MT5 window and wait for it to exit."""
    code = """
import ctypes
from ctypes import wintypes
user = ctypes.windll.user32
kernel = ctypes.windll.kernel32
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
handles = []

@callback_type
def close(window, _):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(window, ctypes.byref(pid))
    handle = kernel.OpenProcess(0x101000, False, pid.value)
    if handle:
        kernel.WaitForSingleObject(handle, 0)  # ensure live
        user.PostMessageW(window, 0x0010, 0, 0)
        handles.append(handle)
    return True

user.EnumWindows(close, 0)
for handle in handles:
    kernel.WaitForSingleObject(handle, 30000)
    kernel.CloseHandle(handle)
"""
    try:
        win_python(container, code, timeout=timeout)
    except Exception:
        pass  # no terminal open; nothing to close


def supervisor_pid(container: str) -> str | None:
    state = docker(
        "exec", container, "cat", "/run/plasma/target-app.json", check=False
    )
    if state.returncode:
        return None
    try:
        return str(json.loads(state.stdout)["supervisor"])
    except (KeyError, ValueError):
        return None


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_head(repo: Path | None = None) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo) if repo else None,
        capture_output=True, check=False,
    )
    return result.stdout.decode().strip() if result.returncode == 0 else ""


# ---------------------------------------------------------------------------
# mt5-mcp (package MCP endpoint)
# ---------------------------------------------------------------------------


async def mcp_call(i: int, operation: str, **arguments: object) -> dict:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    timeout = timedelta(seconds=120)
    async with streamablehttp_client(mcp_url(i), timeout=timeout) as streams:
        async with ClientSession(
            streams[0], streams[1], read_timeout_seconds=timedelta(seconds=180)
        ) as session:
            await session.initialize()
            result = await session.call_tool("mt5_query", {"operation": operation, **arguments})

    if result.isError:
        text = "".join(part.text for part in result.content if part.type == "text")
        raise RuntimeError(f"mt5_query {operation} failed: {text[:400]}")
    texts = [part.text for part in result.content if part.type == "text"]
    try:
        return json.loads("\n".join(texts))
    except ValueError:
        return {"success": False, "data": None, "raw": ("\n".join(texts))[:400]}


def require_success(payload: dict) -> dict:
    if payload.get("success") is not True:
        raise RuntimeError(f"mt5_query failed: {str(payload)[:400]}")
    return payload["data"] if payload.get("data") is not None else {}


def read_env(path: Path) -> dict[str, str]:
    """Minimal dotenv reader for the hidden .env (KEY=VALUE, quotes stripped)."""
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        env[key] = value
    return env


# ---------------------------------------------------------------------------
# Expert compilation
# ---------------------------------------------------------------------------


def compile_expert(container: str, source: Path, name: str) -> Path:
    """Copy the expert into the container and compile it; raise on failure.

    Result: $MT5/MQL5/Experts/<name>.ex5  (tester: Expert=<name>.ex5)
    """
    base = f"{MT5}/MQL5/Experts/{name}"
    docker("exec", container, "mkdir", "-p", f"{MT5}/MQL5/Experts")
    docker("cp", str(source), f"{container}:{base}.mq5")
    docker("exec", container, "chown", "plasma:", f"{base}.mq5")
    docker("exec", container, "rm", "-f", f"{base}.ex5", f"{base}.log")
    # MetaEditor exit codes vary under Wine; fresh binary + log are authoritative.
    wine(
        container,
        r"C:\MT5\metaeditor64.exe",
        "/portable",
        f"/compile:C:\\MT5\\MQL5\\Experts\\{name}.mq5",
        "/log",
        check=False, timeout=300,
    )
    # MetaEditor exits rc=1 under Wine even on success — the log + .ex5 are
    # authoritative. Give the compiler a few seconds to flush the log (cold start).
    log_text = ""
    for _ in range(10):
        log = docker("exec", container, "cat", f"{base}.log", check=False).stdout
        # MetaEditor writes this log as UTF-16LE — decoding as UTF-8 inserts NULs
        # and hides the "0 errors" marker.
        log_text = log.decode("utf-16", errors="replace") if log.startswith(
            (b"\xff\xfe", b"\xfe\xff")) else log.decode("utf-8-sig", errors="replace")
        if re.search(r"\b\d+ errors?", log_text, re.IGNORECASE):
            break
        time.sleep(1)
    if not re.search(r"\b0 errors?[,;]", log_text, re.IGNORECASE):
        raise RuntimeError(f"{name}: compilation failed:\n{log_text[-800:]}")
    exists = docker("exec", container, "test", "-s", f"{base}.ex5", check=False)
    if exists.returncode:
        raise RuntimeError(f"{name}: no .ex5 produced:\n{log_text[-800:]}")
    return Path(f"{base}.ex5")


# ---------------------------------------------------------------------------
# Tester report parsing
# ---------------------------------------------------------------------------


class ReportParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row:
            self.rows.append(self.row)


def report_summary(html: str) -> dict[str, str]:
    parser = ReportParser()
    parser.feed(html)
    return {
        row[index].rstrip(":"): row[index + 1]
        for row in parser.rows
        for index in range(len(row) - 1)
        if row[index].endswith(":") and row[index + 1]
    }


def pick(summary: dict[str, str], *needles: str) -> str | None:
    lowered = {k.lower(): v for k, v in summary.items()}
    for key, value in lowered.items():
        for needle in needles:
            if needle in key:
                return value
    return None


def to_float(value: str | None) -> float:
    if value is None:
        return 0.0
    cleaned = str(value).replace(",", "").replace(" ", "")
    match = re.search(r"[-+]?[\d.]+", cleaned)
    return float(match.group()) if match else 0.0


def summarize(summary: dict[str, str]) -> dict:
    """Normalize an MT5 tester report into comparable numbers."""
    # Prefer MAXIMAL drawdown (the standard metric); "absolute" is just the
    # drawdown at the final point.
    dd_raw = (
        pick(summary, "balance drawdown maximal")
        or pick(summary, "equity drawdown maximal")
        or pick(summary, "drawdown") or "0"
    )
    dd_match = re.search(r"\(([\d.]+)\s*%\)", dd_raw)
    pt_match = re.search(r"([\d.]+)\s*%", pick(summary, "profit trades") or "")
    return {
        "trades": int(to_float(pick(summary, "total trades"))),
        "profit_factor": to_float(pick(summary, "profit factor")),
        "net_profit": to_float(pick(summary, "net profit")),
        "gross_profit": to_float(pick(summary, "gross profit")),
        "gross_loss": to_float(pick(summary, "gross loss")),
        "max_drawdown": to_float(dd_raw),
        "max_drawdown_pct": float(dd_match.group(1)) if dd_match else None,
        "profitable_trades_pct": float(pt_match.group(1)) if pt_match else 0.0,
        "bars": int(to_float(pick(summary, "bars"))),
    }


def score_row(row: dict) -> float | None:
    """Ranking score (higher = better). Disqualified: <100 trades or net loss.

    score = 20 * (net profit / max drawdown) + 10 * min(profit factor, 5)
    with no drawdown and a net profit: score = 1000 + the PF bonus.
    """
    if row["trades"] < 100 or row["net_profit"] <= 0:
        return None
    pf_bonus = min(row["profit_factor"], 5.0) * 10
    if row["max_drawdown"] > 0:
        return round(row["net_profit"] / row["max_drawdown"] * 20 + pf_bonus, 3)
    return round(1000.0 + pf_bonus, 3)


# ---------------------------------------------------------------------------
# One backtest job (tester run inside the container)
# ---------------------------------------------------------------------------


def find_trades_log(container: str) -> str | None:
    """Locate the EA's observation log inside the container.

    The tester agent runs the EA with its own data dir (Tester/Agent-*/MQL5/Files);
    fall back to the terminal's MQL5/Files. Returns the newest container path.
    """
    r = docker("exec", container, "sh", "-c",
               "find " + f"{MT5}/Tester {MT5}/MQL5/Files" +
               " -name AlgoSamurai_trades.csv -printf '%T@ %p\\n' 2>/dev/null | sort -rn | head -1",
               check=False)
    line = r.stdout.decode(errors="replace").split()
    if len(line) >= 2:
        return line[1]
    return None


def delete_trades_log(container: str) -> None:
    docker("exec", container, "sh", "-c",
           "find " + f"{MT5}/Tester {MT5}/MQL5/Files" +
           " -name AlgoSamurai_trades.csv -delete 2>/dev/null", check=False)


@dataclass
class JobResult:
    ok: bool
    version: str
    period_id: str
    preset_id: str
    container: str
    summary: dict | None = None
    score: float | None = field(default=None)
    error: str | None = None
    output_dir: Path | None = None


def run_backtest(
    *,
    container: str,
    expert_source: Path,
    expert_name: str,
    version: str,
    symbol: str,
    matrix_id: str | None,
    preset: dict,
    period: dict,
    output_root: Path,
    timeout: int = 3600,
) -> JobResult:
    """Compile (if needed) and run one Strategy Tester job; save all evidence."""
    output_dir = output_root / version / period["id"] / preset["id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    job = f"job-{int(time.time())}-{abs(hash((container, expert_name, preset['id'], period['id'], str(timeout)))) % 100000}"
    started = datetime.now(UTC).isoformat(timespec="seconds")
    meta = {
        "version": version,
        "container": container,
        "expert_name": expert_name,
        "expert_source": str(expert_source),
        "expert_sha256": sha256_of(expert_source),
        "git_head": git_head(expert_source.parent),
        "symbol": symbol,
        "matrix_id": matrix_id,
        "period_id": period["id"],
        "date_from": period["from"],
        "date_to": period["to"],
        "preset_id": preset["id"],
        "model": preset["model"],
        "period_tf": preset["period"],
        "deposit": preset["deposit"],
        "leverage": preset["leverage"],
        "started": started,
    }

    def fail(message: str) -> JobResult:
        (output_dir / "error.txt").write_text(message)
        meta["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
        (output_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        return JobResult(False, version, period["id"], preset["id"], container,
                         error=message, output_dir=output_dir)

    try:
        compile_expert(container, expert_source, expert_name)
    except Exception as exc:  # noqa: BLE001 - report per job
        return fail(f"compile: {exc}")

    ini_text = (
        "[Tester]\n"
        f"Expert={expert_name}.ex5\n"
        f"Symbol={symbol}\n"
        f"Period={preset['period']}\n"
        f"Model={preset['model']}\n"
        "Optimization=0\n"
        f"FromDate={period['from']}\n"
        f"ToDate={period['to']}\n"
        f"Report={job}.htm\n"
        "ReplaceReport=1\n"
        "ShutdownTerminal=1\n"
        "UseLocal=1\nUseRemote=0\nUseCloud=0\n"
        "Visual=0\n"
        f"Deposit={preset['deposit']}\n"
        "Currency=USD\n"
        f"Leverage={preset['leverage']}\n"
    )
    ini_local = output_dir / "tester.ini"
    ini_local.write_text(ini_text)
    try:
        docker("cp", str(ini_local), f"{container}:{MT5}/{job}.ini")
        # Merge credentials inside the container; never write the password to the host ini.
        # Also pin the expert to this test and disable live trading.
        merge = """
import configparser, os, pathlib, pwd, sys
root = pathlib.Path(sys.argv[1]); path = root / sys.argv[2]
config = configparser.ConfigParser(interpolation=None); config.optionxform = str
config.read(root / 'config.ini', encoding='utf-16'); config.read(path)
config['Experts'] = {'Enabled': '1', 'AllowLiveTrading': '0', 'AllowDllImport': '0'}
config['Tester']['Login'] = config['Common']['Login']
account = pwd.getpwnam('plasma')
with os.fdopen(os.open(path, os.O_WRONLY | os.O_TRUNC), 'w', encoding='utf-16') as stream:
    os.fchmod(stream.fileno(), 0o600); os.fchown(stream.fileno(), account.pw_uid, account.pw_gid)
    config.write(stream, space_around_delimiters=False)
"""
        docker("exec", container, "python3", "-c", merge, MT5, f"{job}.ini")
        supervisor = supervisor_pid(container)

        # Freeze the kiosk supervisor so it cannot launch a competing terminal
        # while the Strategy Tester owns the account/terminal processes.
        docker("exec", container, "systemctl", "stop", "mt5-mcp.service", check=False)
        if supervisor:
            docker("exec", container, "kill", "-STOP", supervisor, check=False)
        # Stale observation log from a previous job — the EA recreates it on OnInit.
        delete_trades_log(container)
    except Exception as exc:  # noqa: BLE001
        return fail(f"setup: {exc}")

    try:
        close_terminal(container)
        result = wine(
            container,
            r"C:\MT5\terminal64.exe",
            "/portable",
            f"/config:C:\\MT5\\{job}.ini",
            timeout=timeout, check=False,
        )
        (output_dir / "tester-process.log").write_bytes(result.stdout + result.stderr)
    except subprocess.TimeoutExpired as exc:
        return fail(f"tester timeout after {timeout}s")
    finally:
        close_terminal(container)
        if supervisor:
            docker("exec", container, "kill", "-CONT", supervisor, check=False)
        docker("exec", container, "systemctl", "start", "mt5-mcp.service", check=False)
        docker("exec", container, "rm", "-f", f"{MT5}/{job}.ini", check=False)

    report_local = output_dir / "report.htm"
    deadline = time.monotonic() + max(300, timeout)
    while docker(
        "cp", f"{container}:{MT5}/{job}.htm", str(report_local), check=False
    ).returncode:
        if time.monotonic() > deadline:
            # the launcher returns in ~2s; a stuck job leaves the detached tester agent running —
            # hard-kill it (polite WM_CLOSE is insufficient) so it cannot poison the next job.
            # shortcut: pkill -f pattern matches the job's launcher, not the kiosk terminal (config.ini).
            docker("exec", container, "pkill", "-9", "-f", "metatester64", check=False)
            docker("exec", container, "pkill", "-9", "-f", "config:C:.MT5.job-", check=False)
            return fail(f"no test report after {timeout}s (check tester-process.log)")
        time.sleep(2)

    summary_raw = report_summary(read_text(report_local))
    name_in_report = (summary_raw.get("Expert") or "").split("\\")[-1]
    if name_in_report != expert_name:
        return fail(f"report expert mismatch: {summary_raw.get('Expert')!r}")
    if int(to_float(pick(summary_raw, "bars"))) <= 0:
        return fail("report shows zero processed bars")

    row = summarize(summary_raw)
    row["score"] = score_row(row)
    meta["finished"] = datetime.now(UTC).isoformat(timespec="seconds")
    # Observation-only trade event log written by the EA (trading logic untouched).
    # It lives under the tester agent's data dir, not the terminal's.
    remote_log = find_trades_log(container)
    if remote_log:
        docker("cp", f"{container}:{remote_log}",
               str(output_dir / "trades.tsv"), check=False)
    (output_dir / "error.txt").unlink(missing_ok=True)  # clear stale failure marker
    (output_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    (output_dir / "summary.json").write_text(json.dumps(row, indent=2))
    (output_dir / "summary-raw.json").write_text(json.dumps(summary_raw, indent=2, default=str))
    try:
        import reportgen
        reportgen.job_report(output_dir)
    except Exception as exc:  # noqa: BLE001 - reporting must never fail a job
        print(f"  [reportgen] job report skipped: {exc}", flush=True)
    return JobResult(True, version, period["id"], preset["id"], container,
                     summary=row, score=row["score"], output_dir=output_dir)


# ---------------------------------------------------------------------------
# Aggregation (results/ -> TABLE.md + table.csv)
# ---------------------------------------------------------------------------

FIELD_ORDER = (
    "version", "period", "preset", "trades", "profit_factor", "net_profit",
    "max_drawdown", "max_drawdown_pct", "profitable_trades_pct", "bars", "score",
)


def load_rows_fresh(results_root: Path) -> list[dict]:
    rows: list[dict] = []
    for version_dir in sorted(p for p in results_root.iterdir() if p.is_dir()) if results_root.is_dir() else []:
        for period_dir in sorted(p for p in version_dir.iterdir() if p.is_dir()):
            for preset_dir in sorted(p for p in period_dir.iterdir() if p.is_dir()):
                summary_file = preset_dir / "summary.json"
                if not summary_file.exists():
                    continue
                row = json.loads(summary_file.read_text())
                meta: dict = {}
                meta_file = preset_dir / "meta.json"
                if meta_file.exists():
                    meta = json.loads(meta_file.read_text())
                row.update(
                    version=version_dir.name,
                    period=meta.get("period_id", period_dir.name),
                    preset=meta.get("preset_id", preset_dir.name),
                    model=meta.get("model", ""),
                    period_tf=meta.get("period_tf", ""),
                    date_from=meta.get("date_from", ""),
                    date_to=meta.get("date_to", ""),
                )
                row["score"] = score_row(row)
                rows.append(row)
    return rows


def period_span(period: dict) -> int:
    fmt = "%Y.%m.%d"
    start = datetime.strptime(period["from"], fmt)
    end = datetime.strptime(period["to"], fmt)
    return int((end - start).total_seconds())


def load_matrices(path: Path) -> list[dict]:
    """Load one matrix JSON (file) or all matrices in a directory.

    Each matrix: {"symbol": ..., "periods": [{"id","from","to"}], "presets": [...]}.
    Period ids must be unique across the matrices used in one epoch.
    """
    files = sorted(path.glob("*.json")) if path.is_dir() else [path]
    if not files:
        raise FileNotFoundError(f"no matrix files under {path}")
    matrices = []
    for file in files:
        matrix = json.loads(file.read_text())
        matrix["id"] = file.stem
        if not matrix.get("periods") or not matrix.get("presets"):
            raise ValueError(f"matrix {file} has no periods or presets")
        matrices.append(matrix)
    return matrices


def write_tables(results_root: Path, rows: list[dict]) -> Path:
    results_root.mkdir(parents=True, exist_ok=True)
    # table.csv
    csv = results_root / "table.csv"
    fields = [f for f in FIELD_ORDER if any(f in r for r in rows)]
    lines = [",".join(fields)]
    for row in sorted(rows, key=lambda r: (r.get("version", ""), r.get("period", ""), r.get("preset", ""))):
        lines.append(",".join(str(row.get(f, "")) for f in fields))
    csv.write_text("\n".join(lines) + "\n")

    # TABLE.md grouped by expert_version/time_period
    lines: list[str] = ["# Backtest results", ""]
    by_version: dict[str, list[dict]] = {}
    for row in rows:
        by_version.setdefault(row.get("version", "?"), []).append(row)
    for version in sorted(by_version):
        version_rows = by_version[version]
        lines.append(f"## {version}")
        lines.append("")
        by_period: dict[str, list[dict]] = {}
        for row in version_rows:
            by_period.setdefault(row.get("period", "?"), []).append(row)

        def dates(pid: str) -> tuple:
            first = by_period[pid][0]
            return (first.get("date_from") or "9999", pid)

        for period_id in sorted(by_period, key=dates):
            period_rows = by_period[period_id]
            first = period_rows[0]
            if first.get("date_from") and first.get("date_to"):
                label = f"{period_id} ({first['date_from']} → {first['date_to']})"
            else:
                label = period_id
            lines.append(f"### {label}")
            lines.append("")
            header = ["preset", "model", "tf", "trades", "PF", "net P/L",
                      "max DD", "DD %", "win %", "score"]
            lines.append("| " + " | ".join(header) + " |")
            lines.append("|" + "---|" * len(header))
            for row in sorted(period_rows, key=lambda r: str(r.get("preset", ""))):
                dd_pct = row.get("max_drawdown_pct")
                lines.append("| " + " | ".join([
                    str(row.get("preset", "")),
                    str(row.get("model", "")),
                    str(row.get("period_tf", "")),
                    str(row.get("trades", "")),
                    f"{row.get('profit_factor', ''):.2f}",
                    f"{row.get('net_profit', ''):,.2f}",
                    f"{row.get('max_drawdown', ''):,.2f}",
                    f"{dd_pct:.1f}" if dd_pct is not None else "",
                    f"{row.get('profitable_trades_pct', ''):.1f}",
                    f"{row.get('score'):.1f}" if row.get("score") is not None else "DQ",
                ]) + " |")
            lines.append("")
    (results_root / "TABLE.md").write_text("\n".join(lines))
    try:
        import reportgen
        reportgen.index_report(results_root)
    except Exception as exc:  # noqa: BLE001 - reporting must never fail aggregation
        print(f"  [reportgen] index skipped: {exc}", flush=True)
    return results_root / "TABLE.md"


def update_versions(results_root: Path, entry: dict) -> None:
    versions_file = results_root / "VERSIONS.json"
    versions: dict = {}
    if versions_file.exists():
        try:
            versions = json.loads(versions_file.read_text())
        except ValueError:
            versions = {}
    versions[entry["version"]] = entry
    versions_file.write_text(json.dumps(versions, indent=2) + "\n")


def best_row(rows_of: list[dict]) -> dict | None:
    scored = [r for r in rows_of if r.get("score") is not None]
    if not scored:
        return None
    return max(scored, key=lambda r: r["score"])
