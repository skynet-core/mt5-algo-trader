---
name: Math
description: Quantitative math for trading system evaluation: lot/margin math, expectancy, breakeven, compounding. Load when reasoning about position sizing, risk, or whether a backtest can be profitable.
---

# Math (trading calculations)

## Unit math
- EURUSD on a 5-digit broker: `point = 0.00001`. `1 pip = 10 points`.
- Report "points" in an EA are in `point` units; convert: `points × point = price distance`.
- One standard lot (100,000 units) ≈ `100,000 × point × 10` USD per pip at ~1.10 → ~$10 / pip.

## Position sizing (the constraint math)
- Margin required = `notional / leverage`. With **leverage 1:30**:
  max notional = `1000 USD × 30 = $30,000`.
  → **max ≈ 0.28 standard lots of EURUSD** (100k notional/lot at ~1.10).
  Any EA that sizes above ~0.25 lots at this account **will be margin-rejected or stopped out** — verify every lot formula against this ceiling.
- Risk-based lot: `lots = (equity × risk%) / (stopDistancePoints × pointValuePerLot)`,
  where `pointValuePerLot = 100000 × point × pipUnits`. Round down to broker lot step (0.01).
- Breakeven win rate = `1 / (1 + RR)` where `RR = avgWin / avgLoss` (in $). A 3:1 RR needs only ~25% wins; a 1:1 RR needs >50%.

## Expectancy & growth
- `E[trade] = p·W − (1−p)·L` (W/L = average win/loss in $).
- `n` trades → expected net ≈ `n × E[trade]`, variance scales with `n`.
- Compounding at risk r% per trade: equity grows by `(1+r/100)^n` — small per-trade r matters more than frequency.

## Common errors to catch
- Mixing pips and points (factor 10) in lot/SL math.
- Forgetting that SL in points on the *entry* price ≠ distance in account $ without the point value.
- Assuming leverage scales P&L (it only scales margin; P&L scales with lots).
- Dividing by zero distance (SL=0 → unlimited risk).
