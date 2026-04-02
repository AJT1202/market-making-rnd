---
title: "Backtester v1.0 — Master Roadmap"
created: 2026-04-02
tags:
  - roadmap
  - backtesting
  - architecture
  - market-making
  - polymarket
  - v1.0
status: planning
related:
  - "[[Engine-Architecture-Plan]]"
  - "[[Data-Alignment-Architecture]]"
  - "[[NVDA-POC-Results]]"
  - "[[Fill-Simulation-Research]]"
  - "[[Core-Market-Making-Strategies]]"
  - "[[Breeden-Litzenberger-Pipeline]]"
  - "[[Backtesting-Architecture]]"
  - "[[Research Index]]"
---

# Backtester v1.0 — Master Roadmap

## Executive Summary

Backtester v1.0 is a **production-quality, deterministic, event-driven backtesting engine** for market making on Polymarket stock and index binary event markets. It consumes tick-level Polymarket L2 orderbook snapshots and trades (via Telonex) alongside tick-level options NBBO data (via ThetaData), processes them through a unified timeline, simulates order fills with realistic queue position and adverse selection modeling, and produces reproducible P&L and risk analytics.

**What v1.0 enables:**

- Realistic evaluation of market making strategies across multiple strikes and underlyings, with fill simulation grounded in actual L2 depth and trade flow rather than naive midpoint assumptions
- Integration of options-implied fair values (Breeden-Litzenberger pipeline) as a first-class data source available to strategies at each simulation point with no lookahead
- Deterministic, bit-identical replay for strategy comparison, parameter tuning, and regression testing
- A clean strategy interface that separates signal generation from execution mechanics, enabling rapid iteration on quoting logic

**What v1.0 does NOT include:**

- Live trading connectivity or paper trading against Polymarket APIs
- Automated delta hedging in the options market
- Machine learning models for fill prediction or fair value estimation
- Real-time streaming or WebSocket-based data ingestion
- Multi-asset portfolio optimization across unrelated underlyings
- Gas/settlement cost modeling for on-chain Polygon transactions

**Runtime environment:** Development and small-dataset testing on MacBook Air M2 (macOS). Full-scale backtests on Windows 10 PC. Data stored on an external SSD (exFAT). All data paths read from `config.toml` → `paths.data_dir` — the only setting that changes per machine. All code uses `pathlib.Path` for cross-platform path handling.

**Lineage:** Successor to the BTC 5-minute binary engine and the [[NVDA-POC-Results|NVDA POC]] (which demonstrated that midpoint fill simulation overstates P&L by $639 and fills by 16.5x). The POC's core lesson --- L2 data and trade-driven fills are non-negotiable --- is the foundational design constraint for every phase of this roadmap.

---

## Phase Overview

| Phase | Name | Dependencies | Complexity | Key Deliverables |
|-------|------|--------------|------------|------------------|
| 1 | Data Acquisition Pipeline | None | Medium | ThetaData tick-NBBO + EOD download scripts, Telonex book+trade download, smart filtering, Parquet storage |
| 2 | Data Alignment & DataProvider | Phase 1 | High | Event stream construction, DataProvider with time-cursor, binary-search indexing, no-lookahead enforcement |
| 3 | Core Backtesting Engine | Phase 2 | High | 5-phase event loop, dual-book state, order management, queue position model, trade-driven fill simulation |
| 4 | Fair Value & Strategy Interface | Phase 2 (partial), Phase 3 | Medium-High | B-L pipeline integration, vol surface fitting, Strategy protocol, reference strategies |
| 5 | Analytics, Validation & Polish | Phases 3, 4 | Medium | P&L tracking, performance metrics, determinism verification, audit trail, NVDA replay validation |

---

## Phase Dependency Graph

```
Phase 1: Data Acquisition Pipeline
  |
  v
Phase 2: Data Alignment & DataProvider
  |         \
  v          \
Phase 3:      Phase 4 (partial):
Core Engine   B-L pipeline can begin
  |           once DataProvider exists
  v          /
Phase 4:    /
Fair Value & Strategy Interface
(full integration requires Phase 3)
  |
  v
Phase 5: Analytics, Validation & Polish
```

More precisely:

