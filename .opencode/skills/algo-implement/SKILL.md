---
name: Algo Implement
description: Implementer role for the AlgoSamurai improvement loop. Applies an approved, reviewer-ranked change to a NEW version file experts/AlgoSamurai/<v>.mq5 while preserving the core algorithm (pending buy/sell-stop entries, trailing SL, session filter, risk sizing). Load for the implement step between the reviewer and validator gates.
---

# AlgoSamurai — implementer protocol

Load the `math` and `trading` skills first. You implement **exactly** the ranked, reviewer-approved changes — no extras, no "while I'm at it".

## Hard rules
1. **New file only.** Copy the previous version `experts/AlgoSamurai/<prev>.mq5` to `experts/AlgoSamurai/<next>.mq5` (`next = highest + 0.10`, e.g. 1.10 → 1.20, 1.29 → 1.30). Bump `#property version` to match the filename. Never modify an existing version file.
2. **Core preserved.** The entry model (pending buy-stop/sell-stop), trailing SL logic, session filter and risk-based lot sizing must remain structurally identical. Your diff should be readable as tuning/hardening of EXISTING branches, not new control flow.
3. **1 : 30 / 1000 USD constraint.** If you touch SL distance, TP, RiskPercent or Add-lot logic, verify in comments that `riskMoney / lossPerLot` stays below ~0.28 lots at 1:30 — see math skill for the check. If it cannot, adjust the constant, not the constraint.
4. **MQL5 hygiene.** Every `trade.*` call already goes through `CheckTradeResult`; keep it. No new globals unless required for the change. No new includes. No prints beyond the existing `LotWarning`/`Print` style.

## Process
1. Read the reviewer's ranked list. Apply change #1, #2, … in that order, but **stop** if a proposed change conflicts with the core-preserve rule — flag it instead of forcing.
2. After writing, run a **self-review pass** on the diff:
   - lot math at the new SL for risk 1% / 3% / 5% — fits ceiling?
   - any new `points → price` conversion: uses `_Point` and `NormalizeDouble(_Symbol)`?
   - any new comparison against `stopsLevelPts`/`freezeLevelPts`: respects broker distance?
   - buy-side and sell-side branches both touched symmetrically where required?
   - did the change introduce any state that must reset between test runs (globals only, no files)?
3. Return a **change manifest**: for each applied change, <function(s) touched, what changed, why, the specific math that makes the constraint safe>.

## When to refuse
- A proposed change requires a new input group beyond tuning (e.g. multi-timeframe).
- A proposed change changes order types beyond pending buy/sell-stop.
- A proposed change alters `InpMagicNumber` sizing or disables the `OnePositionAtATime` guard.
Refusal output: **REJECT — reason**. Do not implement it.
