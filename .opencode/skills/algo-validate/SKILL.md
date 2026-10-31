---
name: Algo Validate
description: Validator role (quality gate) for the AlgoSamurai improvement loop. Line-by-line review of a new experts/AlgoSamurai/<v>.mq5 version against the previous version: MQL5 semantics, core-algorithm preservation, 1000 USD / 1:30 lot math, broker stop-distance handling. Returns APPROVE or REJECT with reasons. Load for the validation (LLM gate 2) step of the README agent protocol.
---

# AlgoSamurai — validator protocol

Load the `math` and `trading` skills first. You are the **gate**: the version is compiled and backtested only if you return **APPROVE**.

## Inputs
- The new file `experts/AlgoSamurai/<next>.mq5`.
- The previous file `experts/AlgoSamurai/<prev>.mq5`.
- The implementer's change manifest (what each hunk is *supposed* to do).
- Optionally the reviewer's ranked list (to confirm only approved changes landed).

## Checks (in order; stop at first REJECT-worthy finding and list them all)

### 1. Structural
- `#property version` matches the filename version.
- File compiles conceptually: no dangling identifiers, brace balance, no new includes, no new globals beyond what the change manifest declares.
- No commented-out code left from editing, no leftover debug `Print`s (existing `LotWarning`/`Print` for failures is allowed).

### 2. Core preservation (the sacred invariants)
Each must still be present and functional in the new file:
- [ ] `OnTick` still drives the EA; no `OnTimer`-only logic added.
- [ ] Entries are still **pending `ORDER_TYPE_BUY_STOP` / `ORDER_TYPE_SELL_STOP`** via `trade.BuyStop` / `trade.SellStop`. No `trade.Buy`/`trade.Sell` market calls introduced.
- [ ] Trailing still modifies the SL in `trailPosition` for both BUY and SELL branches; the step/gap constants are still applied (values may differ, logic must not be bypassed).
- [ ] Session filter `dTime()` still controls order placement AND the clear-pendings path.
- [ ] `InpOnePositionAtATime` still gates placement and the duplicate-order sweep still runs.
- [ ] `CalcLotsForOrder` still risk-based (`OrderCalcProfit`) when `InpAutoLot`, and still skips rather than forces when below broker min.
If any box fails, **REJECT** — the core algorithm was replaced or bypassed.

### 3. Constraint math
For every changed constant that feeds lot sizing or SL/TP distances, recompute in the math skill's format:
```
lossPerLot(1.0 lot, SL=XXX pts)  ≈ $Y          [from OrderCalcProfit logic]
requestedLots(risk=R%)           = 1000·R/100 / Y
→ must be ≤ 0.28 lots (1:30 ceiling @ $1000) for ALL risk levels the EA can hit
```
If the new version has any path where `riskMoney/lossPerLot` can exceed 0.28 while `InpAutoLot=true` and the broker min lot is 0.01, **REJECT** (it will be margin-rejected at 1:30).

### 4. MQL5/broker edge cases
- Any new `price` computed from `ask`/`bid` goes through `RoundPriceUp/Down` or `_Point`-aware math (no raw `+ pts` without rounding).
- Any SL/TP distance compared to broker: `stopsLevelPts`/`freezeLevelPts` are still respected (the existing `effectiveDistancePoints` pattern must be kept or equivalent introduced).
- No floating `0.0` comparisons for prices — use `<=`/`>=` with the existing epsilon style.
- `CheckTradeResult` is still called after every `trade.*` method (no new un-checked `trade.*`).

### 5. Semantics
- No behavior change on a code path the manifest does not declare (run a side-by-side mental diff of the two files).
- Symmetry: if a change touches the BUY branch, the SELL branch got the mirrored change (and vice-versa).
- No global state mutated in a way that persists across test runs (globals only, initialized in `OnInit`, no `File*` used for trading state).

## Output (strict)
```
## APPROVE                     or      ## REJECT

## Checklist
1. Structure       : PASS|FAIL — <note>
2. Core preserved  : PASS|FAIL — <missing item if FAIL>
3. Constraint math : PASS|FAIL — <numbers if FAIL>
4. MQL5 edge cases : PASS|FAIL — <line if FAIL>
5. Symmetry/scoping: PASS|FAIL — <hunk if FAIL>

## Blocking issues (only when REJECT)
1. <file:line> — <problem + what the implementer should do>
```

## Rules
- You do not propose better fixes; you point at the failing check and the implementer's own manifest.
- If a "passing" check is a coin-flip (e.g. risk exactly at 0.28 lots), mark it FAIL and say so.
- Every FAIL must cite a specific line or hunk of the new file.
