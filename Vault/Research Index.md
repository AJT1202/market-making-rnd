---
tags:
  - index
  - market-making
  - polymarket
---

# Polymarket Stock & Index Market Making — Research Index

## Overview

Research initiative into market making strategies on Polymarket's stock and index binary event markets. The core thesis: Polymarket prices are systematically mispriced relative to options-implied probabilities, creating a market making opportunity.

See [[polymarket-stock-index-mm-research-brief]] for the full research brief.

## Research Notes

### Platform & Data
- [[Polymarket-CLOB-Mechanics]] — Order book structure, token mechanics, fees
- [[Polymarket-Data-API]] — Market data endpoints, schemas, query methods
- [[Polymarket-Trading-API]] — Authentication, order placement, execution
- [[ThetaData-Options-API]] — Options chain retrieval, available fields, historical data
- [[ThetaData-Stock-Index-Data]] — Stock/index price data, resolution, bulk access
- [[Telonex-Data-Platform]] — Tick-level historical orderbook data for Polymarket ($79/mo)

### Pricing Theory
- [[Breeden-Litzenberger-Pipeline]] — Risk-neutral probability extraction framework
- [[Vol-Surface-Fitting]] — SABR vs SVI, calibration, practical recommendations
- [[Risk-Neutral-vs-Physical-Probabilities]] — Risk premium gap, adjustment methods

### Strategies
- [[Range-Market-Strategy]] — Range binary markets: pricing (B-L difference), sum-to-one arbitrage, Greeks
- [[Core-Market-Making-Strategies]] — Quoting strategies, mathematical formulations
- [[Inventory-and-Risk-Management]] — Inventory management, hedging, adverse selection
- [[Capital-Efficiency-and-Edge-Cases]] — Returns, capital allocation, risk scenarios

### Advanced Strategies (L2 + Granular Options Data)
- [[Orderbook-Microstructure-Strategies]] — OBI signals, micro-price models, queue analysis, depth-aware MM, optimal quote placement
- [[Options-Implied-Signal-Strategies]] — Intraday B-L recalibration, vol surface dynamics, Greeks signals, 0DTE, RN-to-physical bridges
- [[Order-Flow-Analysis-Strategies]] — VPIN implementation, adverse selection decomposition, informed trader detection, Hawkes processes, market impact
- [[Cross-Platform-Stat-Arb-Strategies]] — Mispricing signal properties, OU convergence trading, lead-lag analysis, sum-to-one arbitrage, multi-signal combination

### Backtesting
- [[Backtesting-Architecture]] — System design, fill simulation, data pipeline
- [[Performance-Metrics-and-Pitfalls]] — Metrics, common pitfalls, statistical rigor
- [[Backtesting-Plan]] — Phased implementation plan with data requirements
- [[Orderbook-Backtesting-with-Telonex]] — L2 fill simulation, microstructure analysis, pipeline integration
- [[NVDA-POC-Implementation-Plan]] — POC plan: Telonex viability test with NVDA March 30 event
- [[NVDA-POC-Results]] — Backtest results: L2 vs midpoint comparison ($639 P&L overstatement)
- [[Telonex-Data-Quality-Report]] — Data quality analysis (4.2/5 score)
- [[Telonex-Viability-Verdict]] — Verdict: YES, subscribe to Telonex Plus ($79/mo)
- [[Engine-Feature-Analysis]] — BTC engine feature-by-feature evaluation for Telonex (7 keep, 12 adapt, 3 replace, 2 drop)
- [[Fill-Simulation-Research]] — Hybrid trade-driven fill simulation, queue models, adverse selection research
- [[Engine-Architecture-Plan]] — Complete engine architecture spec (2500 lines, 16 sections, full interfaces)
- [[Data-Alignment-Architecture]] — DataProvider interface, tick-level indexing, event stream construction, no-lookahead enforcement

## Target Tickers

| Ticker | Type | Exchange |
|--------|------|----------|
| AAPL | Stock | NASDAQ |
| MSFT | Stock | NASDAQ |
| GOOGL | Stock | NASDAQ |
| AMZN | Stock | NASDAQ |
| META | Stock | NASDAQ |
| NVDA | Stock | NASDAQ |
| TSLA | Stock | NASDAQ |
| NFLX | Stock | NASDAQ |
| PLTR | Stock | NYSE |
| SPX | Index | CBOE |
| NDX | Index | CBOE |

## Data Sources

| Source | Data | Access |
|--------|------|--------|
| Polymarket API | Midpoints (1-min), trades (tick-level), order book | Free |
| ThetaData | Options chains, stock/index prices, Greeks, IV | Options Standard ($80/mo) |
| Telonex | Tick-level orderbook snapshots, trades, onchain fills | Plus ($79/mo) |
