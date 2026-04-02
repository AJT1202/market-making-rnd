---
title: "NVDA POC Results: Backtest & Telonex Evaluation"
created: 2026-03-31
tags:
  - poc
  - results
  - backtesting
  - telonex
  - nvda
  - market-making
---

# NVDA POC Results: Backtest & Telonex Evaluation

## Event Summary

**Event:** Will NVIDIA (NVDA) close above $X on March 30, 2026?
**NVDA Closing Price:** $165.06

| Strike | Resolution | Polymarket Mid (avg during market hours) |
|--------|-----------|------------------------------------------|
| $160 | YES | ~0.95 (deep ITM) |
| $165 | YES | ~0.71 (near ATM — most dynamic) |
| $170 | NO | ~0.24 (slightly OTM) |
| $175 | NO | ~0.015 (deep OTM) |
| $180 | NO | ~0.005 (very deep OTM) |

## Strategy Tested

**Probability-based quoting** ([[Core-Market-Making-Strategies#1. Probability-Based Quoting]])

| Parameter | Value |
|-----------|-------|
| Fair value model | Black-Scholes binary call |
| Implied volatility | 50% annualized |
| Half-spread | $0.02 |
| Min edge threshold | $0.03 |
| Max position per market | 50 shares |
| Order size | 10 shares |
| Market hours only | 9:30 AM - 4:00 PM ET |

## Headline Result: L2 vs Midpoint Fill Simulation

| Metric | L2 (Realistic) | Midpoint (Naive) | Overstatement |
|--------|---------------|------------------|---------------|
| **Total P&L** | **-$18.10** | **+$620.70** | **+$638.80** |
| Spread Capture | -$6.70 | +$603.10 | +$609.80 |
| Inventory P&L | -$11.40 | +$17.60 | +$29.00 |
| Total Fills | 188 | 3,096 | **16.5x** |
| Fill Rate | 0.93% | 13.9% | **15x** |
| Buy Fills | 89 | 1,548 | 17.4x |
| Sell Fills | 99 | 1,548 | 15.6x |

> [!important] Key Finding
> The midpoint fill simulator overstates P&L by **$638.80** — turning a losing strategy into an apparent winner. Without L2 orderbook data, backtesting is essentially fiction for market making strategies.

## Per-Strike Breakdown

### L2 Fill Simulator

| Strike | Fills | Buy | Sell | Final Pos | Cash | Settlement | P&L |
|--------|-------|-----|------|-----------|------|------------|-----|
| $160 | 0 | 0 | 0 | 0 | $0.00 | $0.00 | **$0.00** |
| $165 | 163 | 79 | 84 | -50 | $21.40 | -$50.00 | **-$28.60** |
| $170 | 25 | 10 | 15 | -50 | $10.50 | $0.00 | **+$10.50** |
| $175 | 0 | 0 | 0 | 0 | $0.00 | $0.00 | **$0.00** |
| $180 | 0 | 0 | 0 | 0 | $0.00 | $0.00 | **$0.00** |

### Analysis

1. **$160 and $175/$180** — No fills. $160 was too deep ITM (mid ~0.95) and $175/$180 too deep OTM (mid < 0.02). The Black-Scholes fair value was close to the Polymarket mid, so the min_edge threshold of $0.03 was never exceeded.

2. **$165 (near ATM)** — Most active. 163 fills, but ended up short 50 YES tokens that resolved YES → **-$50 settlement loss**. The strategy accumulated a directional short position that was wrong at resolution. This is the binary resolution risk highlighted in [[Inventory-and-Risk-Management]].

3. **$170 (slightly OTM)** — 25 fills, ended short 50. Resolved NO, so the short NO position settled to $0 → **+$10.50 profit** from spread capture.

## Why the Strategy Lost (L2)

### 1. Adverse Selection on Fills

With realistic L2 fill simulation, orders only fill when they cross the spread (price >= best ask for buys, price <= best bid for sells). This means:
- **We get filled when the market moves against us** — if our bid is hit, it's because someone wanted to sell (price dropping)
- **We don't get filled when the market moves in our favor** — our asks sit behind the queue

This is the classic adverse selection problem. The L2 simulator captures it; the midpoint simulator doesn't.

### 2. Inventory Accumulation

The $165 strike accumulated a -50 YES position (max short). Since NVDA closed at $165.06 (just barely above $165), the YES token resolved to $1.00, causing a $50 loss on the position. The strategy had no mechanism to reduce inventory aggressively when approaching resolution.

### 3. B-S Model Limitations

The Black-Scholes model with 50% IV is a rough approximation. It doesn't account for:
- Intraday volatility shifts
- The actual options-implied skew ([[Breeden-Litzenberger-Pipeline]] would be better)
- Market microstructure effects on Polymarket pricing

## Lessons Learned

### For the Strategy

1. **Min edge of 3 cents is too tight** for a 2-cent half-spread — adverse selection eats the spread. Consider 5+ cents min edge.
2. **Position limits need time-aware decay** — as expiry approaches, max position should shrink to reduce binary resolution risk.
3. **The $165 near-ATM strike is where the action is** but also where the risk is highest. The strategy needs better inventory management near ATM.
4. **B-S fair values worked surprisingly well** for deep ITM/OTM strikes (no edge found = no bad trades). The challenge is ATM where the model is least precise.

### For the Backtesting Framework

1. **L2 data is non-negotiable** for market making backtesting. The midpoint simulator produced fictional results.
2. **The fill simulation can be improved**: currently uses a binary "crosses spread or not" model. A probabilistic queue-position model using depth data would be more realistic.
3. **Need trades data in addition to orderbook data** to calibrate fill rates and validate the simulator.

## Files Produced

| File | Location |
|------|----------|
| Source code | `src/*.py` (7 modules, ~1000 lines) |
| L2 fills | `output/fills_l2.csv` |
| L2 P&L history | `output/pnl_history_l2.csv` |
| L2 fair values | `output/fair_values_l2.csv` |
| Midpoint fills | `output/fills_midpoint.csv` |
| Midpoint P&L history | `output/pnl_history_midpoint.csv` |
| Midpoint fair values | `output/fair_values_midpoint.csv` |

## Related Notes

- [[NVDA-POC-Implementation-Plan]] — Original plan
- [[Telonex-Data-Quality-Report]] — Data quality analysis
- [[Telonex-Viability-Verdict]] — Go/no-go on paid plan
- [[Core-Market-Making-Strategies]] — Strategy theory
- [[Inventory-and-Risk-Management]] — Inventory management research
- [[Performance-Metrics-and-Pitfalls]] — Metrics methodology