```
  [Phase 1] ────────────────────────────────┐
      │                                     │
      v                                     │
  [Phase 2] ────────────┐                   │
      │                 │                   │
      v                 v                   │
  [Phase 3]        [Phase 4 early]          │
      │            (B-L pipeline,           │
      │             vol surface)            │
      v                 │                   │
  [Phase 4 full] <──────┘                   │
  (strategy interface,                      │
   reference strategies)                    │
      │                                     │
      v                                     │
  [Phase 5] <───────────────────────────────┘
  (validation uses raw data from Phase 1)
```

**Critical path:** Phase 1 --> Phase 2 --> Phase 3 --> Phase 4 (full) --> Phase 5. The B-L pipeline and vol surface fitting (Phase 4 early) can begin in parallel with Phase 3 once the DataProvider is available, which shortens the total timeline.

---

## Phase Summaries

### Phase 1: Data Acquisition Pipeline

**Scope:** Build robust, idempotent download scripts for both data sources. ThetaData tick-level NBBO quotes and EOD Greeks/OI for all target tickers and filtered option contracts. Telonex `book_snapshot_full` and `trades` channels for all target Polymarket markets. Smart filtering keeps data volume manageable: strikes within 20% of ATM, expiries within 30 DTE. All output is sorted Parquet with consistent partitioning by date and entity. This phase extends the existing `Code/scripts/download_options.py` with new commands rather than building from scratch.

**Key decisions:** zstd compression for ThetaData downloads (better ratio for large tick-level files); Snappy for Telonex data (native SDK output) and aligned event streams (fast decompression). One Parquet file per (ticker, expiry) for ThetaData and per (market_slug, date) for Telonex. The `market_registry.parquet` mapping Polymarket markets to (ticker, strike, expiry) is built from Telonex market metadata.

**Risks:** ThetaData rate limits on Standard tier (4 concurrent requests) may make full-universe downloads slow. Telonex API availability and data completeness for less-liquid markets. Mitigation: incremental downloads with checkpoint tracking; data quality reports generated per download batch.

**Detailed plan:** [[Phase-1-Data-Acquisition]]

---

### Phase 2: Data Alignment & DataProvider

**Scope:** Merge raw Telonex book snapshots and trades into unified, chronologically sorted event streams per market. Build the `DataProvider` --- the sole interface through which the engine and strategy access all data. The DataProvider maintains a monotonically advancing time cursor; every query returns only data with timestamps <= current simulation time, making lookahead impossible by construction. Binary-search indexing (`np.searchsorted`) on pre-sorted Parquet arrays provides O(log n) access. Lazy loading and LRU caching keep memory usage bounded even with multi-day, multi-ticker backtests. Data quality checks enforce timestamp monotonicity, detect gaps, and validate cross-source consistency (e.g., Telonex trade timestamps falling within ThetaData market hours).

