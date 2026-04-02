---
title: "Telonex Viability Verdict"
created: 2026-03-31
tags:
  - telonex
  - verdict
  - data-platform
  - evaluation
---

# Telonex Viability Verdict

## Decision: YES — Subscribe to Telonex Plus ($79/month)

## Evidence

### The Killer Metric

The NVDA POC backtest ([[NVDA-POC-Results]]) demonstrated that **without L2 orderbook data, market making backtests produce fictional results**:

| Simulator | P&L | Fills | Verdict |
|-----------|-----|-------|---------|
| **L2 (Telonex data)** | **-$18.10** | 188 | Strategy needs work |
| **Midpoint (no Telonex)** | **+$620.70** | 3,096 | "Looks amazing!" (wrong) |

The midpoint simulator overstated P&L by **$638.80** and fill count by **16.5x**. Without Telonex, we would have deployed a losing strategy believing it was profitable.

### Data Quality Assessment

From [[Telonex-Data-Quality-Report]]:

| Dimension | Score | Notes |
|-----------|-------|-------|
| Coverage completeness | 4/5 | Full day, minor overnight gaps |
| Schema usability | 4/5 | Clean Parquet, consistent schema (strings need type conversion) |
| Update frequency | 5/5 | Sub-second median (21-342ms) — genuine tick-level |
| Depth usefulness | 4/5 | 25 levels captured; effective depth is 3-5 levels (Polymarket is thin) |
| Data size efficiency | 5/5 | 860KB-1.4MB per market per day — very manageable |
| **Overall** | **4.2/5** | |

### Cost-Benefit

| Item | Monthly Cost |
|------|-------------|
| Telonex Plus | $79 |
| ThetaData Options Standard | $80 |
| **Total data cost** | **$159** |

The $79/month is:
- Less than the cost of a single bad trade from an uncalibrated strategy
- The only source of historical L2 orderbook data for Polymarket
- Unlimited downloads for all 839K+ markets

### What Telonex Enables

1. **Realistic fill simulation** — Queue position estimation with actual depth data
2. **Spread analysis** — How wide are spreads really? When do they tighten/widen?
3. **Liquidity profiling** — Which markets have enough depth to make?
4. **Adverse selection measurement** — How often do fills coincide with adverse moves?
5. **Strategy parameter tuning** — Calibrate min_edge, half_spread, position limits against real microstructure

### Limitations Acknowledged

1. Off-chain data starts October 2025 — no earlier history for book snapshots
2. Polymarket's own thin liquidity limits depth usefulness (3-5 effective levels even with 25 captured)
3. The Telonex Python SDK has a redirect-following bug — we had to use raw HTTP requests. Minor inconvenience.
4. No real-time data — Telonex is historical only, updated daily

## Next Steps After Subscribing

1. **Download multi-day data** for the NVDA event (March 27-31) to see pre-expiry dynamics
2. **Add trades channel** to calibrate fill simulation probability models
3. **Expand to more events** — run the same backtest on 10+ resolved events to get statistical significance
4. **Tune strategy parameters** with realistic fill simulation:
   - Increase min_edge to 5+ cents
   - Add time-aware position limits
   - Implement Avellaneda-Stoikov inventory skewing
5. **Upgrade fair value model** from Black-Scholes to full [[Breeden-Litzenberger-Pipeline]] via ThetaData

## Related Notes

- [[NVDA-POC-Results]] — Full backtest results
- [[Telonex-Data-Quality-Report]] — Detailed data quality analysis
- [[Telonex-Data-Platform]] — API reference
- [[Orderbook-Backtesting-with-Telonex]] — Implementation research
- [[NVDA-POC-Implementation-Plan]] — Original plan
