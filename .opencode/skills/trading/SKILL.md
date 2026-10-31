---
name: Trading
description: Trading-domain knowledge for the AlgoSamurai EA (pending buy/sell-stop momentum entries, trailing SL, session filter, risk-based lot sizing). Load when proposing, implementing, or validating changes to the EA or judging whether a change fits the strategy type.
---

# Trading (AlgoSamurai strategy context)

## What the EA actually is
A **momentum breakout / stop-order EA** on EURUSD:
- Enters with **pending buy-stop / sell-stop** orders placed `InpStep` points (min broker stops level) away from the bid/ask, one per side.
- Each pending carries SL (`InpStopLoss = 150 pts`) and TP (`InpTakeProfit = 1250 pts`) → nominal **RR ≈ 1 : 8.3**.
- While open, the SL is **trailed** in flat steps (200 pts when in profit, 50 pts at a loss, keeping a gap from price). TP stays fixed. So the effective exit is usually the trailing SL, not the TP.
- **Session filter** (4:00–22:00 server time), **one position at a time** (pendings cancelled while a position is open), pendings re-anchored every ≥5 s while price moves.
- **Lot sizing**: risk-based — `lots = (equity · RiskPercent%) / loss-per-lot(entry→SL)`, using `OrderCalcProfit`; skips the trade if the lot falls below the broker minimum.
- The EA prints failures; every order/modify/delete is checked against broker retcodes.

## Hard constraint (never propose anything that breaks this)
**$1 000 deposit, max 1:30 leverage** → max margin ≈ notional $30 000 ≈ **0.28 standard lots EURUSD**.
With `RiskPercent = 1.0`: loss-per-lot for a 150-pt SL ≈ $150 → **standard lot ≈ 0.066 lots**. Comfortable. But:
- If a change lowers SL distance to <~90 pts, required lots grow; check against 0.28 lots.
- If a change raises RiskPercent above ~4–5%, lots approach/exceed the 0.28-lot ceiling at 150-pt SL → margin rejection under 1:30. **Keep per-trade $ risk between ~0.5% and ~3%** at this account size unless the user says otherwise.

## What "better" means for THIS strategy
- **Fewer bad trades** (entry filter that keeps only momentum continuing in the intended direction) beats adding exits.
- **Trailing that locks more profit** (wider gap once in profit, tighter when flat) is the highest-leverage knob: most losses in the backtests come from winning trades that round-trip, not from SL hits.
- **Symmetric treatment of buy vs sell side**: the EA has no bias; do not add one without regime evidence.
- **Cost realism**: at 150-pt SL with 0.06–0.2 lots, spread (typ. 5–12 pts on ICMarketsEU demo) is ~5–8% of the SL distance. Prefer changes that reduce trade frequency with higher-quality entries over higher frequency.
- **Session choice**: 4–22 covers London+NYSE. Narrowing to London open (7–12) or NYSE (13–18) is a legitimate filter to test — it is a *parameter* of the filter, not a new strategy.

## Change classes ranked (allowed → dangerous)
1. **Parameters**: step/SL/TP/step-size/risks/session hours — pure tuning. Safe.
2. **Exit hardening**: better trailing cadence, profit-lock threshold, breakeven move. Safe, keeps core.
3. **Entry filter refinement**: require bar-range or momentum condition before placing stops. Safe-ish, must stay inside the existing "place a stop order" flow.
4. **New exit type** (e.g. time-based close at session end): acceptable as an *additional* guard on the existing position, not a replacement for SL/TP/trailing.
5. **REPLACING the entry model** (market orders, reversal logic, martingale, grid, hedging, multiple symbols): **rejected by definition** — the core algorithm must stay a pending-stop momentum EA.

## What to watch in results
- Win% low + big winners = expected for a trailing TP-RR system; don't "fix" by lowering TP.
- Many small losers = entries being stopped out flat; entry filter is the answer, not bigger lots.
- PF up only on one timeframe or one window = microstructure fit, not an edge.
- Net profit up but on many fewer trades: verify expected value per trade rose, not just variance.
- If a version has >0 on ≥half of windows but PF < 1 on the rest, it improved the edge's *variance profile*, not its *mean*. Be honest about which.

## Sizing reality check to do for every proposed change
- Compute `loss_per_lot` for the new SL distance (from the EA's `OrderCalcProfit` logic): for 1.0 lot EURUSD, ~$1 / 10 pts ≈ **$150 per 150 pts**.
- Lots = (1000 · r%) / $150. At r=1% → 0.067; at r=3% → 0.20; at r=5% → 0.33 (exceeds 0.28 margin ceiling → rejected).
- If the proposal changes RiskPercent or SL distance, show this math in the review.
