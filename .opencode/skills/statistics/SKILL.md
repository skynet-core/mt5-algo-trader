---
name: Statistics
description: Interpreting backtest evidence: profit factor, expectancy, t-statistics, drawdown, multi-window consistency, overfitting. Load when judging whether results are real signal or noise.
---

# Statistics (backtest evidence)

## Core metrics and their meaning
- **Profit factor (PF)** = gross win / gross loss. PF 1.0 = break-even. 1.0–1.3 is weak/noise-adjacent; ≥1.5 is meaningful; ≥2 is strong.
- **Expectancy** `E = p·W − (1−p)·L` where p = win%, W/L = average win/loss. A system with E ≤ 0 cannot be profitable at any size.
- **Maximal drawdown** (absolute and % of equity) — the worst realized loss from peak. Judge PF *with* drawdown, never alone.
- **t-statistic of the trade series**: `t = mean(pnl) / (std(pnl)/√n)`. Rule of thumb: `|t| < ~2` with `n < 300` trades → the edge is statistically indistinguishable from zero. Two windows with PF 0.85 and 0.96 at ~500 trades may be the *same* system.

## Multi-window protocol (this repo)
- Each matrix window is an **independent sample** of market regimes. A claim that "version X is better" needs:
  1. X beats baseline on **most** windows (≥ half), not one heroic window; and
  2. the per-window deltas are plausibly larger than their noise (compare PF/DD with t-stat logic above).
- One big win + several small losses = red flag (lottery tail, likely overfit to that window).
- **Never select a version by its single best window** — that is multiple-comparisons selection bias. Use the window-mean score, not the max.

## Common statistical traps to name explicitly
- Win% up + expectancy down = worse system (e.g., tighter TP).
- PF up but maxDD up more than proportional = risk taken, not edge found.
- Results that only work on one timeframe (M1 yes / M15 no) = microstructure/fee artifact, not alpha.
- Changing inputs and re-testing until one window flips positive = overfitting loop; always require cross-window consistency.

## Decision rule to state in any review
A change is an **improvement** iff: expectancy > 0 consistently, and (PF↑ or maxDD↓ or trades↓ at same expectancy) across the majority of independent windows, with per-window sample sizes adequate to see it.