**Key decisions:** Microsecond integer timestamps throughout (int64, matching Telonex's `timestamp_us`), eliminating floating-point drift. Forward-fill semantics for "latest as of t" queries. Source timestamps are authoritative --- no re-timestamping. The event stream interleave order for same-timestamp events is: underlying price > book snapshot > trade (ensuring the book state is current before trade-driven fill checks).

**Risks:** Clock skew between Telonex (exchange timestamps) and ThetaData (OPRA timestamps) could cause subtle misalignment. Mitigation: timezone normalization to UTC at download time; configurable clock-offset parameter per source for sensitivity testing. Memory pressure from loading full-depth L2 snapshots for many markets. Mitigation: lazy loading with configurable max-markets-in-memory.

**Detailed plan:** [[Phase-2-Data-Alignment]]

---

### Phase 3: Core Backtesting Engine

**Scope:** The heart of the system. A 5-phase event loop processes the unified timeline in strict chronological order: (1) external events, (2) internal events (order visibility, cancel effectiveness after latency), (3) fill checks, (4) fair value recomputation, (5) strategy update and action processing. Dual-book state maintains independent YES and NO orderbooks per market, rebuilt from each `book_snapshot_full` (snapshot-direct, no delta reconstruction). Order management supports submit, cancel, and amend with configurable latency simulation. The queue position model tracks where our orders sit in the book and drains queue based on actual trade volume. Fill simulation is trade-driven: only trades trigger fills, depth changes never do. Multi-market support handles 5-10 simultaneous strikes per underlying. All arithmetic uses integers (ticks, centishares, basis points) for bitwise determinism.

**Key decisions:** Snapshot-direct book state eliminates reconstruction drift entirely (a lesson from moving away from delta-based books in the BTC engine). The "external before internal" rule ensures market data at time T is fully processed before any order actions at T. The conservative fill bias --- when uncertain, assume fewer fills --- guards against the 16.5x overcount demonstrated in the POC. Cross-leg parity (YES_ask + NO_ask >= $1.00) is monitored but not synthesized; the two books remain independent.

**Risks:** The 5-phase loop is architecturally complex; bugs in phase ordering can violate causality. Mitigation: extensive unit tests for phase sequencing; the existing `bt_engine/engine/loop.py` implementation provides a validated starting point. Queue position modeling with snapshot-granularity data (0.1-3s intervals) introduces approximation error. Mitigation: configurable queue drain aggressiveness with sensitivity analysis.

**Detailed plan:** [[Phase-3-Core-Engine]]

---

### Phase 4: Fair Value & Strategy Interface

**Scope:** Integrate the Breeden-Litzenberger pipeline as the primary fair value source, consuming ThetaData tick-level options data through the DataProvider. Vol surface fitting (SABR or SVI, to be determined by calibration quality) converts discrete option quotes into a continuous implied volatility surface from which strike-specific binary probabilities are extracted. The `Strategy` protocol defines three callbacks --- `on_market_update()`, `on_trade()`, `on_fair_value_update()` --- and returns `StrategyAction` objects (PLACE, CANCEL, AMEND). The strategy decides when to request fair value recomputation (every N seconds, on vol regime change, etc.) rather than the engine forcing a cadence. Reference strategies include: probability-based quoting (adapted from the POC), Avellaneda-Stoikov / GLFT inventory-aware quoting, and cross-market arbitrage (exploiting YES+NO < $1.00 dislocations). Strategy configuration uses dataclass parameters with YAML serialization.

**Key decisions:** The B-L pipeline lives in the strategy layer, not the engine layer --- different strategies may want different fair value models (B-S, B-L, hybrid, external). The engine provides the data; the strategy interprets it. The existing `Strategy` protocol in `bt_engine/strategy/interface.py` (`on_market_update`, `on_fill`) is extended with `on_trade` and `on_fair_value_update` for richer signal processing. Vol surface fitting happens lazily on strategy request, not on every tick, to avoid unnecessary computation.

**Risks:** SABR/SVI calibration can fail or produce unstable surfaces in illiquid regimes (few strikes, wide spreads). Mitigation: fallback to Black-Scholes with historical IV when surface fitting fails; monotonicity enforcement on the CDF extracted from B-L. The strategy interface must be general enough for diverse strategies without becoming a kitchen-sink abstraction. Mitigation: keep the protocol minimal; complex strategies compose internal state without engine support.

**Detailed plan:** [[Phase-4-Fair-Value-Strategy]]

---

### Phase 5: Analytics, Validation & Polish

**Scope:** P&L tracking with full decomposition: realized (from fills), unrealized (mark-to-market), and settlement (binary resolution). Performance metrics: Sharpe ratio, fill rate, adverse selection ratio (fraction of fills followed by adverse price movement), inventory statistics (time-weighted average position, max drawdown from inventory), and spread capture efficiency. Determinism verification: a test harness that runs the same backtest twice and asserts bitwise-identical output. Audit trail: every order submission, fill, cancellation, quote update, and fair value recomputation is logged with timestamps and full state. Validation: replay the NVDA March 30 POC data through the v1.0 engine and compare fill counts, P&L, and position trajectories against the known POC results (163 L2 fills, -$18.10 P&L). Documentation covering architecture, usage, strategy authoring, and data pipeline operation.

**Key decisions:** Settlement uses the actual Polymarket resolution (YES=$1.00 or NO=$1.00) applied to final positions. Unrealized P&L is marked to the Polymarket mid, not the fair value, to reflect executable exit prices. The audit journal is append-only and can be exported to Parquet for post-hoc analysis. Determinism is enforced by integer arithmetic, fixed seed for any stochastic components, and deterministic sort ordering for same-timestamp events.

**Risks:** NVDA POC replay may not match exactly due to architectural differences (dual-book vs single-book, trade-driven vs snapshot-driven fills). This is expected and informative --- the comparison validates the direction and magnitude of the difference, not exact reproduction. Mitigation: document all known sources of divergence; ensure the new engine's results are more conservative (fewer fills, lower P&L) than the POC's already-conservative L2 results.

**Detailed plan:** [[Phase-5-Analytics-Validation]]

---

## Critical Design Decisions

### 1. Trade-Driven Fills, Not Snapshot-Inferred

**Decision:** Only actual trade events from the Telonex `trades` channel trigger fill checks. Book snapshot depth changes never generate fills.

**Rationale:** The NVDA POC demonstrated a 16.5x fill overcount when inferring fills from price movements. Depth decreases can be cancellations, not trades. The BTC engine's proven architecture uses the same principle. With both `book_snapshot_full` and `trades` available, the phantom fill problem is completely eliminated.

**Reference:** [[Fill-Simulation-Research]] Section 7 (Hybrid Approach 5), [[NVDA-POC-Results]]

### 2. Integer Arithmetic Throughout

**Decision:** All prices, sizes, and intermediate calculations use integer representations: ticks (1 tick = $0.01), centishares (100 cs = 1 share), basis points (10000 bps = 100%). No floating-point arithmetic in the simulation path.

**Rationale:** Floating-point arithmetic is non-deterministic across platforms and compiler settings. Integer arithmetic guarantees bitwise-identical results across runs, machines, and Python versions. The existing engine (`bt_engine/units.py`) already implements the conversion functions (`bps_to_ticks`, `ticks_to_price`, `cs_to_shares`, `tc_to_dollars`).

**Reference:** [[Engine-Architecture-Plan]] Section 13 (Determinism)

### 3. Snapshot-Direct Book State (No Delta Reconstruction)

**Decision:** Each `book_snapshot_full` from Telonex is treated as the authoritative, complete orderbook state. The engine does not reconstruct books from deltas.

**Rationale:** Delta-based reconstruction accumulates drift and requires periodic REST validation snapshots (as in the BTC engine). Telonex provides complete snapshots at every change, eliminating reconstruction entirely. This is simpler, more reliable, and perfectly suited to historical replay.

**Reference:** [[Engine-Architecture-Plan]] Section 1.3 (Key Architectural Differences)

### 4. Dual-Book Independence

**Decision:** YES and NO orderbooks per market are maintained as independent `TokenBook` instances. Cross-leg parity (YES_ask + NO_ask >= $1.00) is monitored and logged but never synthesized or enforced.

**Rationale:** On Polymarket, YES and NO tokens trade independently with separate orderbooks. Synthesizing one from the other would introduce artificial correlation. The dual-book model matches the actual exchange structure and enables strategies that exploit cross-leg dislocations.

**Reference:** [[Engine-Architecture-Plan]] Section 1.2 (Dual-Channel, Dual-Book Model)

### 5. DataProvider as the Single Data Access Point

**Decision:** All data access --- Polymarket books, trades, ThetaData options, underlying prices --- flows through a single `DataProvider` interface with a monotonically advancing time cursor.

**Rationale:** Centralizing data access through one interface with a time cursor makes lookahead impossible by construction. The strategy cannot accidentally query future data because the DataProvider physically will not return it. This is stronger than relying on discipline or code review to prevent lookahead.

**Reference:** [[Data-Alignment-Architecture]] Section 5 (DataProvider Interface), Section 7 (No-Lookahead Enforcement)

### 6. Strategy Owns Fair Value, Engine Owns Execution

**Decision:** The Breeden-Litzenberger pipeline, vol surface fitting, and fair value computation live in the strategy layer. The engine provides raw data and execution simulation but does not compute or impose fair values.

**Rationale:** Different strategies need different fair value models. A probability quoting strategy may use B-L; an inventory-aware strategy may blend B-L with microstructure signals; a simple strategy may use Black-Scholes. Pushing fair value into the engine would either constrain strategy diversity or bloat the engine with every possible model. The existing architecture (the `FairValueManager` in `bt_engine/fair_value/`) provides B-S as a built-in convenience, but strategies are free to ignore it.

**Reference:** [[Engine-Architecture-Plan]] Section 8 (Fair Value Integration)

### 7. 5-Phase Event Loop with Strict Ordering

**Decision:** Each simulation timestamp is processed through five sequential phases: (1) external data ingestion, (2) internal event maturation (latency), (3) fill checks, (4) fair value update, (5) strategy decision. No phase may produce effects visible to an earlier phase at the same timestamp.

**Rationale:** This phase ordering enforces causality. The "external before internal" rule ensures the engine sees the true market state before processing its own latency-delayed orders. The "no same-timestamp reaction" rule prevents the strategy from reacting to data it could not have observed in real time. The existing `bt_engine/engine/loop.py` implements this structure.

**Reference:** [[Engine-Architecture-Plan]] Section 3 (Time Model and Event Loop), Section 1.4 (Design Principles)

### 8. Conservative Fill Bias

**Decision:** When the fill model is uncertain (e.g., trade volume at our price level is ambiguous, queue position is approximate), the engine assumes fewer fills rather than more.

**Rationale:** The single most dangerous failure mode in market making backtesting is overstating fill rates. The POC showed that optimistic fill assumptions turn a -$18 loss into a +$621 profit. A backtest that says a strategy loses money when it actually makes money is annoying but safe; a backtest that says a strategy makes money when it actually loses money is catastrophic. Every ambiguity in the fill model is resolved toward fewer, not more, fills.

**Reference:** [[NVDA-POC-Results]] (Headline Result), [[Fill-Simulation-Research]] (Primary Recommendation)

---

## Success Criteria

v1.0 is **done** when all of the following are satisfied:

1. **End-to-end pipeline works:** Raw data download (Phase 1) through final analytics output (Phase 5) runs without manual intervention for at least 3 different tickers across 5+ trading days each
2. **Determinism verified:** Running the same backtest configuration twice produces bitwise-identical fill logs, P&L series, and summary metrics
3. **NVDA POC replay validates:** The v1.0 engine replaying NVDA March 30 data produces results directionally consistent with the POC (fewer fills and lower absolute P&L than the POC's L2 simulator, since the POC used snapshot-only fill inference while v1.0 uses trade-driven fills)
4. **Multi-strike simultaneous operation:** A single backtest run processes 5 strikes for one underlying with correct cross-strike portfolio accounting
5. **Fill realism passes sanity checks:** Fill rates are in the 0.5-3% range for typical market making parameters (consistent with the POC's 0.93% L2 fill rate), not the 10-15% range that indicates phantom fills
6. **Strategy interface is validated:** At least two distinct strategies (probability quoting + one inventory-aware variant) run successfully with different parameter configurations
7. **Audit trail is complete:** Every order, fill, cancellation, and fair value update is logged with full state context, enabling post-hoc reconstruction of any decision point
8. **Documentation exists:** Architecture overview, data pipeline guide, strategy authoring tutorial, and configuration reference are written and accurate

---

## Known Risks and Mitigations

### Technical Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Queue position model inaccuracy due to snapshot granularity (0.1-3s between snapshots) | High | High | Configurable queue drain aggressiveness; sensitivity analysis across parameter ranges; calibrate against known fill rates from Telonex trades data |
| Memory pressure from full-depth L2 snapshots across many markets | Medium | Medium | Lazy loading, LRU cache eviction, configurable max-markets-in-memory; profile memory usage during Phase 2 development |
| 5-phase event loop complexity introduces causality bugs | High | Medium | Extensive unit tests for phase ordering; property-based tests asserting "no future data visible"; the existing `loop.py` is a validated starting point |
| SABR/SVI vol surface calibration instability in illiquid regimes | Medium | High | Fallback to B-S with historical IV; monotonicity enforcement on B-L CDF; graceful degradation rather than crash |
| Integer overflow in tick/centishare arithmetic for extreme positions | Low | Low | Range checks in `bt_engine/units.py`; Python's arbitrary-precision integers prevent silent overflow |

### Data Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Telonex data gaps (missing snapshots or trades for periods) | High | Medium | Gap detection in data quality checks; strategy receives a `data_gap` flag and can pause quoting |
| ThetaData tick NBBO unavailable for some contracts (illiquid far-OTM options) | Medium | High | Smart filtering (20% ATM, 30 DTE) excludes most illiquid contracts; fallback to EOD data with interpolation |
| Clock skew between Telonex and ThetaData timestamps | Medium | Medium | UTC normalization at download time; configurable per-source clock offset for sensitivity testing |
| Telonex API changes or downtime | Low | Low | Checkpoint-based incremental downloads; raw data cached locally; download scripts are idempotent |

### Scope Risks

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| Phase 4 (fair value) scope creep from adding more pricing models | Medium | High | v1.0 ships with B-S + B-L only; additional models (SABR/SVI) are optional enhancements, not blockers |
| Strategy interface becomes too complex trying to satisfy all possible strategies | Medium | Medium | Keep the `Strategy` protocol minimal (3 callbacks + actions); complex strategies compose internally |
| Performance optimization rabbit holes (Rust rewrite, GPU acceleration) | Low | Medium | Python-only for v1.0; profile first, optimize only proven bottlenecks; Rust rewrite is a v2.0 consideration |

---

## What v1.0 Does NOT Include

These are explicit scope boundaries. Each may become a future version's feature, but is out of scope for v1.0:

- **Live trading or paper trading** --- v1.0 is historical replay only; no connection to Polymarket CLOB APIs for order placement
- **Automated delta hedging** --- the engine does not trade options or stock to hedge binary positions; hedging analysis is offline
- **Machine learning models** --- no neural fill prediction, no learned fair values, no RL-based strategy optimization; all models are analytical (B-S, B-L, parametric vol surfaces)
- **Real-time streaming** --- no WebSocket connections, no incremental book updates; all data is pre-downloaded Parquet
- **Multi-underlying portfolio optimization** --- each underlying is backtested independently; no cross-underlying capital allocation or correlation modeling
- **Gas/settlement cost modeling** --- Polygon on-chain settlement costs are not modeled; the engine assumes zero-cost settlement (reasonable for maker-only strategies with zero maker fees)
- **Range market support** --- v1.0 targets above/below binary markets only; range markets (e.g., "NVDA between $160 and $170") require sum-to-one constraint handling described in [[Range-Market-Strategy]] and are deferred
- **Intraday data re-download** --- data is batch-downloaded once per day; no intraday refresh or streaming updates during backtests
- **GUI or web dashboard** --- all output is programmatic (Parquet files, CSV, stdout); visualization is left to notebooks or external tools

---

## Detailed Phase Plans

Each phase has a dedicated plan document with implementation-level detail:

1. [[Phase-1-Data-Acquisition]] --- Download scripts, filtering logic, Parquet schemas, rate limit handling
2. [[Phase-2-Data-Alignment]] --- Event stream construction, DataProvider interface, indexing, caching, quality checks
3. [[Phase-3-Core-Engine]] --- Event loop, dual-book state, order management, queue position, fill simulation, multi-market
4. [[Phase-4-Fair-Value-Strategy]] --- B-L pipeline, vol surface, Strategy protocol, reference strategies, configuration
5. [[Phase-5-Analytics-Validation]] --- P&L decomposition, metrics, determinism tests, NVDA replay, audit trail, docs

---

## Reference Architecture Documents

- [[Engine-Architecture-Plan]] --- Complete 16-section technical specification (2500+ lines)
- [[Data-Alignment-Architecture]] --- DataProvider interface, storage layout, download pipeline, no-lookahead enforcement
- [[Fill-Simulation-Research]] --- Trade-driven fill simulation, queue models, adverse selection, dual-orderbook handling
- [[NVDA-POC-Results]] --- POC results proving L2 data necessity (16.5x fill overcount, $639 P&L overstatement)
- [[Backtesting-Architecture]] --- Original architecture notes (event-driven rationale, component design)
- [[Core-Market-Making-Strategies]] --- Strategy theory (probability quoting, AS/GLFT, inventory management)
- [[Breeden-Litzenberger-Pipeline]] --- Risk-neutral probability extraction from options chains
