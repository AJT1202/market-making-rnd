---
title: "BTC Engine Feature Analysis: Applicability to Telonex Stock/Index Engine"
created: 2026-03-31
tags:
  - backtesting
  - architecture
  - telonex
  - analysis
  - market-making
  - polymarket
related:
  - "[[btc-backtesting-engine]]"
  - "[[Backtesting-Architecture]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[NVDA-POC-Results]]"
  - "[[Telonex-Data-Quality-Report]]"
  - "[[Performance-Metrics-and-Pitfalls]]"
---

# BTC Engine Feature Analysis: Applicability to Telonex Stock/Index Engine

Critical assessment of every feature in the BTC 5-minute binary backtesting engine for applicability to a new Telonex-based stock/index binary backtesting engine. Ratings reflect how directly the feature transfers to snapshot-based L2 data from daily Parquet files.

**Key architectural difference:** The BTC engine was built for real-time WebSocket deltas (price_change, last_trade_price, book snapshots) collected by our own infrastructure. The new engine consumes periodic orderbook snapshots from Telonex Parquet files. This is not a minor detail -- it fundamentally changes how the book is maintained, how fills are detected, and how the event loop operates.

---

## Rating Scale

| Score | Meaning |
|-------|---------|
| 5 | Directly applicable, little or no change needed |
| 4 | Mostly applicable, minor adaptation required |
| 3 | Conceptually sound but needs significant rework |
| 2 | Partially relevant, core approach must change |
| 1 | Not applicable or actively harmful for the new context |

---

## Feature Assessment Summary

| # | Feature | Applicability | Recommendation |
|---|---------|:------------:|----------------|
| 1 | 5-Layer Architecture | 4 | **Adapt** |
| 2 | Raw Data Ingestion | 2 | **Replace** |
| 3 | Price and Size Conversion | 4 | **Adapt** |
| 4 | Event Normalization and Ordering | 3 | **Adapt** |
| 5 | Orderbook Reconstruction | 2 | **Replace** |
| 6 | Event Loop (Dual-Stream Clock) | 3 | **Adapt** |
| 7 | Market Phases and Lifecycle | 4 | **Adapt** |
| 8 | Order Submission and Latency Model | 5 | **Keep** |
| 9 | Queue Position Model | 3 | **Adapt** |
| 10 | Fill Simulation | 3 | **Adapt** |
| 11 | Order Cancellation | 5 | **Keep** |
| 12 | Replace Semantics | 5 | **Keep** |
| 13 | Engine Safety Checks | 5 | **Keep** |
| 14 | Portfolio and Capital Management | 4 | **Adapt** |
| 15 | Fee Computation | 5 | **Keep** |
| 16 | Settlement and Resolution | 5 | **Keep** |
| 17 | P&L Computation | 4 | **Adapt** |
| 18 | Multi-Market Batch Runs | 2 | **Replace** |
| 19 | Segmentation and Data Quality | 3 | **Adapt** |
| 20 | Warmup Tagging | 4 | **Adapt** |
| 21 | Cross-Leg Parity Checks | 3 | **Adapt** |
| 22 | Tick-Size Change Handling | 1 | **Drop** |
| 23 | Deduplication Logic | 1 | **Drop** |
| 24 | Audit Journal | 5 | **Keep** |
| 25 | Strategy Interface | 4 | **Adapt** |
| 26 | Determinism Guarantees | 5 | **Keep** |

---

## Detailed Analysis

### 1. 5-Layer Architecture

**Applicability: 4/5 -- Adapt**

```
Raw Adapter -> Book Reconstruction -> Execution Simulator -> Portfolio -> Analytics
```

**Assessment:** The layered separation of concerns is excellent architecture regardless of data source. The strict ownership boundaries (book reconstructor never touches order state, execution simulator never modifies the public book) are sound principles that prevent entire categories of bugs.

**What changes:**

- **Layer 1 (Raw Adapter):** Completely different. Ingesting Parquet columns instead of JSONL envelopes. Simpler in some ways (no envelope parsing, no event type dispatch), more complex in others (107 string columns to convert, multi-strike alignment).
- **Layer 2 (Book Reconstruction):** Dramatically simplified. There is no "reconstruction" from deltas. Each Telonex snapshot IS the complete book state. This layer becomes a thin parser/validator rather than a stateful reconstructor.
- **Layers 3-5:** Transfer almost directly. Execution simulation, portfolio management, and analytics are data-source-agnostic once they receive a clean book state.

**Recommendation:** Keep the 5-layer structure. Collapse Layers 1+2 into a single "Data Adapter" layer since there is no reconstruction step. The new stack:

```
[Layer 1: Telonex Adapter]    Parquet -> typed book snapshots, multi-strike alignment
[Layer 2: Execution Simulator] Latency, queue position, fill generation
[Layer 3: Portfolio]           Cash, positions, collateral, P&L (per-strike + aggregate)
[Layer 4: Analytics]           Metrics, cross-strike checks, segment filtering
```

---

### 2. Raw Data Ingestion

**Applicability: 2/5 -- Replace**

**Assessment:** Almost nothing transfers. The BTC engine parses gzip-compressed JSONL with a specific envelope structure (`recv_ts`, `tier`, `source`, `asset_id`, `raw`), dispatches on `event_type` (book, price_change, last_trade_price, tick_size_change), and handles REST snapshot interleaving. None of this applies.

**What the new engine needs instead:**

| BTC Engine | Telonex Engine |
|------------|---------------|
| JSONL.gz parsing | Parquet reading via PyArrow/Pandas |
| Envelope unwrapping | Flat columnar schema (107 columns) |
| Event type dispatch | Single event type: book snapshot |
| `recv_ts` / `exchange_ts` dual timestamps | `timestamp_us` / `local_timestamp_us` |
| Token side resolution via meta.json | `outcome` column ("Yes"/"No") directly in data |
| Multiple files per market | One Parquet file per market per day |

**What is salvageable:** The concept of a data adapter that produces a normalized internal representation is good. The `NormalizedEvent` pattern of converting raw exchange formats into a canonical internal structure is worth keeping, even though the conversion logic is completely different.

**Adaptation needed:**

```python
# New ingestion: read Parquet, convert 100 string columns to numeric
@dataclass
class BookSnapshot:
    timestamp_us: int
    local_timestamp_us: int
    market_id: str
    slug: str
    asset_id: str
    outcome: str  # "Yes" or "No"
    bids: list[PriceLevel]  # Up to 25 levels, parsed from bid_price_0..24, bid_size_0..24
    asks: list[PriceLevel]  # Up to 25 levels
    # Derived
    best_bid: float | None
    best_ask: float | None
    mid: float | None
    spread: float | None
```

The string-to-float conversion for 100 columns should be done once at load time, not per-event. Vectorized Pandas/PyArrow conversion on the full Parquet file, not row-by-row.

---

### 3. Price and Size Conversion

**Applicability: 4/5 -- Adapt**

**Assessment:** The zero-floating-point philosophy using integer ticks and micro-units is genuinely good engineering. Deterministic integer arithmetic prevents an entire class of subtle bugs. The BTC engine's approach of converting at the ingestion boundary and never using floats again is the right pattern.

**However, the POC already violated this.** The actual `src/fill_simulator.py` and `src/engine.py` use `float` throughout. The `EngineState` tracks `positions: dict[int, float]` and `cash: float`. The `Order` dataclass has `price: float` and `size: float`. The rigorous integer-only design documented in the BTC engine spec was not carried into the POC implementation.

**What changes:**

- Telonex prices are strings like `"0.48"` -- same fundamental problem as the BTC engine's `".48"`. The conversion approach is identical.
- Telonex sizes are strings too. Same conversion needed.
- Polymarket tick sizes for stock/index markets appear to be the same as BTC markets ($0.01 ticks in the standard range).

**Recommendation:** Carry the integer arithmetic design forward into the production engine. The POC was deliberately fast and loose. The production engine should not be. One adaptation: Telonex prices may have more decimal places than the BTC engine expected. Validate the actual precision in the data before hardcoding a tick size.

---

### 4. Event Normalization and Ordering

**Applicability: 3/5 -- Adapt**

**Assessment:** The BTC engine's normalization pipeline is sophisticated: canonical event model with 15+ fields, causal group IDs, `price_change` explosion (one envelope to multiple events), three-key canonical sort. Most of this complexity exists because WebSocket data is messy -- events arrive out of order, share timestamps, need deduplication, and must be grouped causally.

**Telonex data is pre-cleaned.** Timestamps are monotonically increasing (verified in the [[Telonex-Data-Quality-Report]]). There is no explosion step (each row is one snapshot). There is no interleaving of event types. The ordering problem is dramatically simpler.

**What remains relevant:**

- The concept of a canonical sort key is still needed, but it becomes trivial: just `timestamp_us` (already monotonic within each file).
- **The multi-strike ordering problem is new.** The BTC engine processes one market at a time. The new engine must interleave snapshots from 5+ strikes into a single chronological stream. This requires a merge-sort across files, which the BTC engine never had to do.
- Causal group IDs have no analogue. Each snapshot is an independent state observation.
- The `normalized_seq` concept can be kept as a simple row counter.

**Adaptation needed:** Replace the complex normalization pipeline with a simpler multi-file merge. The hard part shifts from "ordering within a messy stream" to "temporal alignment across parallel streams."

```python
# Merge 5 strike files into one chronological stream
all_snapshots = pd.concat([
    strike_160_df.assign(strike=160),
    strike_165_df.assign(strike=165),
    # ...
]).sort_values("timestamp_us").reset_index(drop=True)
```

---

### 5. Orderbook Reconstruction

**Applicability: 2/5 -- Replace**

**Assessment:** This is where the data format difference hits hardest. The BTC engine's book reconstruction is its most complex subsystem: it maintains `TokenBook` objects with `bids`/`asks` dicts keyed by price_ticks, applies `price_change` deltas as absolute level updates, handles `book` events as destructive resets, and validates against REST snapshots to measure drift.

**None of this applies.** With Telonex `book_snapshot_25` data, every row IS the complete book state. There are no deltas. There is no reconstruction. There is no drift. The "book" at any moment is simply the latest snapshot row for that strike.

**What replaces it:** A thin parser that converts the 25 bid levels and 25 ask levels from flat columns into a structured book representation. This is a stateless transformation, not a stateful reconstruction.

**What is lost:** The BTC engine's snapshot validation (comparing reconstructed state against REST snapshots) provided a data quality signal. With Telonex, we lose this because there is no reconstruction to validate. However, we gain a different guarantee: every book state is directly observed, not derived. There is no accumulated drift.

**Caveat -- what we miss between snapshots:** The BTC engine could observe individual order placements and cancellations between snapshots (via `price_change` events). Telonex snapshots sample the book state at discrete intervals. Between snapshots (median 21ms-342ms per the [[Telonex-Data-Quality-Report]]), we are blind. Trades and book changes that occur between snapshots are invisible. This is the fundamental limitation of snapshot-based data, and no amount of engineering can recover what was not captured.

**Recommendation:** Replace entirely. The new "book layer" is a Parquet column parser, not a stateful reconstructor. Acknowledge the between-snapshot blindness as a known limitation and design the fill simulator to account for it.

---

### 6. Event Loop (Dual-Stream Clock)

**Applicability: 3/5 -- Adapt**

**Assessment:** The BTC engine's event loop is a 5-phase-per-timestamp design:

1. External market data
2. Pending order/cancel requests
3. Internal simulator events (order_visible, cancel_effective)
4. Expiration checks
5. Strategy delivery + action processing

This is well-designed and the phase ordering invariants are correct (external before internal, no same-timestamp reaction, FIFO for same-time internals). The core logic transfers.

**What changes:**

- **"Dual-stream" is gone.** The BTC engine merges a market data stream with a collector gap stream. The new engine has a single stream of book snapshots (already merged across strikes).
- **Timestamp granularity differs.** BTC events have millisecond exchange timestamps. Telonex has microsecond timestamps. The engine should work in microseconds.
- **Dynamic queue expansion still applies.** When a strategy places an order, the order's `visible_ts` must be inserted into the timeline. The BTC engine uses `bisect.insort` for this. With Telonex's pre-loaded snapshot array, we need a different mechanism -- either a secondary priority queue for internal events that interleaves with the snapshot stream, or pre-compute all internal event timestamps and merge them.
- **The "5 phases per timestamp" structure is over-engineered for snapshots.** When the primary data is periodic snapshots rather than individual events, most timestamps have exactly one external event (one snapshot for one strike). The elaborate grouping-by-timestamp logic can be simplified.

**Recommendation:** Adapt to a simpler event loop. Keep the phase ordering invariants (external before internal, latency enforcement). Drop the timestamp-grouping complexity. Use a priority queue (min-heap) that merges the snapshot stream with internal events (order_visible, cancel_effective). This is closer to the design in [[Backtesting-Architecture]] Section 2.3 than to the BTC engine's 5-phase design.

---

### 7. Market Phases and Lifecycle

**Applicability: 4/5 -- Adapt**

**Assessment:** The PRE_OPEN / ACTIVE / CLOSED / RESOLVED lifecycle is correct for stock/index markets. The concept of preventing trading outside active hours is essential.

**What changes:**

- **BTC markets are 5 minutes long.** Stock/index markets have a full trading day (6.5 hours for US equities) plus pre/post-market Polymarket activity.
- **Market hours definition is different.** The POC already implemented this correctly: US market hours 9:30 AM - 4:00 PM ET (13:30-20:00 UTC). But the strategy may want to trade Polymarket outside equity hours (the Polymarket book is active 24/7, as shown in the [[Telonex-Data-Quality-Report]] with data from 00:00-22:54 UTC).
- **Multiple markets share the same lifecycle.** All 5 NVDA strikes on the same date have identical open/close/resolution times. The BTC engine's per-market lifecycle becomes a per-event lifecycle shared across strikes.
- **Resolution is known in advance.** For the BTC engine, resolution happens at the end of the 5-minute window. For stock/index markets, resolution happens at the equity close (4:00 PM ET). The data continues after resolution (Polymarket books remain active for settlement).

**Recommendation:** Keep the phase model. Extend the phases to support a configurable trading window. Add an `EQUITY_HOURS_ONLY` flag that the strategy can use to restrict activity. Consider adding a `PRE_RESOLUTION` phase for the final minutes before the equity close, when binary options gamma is extreme and risk management behavior should differ.

---

### 8. Order Submission and Latency Model

**Applicability: 5/5 -- Keep**

**Assessment:** The latency model is data-source-independent. The separation of `decision_to_send_ms` (infrastructure latency) and `exchange_network_ms` (blockchain/CLOB latency) is correct for Polymarket regardless of how the backtest data was collected. The three modes (CONSTANT, EMPIRICAL_DISTRIBUTION, BUCKETED_BY_TIME_OF_DAY) cover the relevant scenarios.

The same-timestamp guard (order cannot become visible at the decision timestamp) prevents look-ahead bias and applies universally.

Polymarket submission latency (200-800ms) is the same whether we are backtesting BTC markets or NVDA markets -- it is the same exchange.

**Recommendation:** Keep as-is. No adaptation needed.

---

### 9. Queue Position Model

**Applicability: 3/5 -- Adapt**

**Assessment:** The three-model approach (CONSERVATIVE / PROBABILISTIC / OPTIMISTIC) is sound, and the concept of explicit per-order queue state that evolves deterministically is the right design. The queue evolution rule (queue drains only through trades consuming depth) is correct.

**What changes fundamentally:** The BTC engine assigns queue position based on `displayed_size` from the reconstructed book at the order's `visible_ts`, then drains the queue using `last_trade_price` events. With Telonex snapshot data, we have two problems:

1. **Queue assignment is less precise.** We see the book at snapshot times, not at the exact `visible_ts`. The queue depth at the order's price level must be interpolated from the nearest snapshot. If `visible_ts` falls between two snapshots, we use the prior snapshot's depth (conservative, avoids look-ahead).

2. **Queue drain is harder to model.** The BTC engine uses explicit trade events to drain the queue. Telonex `book_snapshot_25` does not include trade events. We have two options:
   - **Use Telonex `trades` channel data** alongside snapshots. This requires downloading a second channel ($) and aligning trade timestamps with snapshot timestamps. This is the correct approach for a production engine.
   - **Infer fills from snapshot changes.** If the depth at our price level decreased between snapshots, some volume was consumed. This is noisy and lossy but works without trade data. The POC used a simpler variant of this (BBO crossing detection).

**The POC's approach was crude but directional.** The `L2FillSimulator` in `src/fill_simulator.py` checked whether the BBO crossed the order's price between snapshots. This is a binary fill/no-fill model without queue position tracking. It captures the first-order effect (did the price move through our level?) but misses queue dynamics entirely.

**Recommendation:** Adapt the queue position model. For Phase 1 of the new engine, use the CONSERVATIVE model (back of queue) with snapshot-interpolated depth. For Phase 2, integrate Telonex `trades` data to drive queue drain. The PROBABILISTIC model should use seeded RNG as before for determinism.

---

### 10. Fill Simulation

**Applicability: 3/5 -- Adapt**

**Assessment:** The 7 fill conditions from the BTC engine are all valid:

1. Order status is ACTIVE/PARTIALLY_FILLED/PENDING_CANCEL -- universal
2. `trade_ts >= order.visible_ts` -- universal
3. `cancel_effective_ts` not reached -- universal
4. Trade price matches order's price level -- universal
5. Trade direction compatible -- universal
6. `queue_ahead_units == 0` -- universal
7. Remaining trade size sufficient -- universal

The 2-phase fill process (Phase 1: queue reduction, Phase 2: fill allocation) is well-designed. The PENDING_CANCEL fill window is critical and correct.

**What changes:** The trigger mechanism. In the BTC engine, every `last_trade_price` event is a potential fill trigger. With Telonex snapshots, fills must be triggered differently:

- **With trades data:** Each trade from the `trades` channel triggers the 7-condition check. This is the direct analogue of the BTC engine and works identically.
- **Without trades data (snapshot-only):** Fills must be inferred from book state changes. A resting bid at price P fills when a subsequent snapshot shows the best ask dropped below P (meaning someone sold through our level). This is the approach the POC used, and it is a reasonable approximation -- but it can miss fills when the price spikes through our level and recovers between snapshots.

**The POC results validate this concern.** The L2 simulator showed 188 fills vs. the midpoint simulator's 3,096 fills (a 16.5x difference). This gap is partly realistic (adverse selection) and partly an artifact of snapshot-frequency limitations (fills between snapshots that the L2 simulator misses because the book recovered before the next snapshot).

**Recommendation:** Adapt. Keep all 7 conditions. Design the fill trigger to work with both trades data (preferred) and snapshot-only (fallback). When using snapshot-only mode, flag fills as lower confidence and add a configurable "fill sensitivity" parameter that controls how aggressively to infer fills from book changes.

---

### 11. Order Cancellation

**Applicability: 5/5 -- Keep**

**Assessment:** The cancellation lifecycle (decision -> cancel_sent -> cancel_effective) with independent cancel latency is exchange-specific, not data-source-specific. The PENDING_CANCEL window where fills can still occur models a real Polymarket behavior. The reservation release logic on cancel is pure portfolio accounting.

**Recommendation:** Keep as-is. No adaptation needed.

---

### 12. Replace Semantics

**Applicability: 5/5 -- Keep**

**Assessment:** Cancel + Submit non-atomic replace is how Polymarket works. The race condition (old order filling during cancel, both orders briefly coexisting) is real. Independent queue position assignment for the new order is correct.

**Recommendation:** Keep as-is.

---

### 13. Engine Safety Checks

**Applicability: 5/5 -- Keep**

**Assessment:** Phase check, tick grid check, and capital check are all valid regardless of data source.

**Minor note:** The tick grid check (`price_ticks % tick_size_ticks == 0`) needs verification against the stock/index market tick sizes. The BTC engine handled tick_size_change events because BTC 5-minute markets sometimes changed tick sizes dynamically. Stock/index markets may have different tick conventions. Verify from the Telonex data what tick sizes are actually used.

**Recommendation:** Keep all three checks. Verify tick size assumptions empirically.

---

### 14. Portfolio and Capital Management

**Applicability: 4/5 -- Adapt**

**Assessment:** The portfolio accounting (cash_balance, reserved_cash, positions, reserved_positions, fees_paid, realized_pnl) is correct and data-source-independent. The two position modes (INVENTORY_BACKED and COLLATERAL_BACKED) model real Polymarket constraints. The boxed position concept (min(yes, no) pays 100 regardless of outcome) is correct for binary markets.

**What changes:**

- **Multi-strike capital sharing.** The BTC engine tracks one YES position and one NO position per market. The new engine must track positions across 5+ strikes simultaneously. Capital is shared: a fill on the $165 strike reduces cash available for the $170 strike. This is a significant extension.
- **Cross-strike collateral.** Holding YES on the $160 strike and NO on the $170 strike creates a directional spread position. The risk profile differs from holding YES+NO on the same strike (which creates a risk-free box). The portfolio needs to understand inter-strike hedging.
- **Position limits per strike vs. aggregate.** The strategy may want per-strike position limits (max 50 shares per strike) AND aggregate limits (max 200 shares total across all strikes).

**Recommendation:** Adapt. Extend the portfolio to support `positions: dict[int, dict[str, int]]` (strike -> {YES: units, NO: units}). Add aggregate capital tracking. Keep the INVENTORY_BACKED / COLLATERAL_BACKED modes but apply them across the multi-strike portfolio.

---

### 15. Fee Computation

**Applicability: 5/5 -- Keep**

**Assessment:** `fee = (cost * fee_rate_bps) // 10000` is universal. Polymarket's zero maker fee / small taker fee structure is the same for all markets.

**Recommendation:** Keep as-is.

---

### 16. Settlement and Resolution

**Applicability: 5/5 -- Keep**

**Assessment:** Binary resolution (YES wins -> 100 ticks per unit, NO wins -> 0) is identical for BTC and stock/index markets. The settlement reconciliation invariant (`final_cash = initial_cash + trading_cashflows - fees + settlement_payouts`) is universal.

**One addition needed:** Multi-strike settlement. All strikes on the same event resolve simultaneously at the equity close. The engine must settle all 5+ strikes in a single pass. This is a mechanical extension, not a design change.

**Recommendation:** Keep. Extend to handle batch settlement of all strikes at a single resolution timestamp.

---

### 17. P&L Computation

**Applicability: 4/5 -- Adapt**

**Assessment:** Realized P&L at settlement and mark-to-market during the backtest are correct concepts. The P&L transition tracking in the audit journal is excellent for debugging.

**What changes:**

- **P&L decomposition needs enhancement.** The [[Performance-Metrics-and-Pitfalls]] framework (Spread Capture + Inventory P&L + Adverse Selection Cost) is more relevant for the stock/index engine than the BTC engine's simple realized_pnl. The new engine should compute this decomposition natively.
- **Per-strike vs. aggregate P&L.** Need both views: how did each strike contribute, and what is the portfolio-level result?
- **Resolution P&L dominates.** As noted in [[Performance-Metrics-and-Pitfalls]], binary resolution often dwarfs spread capture. The new engine should clearly separate trading P&L from resolution P&L per strike.

**Recommendation:** Adapt. Implement the 3-component P&L decomposition from [[Performance-Metrics-and-Pitfalls]]. Track per-strike and aggregate P&L. Flag resolution P&L separately.

---

### 18. Multi-Market Batch Runs

**Applicability: 2/5 -- Replace**

**Assessment:** The BTC engine processes markets sequentially with cash carry-forward. Each market gets a fresh strategy instance. No position carry-forward. This makes sense for BTC 5-minute markets where each market is independent and sequential.

**This does not work for stock/index markets.** Multiple strikes trade simultaneously on the same underlying. You cannot process them sequentially -- the strategy must see all strikes at once to:

- Manage capital allocation across strikes
- Detect cross-strike arbitrage
- Maintain a coherent portfolio view
- React to the underlying price (which affects all strikes simultaneously)

**What replaces it:**

- **Concurrent multi-strike processing.** The event loop processes snapshots from all strikes in chronological order. The strategy receives consolidated state across all strikes at each decision point.
- **Event-level granularity per day, day-level batching across days.** Multiple days of the same underlying can be batched sequentially with cash carry-forward (similar to the BTC engine's approach). But within a single day, all strikes are concurrent.
- **Strategy gets a full portfolio view.** Not a fresh instance per strike. One strategy instance managing all strikes on one underlying for one day.

**Recommendation:** Replace. Design a within-day concurrent multi-strike engine with across-day sequential batching.

---

### 19. Segmentation and Data Quality

**Applicability: 3/5 -- Adapt**

**Assessment:** The TRUSTED / DEGRADED / INVALID segment quality labels are a useful framework. The concept of marking data quality segments and filtering results accordingly is good practice.

**What changes:**

- **Gap detection is simpler.** No `gaps.jsonl` (that was our collector's artifact). Gaps are detected purely from timestamp jumps in the Telonex data, which the [[Telonex-Data-Quality-Report]] already analyzed (18-107 gaps >60s per strike, max gap 2.9 hours).
- **No drift measurement.** The BTC engine compared reconstructed book state against REST snapshots to measure drift. There is no reconstruction with Telonex, so there is no drift to measure.
- **One-sided BBO is the primary quality signal.** The data quality report shows 5-13% of snapshots have missing BBO on one side (structural, not a data error). These should be flagged differently from gaps.

**New quality signals to track:**

| Signal | Source | Impact |
|--------|--------|--------|
| Timestamp gap > threshold | Inter-snapshot interval | Mark as DEGRADED, no-trade zone |
| One-sided BBO | Missing bid or ask | Reduce fill aggressiveness |
| Crossed book | best_bid >= best_ask | Mark as INVALID (only 1 case in POC data) |
| Stale book | Repeated identical snapshots | Detect via content hashing |

**Recommendation:** Adapt. Keep the 3-label framework. Replace drift-based scoring with gap-based and BBO-validity-based scoring.

---

### 20. Warmup Tagging

**Applicability: 4/5 -- Adapt**

**Assessment:** The warmup concept (tag early fills for exclusion from headline metrics without suppressing execution) is correct. The reasoning is sound: suppressing the strategy during warmup shifts queue positions and introduces artifacts.

**What changes:**

- **Warmup duration is different.** BTC 5-minute markets need a few seconds of warmup. Stock/index day-long markets might need 5-30 minutes of warmup (time for the strategy to establish quotes, observe initial book state, compute fair values).
- **Warmup may also apply after gaps.** The BTC engine reset warmup after each segment boundary. The same should apply after gaps >60s in Telonex data.

**Recommendation:** Adapt. Keep the warmup mechanism. Make the warmup duration configurable per-run. Apply warmup at the start of the trading day AND after gaps.

---

### 21. Cross-Leg Parity Checks

**Applicability: 3/5 -- Adapt**

**Assessment:** The YES + NO parity checks (`yes_ask + no_ask >= 100 ticks`, `yes_bid + no_bid <= 100 ticks`) are correct binary market constraints. The synthetic pricing and effective spread calculations are useful diagnostic signals.

**What changes:**

- **We may only have YES-side data.** The POC downloaded only the YES outcome for each strike. If we also download NO-side data, the parity checks apply directly. If not, we cannot compute cross-leg parity.
- **Cross-STRIKE parity is more important.** For stock/index markets, the more valuable parity check is across strikes: implied probabilities should be monotonically decreasing with strike price (P(NVDA > 160) >= P(NVDA > 165) >= P(NVDA > 170)). The [[Telonex-Data-Quality-Report]] verified this held with 0% violations. This check should be built into the engine.

**Recommendation:** Adapt. Keep cross-leg (YES/NO) parity checks if we have both outcomes. Add cross-strike monotonicity checks as a new first-class diagnostic. Flag violations as arbitrage signals, not just data quality issues.

---

### 22. Tick-Size Change Handling

**Applicability: 1/5 -- Drop**

**Assessment:** The BTC engine handles `tick_size_change` events because BTC 5-minute markets dynamically change tick sizes when prices approach extremes (e.g., 0.96 or 0.04). This required auto-cancelling off-grid orders and re-quoting.

**Stock/index markets on Polymarket appear to use fixed tick sizes.** The Telonex data schema has no tick_size column or tick_size_change event. The POC data shows standard $0.01 tick sizes throughout. Unless we discover otherwise empirically, this feature is not needed.

**Recommendation:** Drop. Do not implement tick-size change handling. If tick changes are discovered in the data later, add it then -- but do not build speculative infrastructure.

---

### 23. Deduplication Logic

**Applicability: 1/5 -- Drop**

**Assessment:** The BTC engine's hash-based and semantic deduplication exists because our WebSocket collector could receive duplicate events (reconnection replays, multi-connection overlap). Telonex data is pre-deduplicated at the collection layer (they use redundant WebSocket connections with dedup). The [[Telonex-Data-Quality-Report]] found monotonically increasing timestamps with no ordering anomalies.

**Recommendation:** Drop entirely. If duplicate snapshots are discovered in future data, add content-hash dedup at that point. Do not build it proactively.

---

### 24. Audit Journal

**Applicability: 5/5 -- Keep**

**Assessment:** The append-only journal with 22+ entry types is invaluable for debugging. The deterministic serialization (canonical JSON, SHA-256 hashes) enables exact replay verification. This is completely data-source-independent.

**Recommendation:** Keep as-is. Add new entry types for multi-strike events:

- `STRIKE_SNAPSHOT`: Book state received for a specific strike
- `CROSS_STRIKE_CHECK`: Monotonicity violation detected
- `PORTFOLIO_AGGREGATE`: Periodic portfolio-level state snapshot

---

### 25. Strategy Interface

**Applicability: 4/5 -- Adapt**

**Assessment:** The event-driven strategy interface (`on_event() -> list[StrategyAction]`) is clean and correct. The consolidated `BOOK_UPDATE` (one event per BBO change, not per-level) prevents strategy overreaction to intermediate states. The available actions (PLACE_ORDER, CANCEL_ORDER, REPLACE_ORDER, NO_OP) are complete.

**What changes:**

- **Strategy needs multi-strike awareness.** The current interface delivers events one-at-a-time. The new interface should batch all book updates at a given timestamp across all strikes into a single strategy call. The strategy needs to see the full portfolio state to make coherent decisions.
- **New event type: UNDERLYING_PRICE_UPDATE.** The NVDA stock price drives fair values across all strikes. The strategy should receive underlying price updates as a distinct event type.
- **New event type: FAIR_VALUE_UPDATE.** When fair values are recomputed (e.g., from Black-Scholes or Breeden-Litzenberger), the strategy should receive an update per strike.
- **Book update should include depth information.** The BTC engine's BOOK_UPDATE only contained 4 BBO values (YES bid/ask, NO bid/ask). The new engine should include depth at the top N levels since the strategy may use depth imbalance signals.

**Proposed expanded event set:**

| Event | When | Key Data |
|-------|------|----------|
| `BOOK_UPDATE` | BBO changed on any strike | All strikes' BBOs, top-5 depth, depth imbalance |
| `UNDERLYING_UPDATE` | New stock/index price | Underlying price, timestamp |
| `FAIR_VALUE_UPDATE` | Fair values recomputed | Per-strike fair values, edge vs. market |
| `ORDER_VISIBLE` | Own order now resting | Full order details |
| `FILL` | Own order filled | Fill details, portfolio state after |
| `CANCEL_EFFECTIVE` | Cancel took effect | Order details |
| `MARKET_OPEN` | Trading begins | Initial state |
| `MARKET_CLOSE` | Trading ends | Final state |
| `RESOLUTION` | Market settled | Per-strike outcomes |

**Recommendation:** Adapt. Extend the event set and make the strategy multi-strike-aware. Keep the action types unchanged.

---

### 26. Determinism Guarantees

**Applicability: 5/5 -- Keep**

**Assessment:** Integer arithmetic, seeded RNG, canonical ordering, deterministic hashing, monotonic counters, FIFO internals -- all of these are universal best practices. They are data-source-independent and should be carried forward without modification.

The ability to run the same backtest twice and get identical results is non-negotiable for a research engine. It enables debugging, A/B testing of strategy parameters, and confidence that observed differences are due to strategy changes, not numerical noise.

**Recommendation:** Keep all six determinism guarantees. Enforce them from day one -- do not allow float arithmetic to creep into the hot path as happened in the POC.

---

## Gap Analysis: Missing Features

The following capabilities are absent from the BTC engine and must be built for the stock/index engine.

### Gap 1: Multi-Strike Portfolio Management (Critical)

**Priority: P0 -- Must have**

The BTC engine processes one market at a time. Stock/index events have 5+ simultaneous strikes on the same underlying. The engine needs:

- **Simultaneous position tracking** across all strikes
- **Shared capital pool** with per-strike and aggregate limits
- **Cross-strike risk metrics**: net delta, gamma exposure, max loss on any outcome
- **Capital allocation optimization**: which strikes to quote, how much size per strike
- **Aggregate P&L dashboard**: per-strike contributions to total P&L

This is not a minor extension. It fundamentally changes how the portfolio layer works. The BTC engine's single-market portfolio becomes a multi-instrument portfolio manager.

### Gap 2: Fair Value Integration (Critical)

**Priority: P0 -- Must have**

The BTC engine has no fair value model -- it relies entirely on the strategy to decide prices. The [[Backtesting-Architecture]] and [[Backtesting-Plan]] describe a Breeden-Litzenberger pipeline, but the BTC engine spec does not integrate it.

The POC used a Black-Scholes binary call formula (`fair_value.py`) as a stand-in. This worked for a POC but has known limitations ([[NVDA-POC-Results]] Section "B-S Model Limitations"):

- No intraday volatility shifts
- No skew/smile from the actual options market
- Inaccurate near ATM where it matters most

The production engine needs:

- **Pluggable fair value provider interface** (B-S for simple cases, B-L for production)
- **Time-synchronized fair value updates** (re-derive fair values as underlying price changes)
- **Fair value confidence bands** (wider bands = wider quotes)
- **Staleness detection** (if the options chain hasn't updated, flag the fair value as stale)

### Gap 3: Underlying Price Integration (Critical)

**Priority: P0 -- Must have**

The BTC engine has no concept of an "underlying" -- the BTC price IS the market. For stock/index markets, the underlying equity price (e.g., NVDA at $165.06) drives the fair values of all strikes. The engine needs:

- **Underlying price feed** as a separate data stream (1-minute bars from ThetaData or similar)
- **Time alignment** between Polymarket snapshots and equity prices (the [[Backtesting-Architecture]] Section 4.3 `TimeAligner` class addresses this)
- **Fair value re-derivation** triggered by underlying price changes
- **Underlying price at resolution** to determine binary outcomes

The POC loaded `nvda_prices_1m.parquet` alongside the book snapshots. This pattern should be formalized into the engine architecture.

### Gap 4: Cross-Strike Arbitrage Detection (Important)

**Priority: P1 -- Should have**

With multiple strikes, structural arbitrage opportunities can emerge:

| Arbitrage Type | Condition | Example |
|---------------|-----------|---------|
| **Monotonicity violation** | P(S>K1) < P(S>K2) where K1 < K2 | P(NVDA>160) < P(NVDA>165) |
| **Butterfly spread** | Non-convex probability density | P(160) + P(170) < 2*P(165) violation |
| **Vertical spread** | Strike spread priced incorrectly | Buy $165 YES + Sell $160 YES for guaranteed loss |
| **Calendar spread** | Same strike, different dates | Only relevant if trading multiple expiries |

The engine should detect these in real-time (at each snapshot) and expose them to the strategy as signals. The BTC engine's cross-leg parity checks are a weak version of this -- the stock/index engine needs the full cross-strike consistency framework.

### Gap 5: Trades Data Integration (Important)

**Priority: P1 -- Should have**

The POC used only `book_snapshot_25` data. The BTC engine relied heavily on trade events (`last_trade_price`) for fill simulation, queue drain, and volume tracking. The absence of trade data in the POC is a significant limitation:

- **Queue drain** cannot be modeled without trade flow
- **Fill triggers** are inferred from book changes (lossy)
- **Volume tracking** for adverse selection modeling is impossible
- **VPIN / order flow toxicity** requires trade data

Telonex offers a `trades` channel. The production engine should consume both `book_snapshot_25` and `trades` data, aligned by timestamp. This adds cost ($79/month plan includes multiple channel downloads) but dramatically improves fill simulation realism.

### Gap 6: Liquidity Regime Detection (Important)

**Priority: P1 -- Should have**

The [[Orderbook-Backtesting-with-Telonex]] describes a `identify_liquidity_regimes()` function that classifies the market into TIGHT/NORMAL/WIDE/STRESSED regimes based on spread and depth percentiles. The BTC engine has no liquidity regime awareness.

The [[Telonex-Data-Quality-Report]] shows dramatic intraday spread variation on the $165 ATM strike: from $0.83 overnight to $0.07 during peak hours. A strategy that does not adapt to these regimes will lose money quoting into illiquid periods.

The engine should compute and expose liquidity regime classifications to the strategy.

### Gap 7: Time-to-Expiry Awareness (Important)

**Priority: P1 -- Should have**

Binary option gamma increases exponentially as expiry approaches. The BTC 5-minute markets have constant (and extreme) gamma throughout their short lives. Stock/index daily markets have a full gamma curve from market open to close.

The engine should:

- Track time remaining until resolution as a first-class state variable
- Expose it to the strategy for position sizing decisions (reduce size as gamma increases)
- Use it in fair value computation (the B-S formula already needs tau)
- Enable "shutdown window" behavior (stop quoting in the final N minutes)

### Gap 8: Multi-Day Backtest Continuity (Nice to Have)

**Priority: P2 -- Nice to have**

The BTC engine's batch runner carries cash forward between sequential 5-minute markets. For stock/index markets, we may want to test strategies across multiple days of the same underlying (e.g., NVDA every day for a month). This requires:

- Loading multi-day Parquet files
- Resetting market state (new strikes, new fair values) at day boundaries
- Carrying cash and possibly realized P&L metrics across days
- Different strike sets on different days

### Gap 9: Parquet-Native Performance Optimization (Nice to Have)

**Priority: P2 -- Nice to have**

The BTC engine processes events row-by-row in Python. With 20K-39K snapshots per strike per day and 5+ strikes, the new engine processes 100K-200K snapshots per day. At this scale, pure Python may be slow.

Consider:

- Pre-computing all book state as NumPy arrays at load time (vectorize the string-to-float conversion)
- Using Polars instead of Pandas for faster Parquet reads
- Profiling the event loop to identify bottlenecks before optimizing

Do not prematurely optimize. The POC ran 140K snapshots on a single day without performance issues. But as we scale to multi-day, multi-ticker backtests, performance will matter.

---

## Summary: Build Priority

### Phase 1 -- Minimal Viable Engine

Build these first. They are the minimum required to produce trustworthy backtest results.

| Component | Source |
|-----------|--------|
| Telonex Parquet Adapter | New (replaces BTC Raw Adapter + Book Reconstruction) |
| Multi-Strike Event Loop | Adapted from BTC Event Loop |
| Market Phases | Adapted from BTC (add equity hours) |
| Order Submission + Latency | Keep from BTC as-is |
| Fill Simulation (snapshot-based) | Adapted from BTC (book-crossing trigger) |
| Order Cancellation + Replace | Keep from BTC as-is |
| Multi-Strike Portfolio | New (extends BTC Portfolio) |
| Fair Value Provider (B-S) | Adapted from POC `fair_value.py` |
| Underlying Price Feed | New |
| Settlement (multi-strike) | Adapted from BTC |
| Determinism Guarantees | Keep from BTC as-is |

### Phase 2 -- Production Quality

Add these to make the engine production-grade.

| Component | Source |
|-----------|--------|
| Trades Data Integration | New (Telonex `trades` channel) |
| Queue Position Model (trade-driven drain) | Adapted from BTC |
| Cross-Strike Arbitrage Detection | New |
| Liquidity Regime Detection | New (from [[Orderbook-Backtesting-with-Telonex]]) |
| Data Quality Segmentation | Adapted from BTC |
| Audit Journal | Keep from BTC |
| P&L Decomposition | From [[Performance-Metrics-and-Pitfalls]] |
| Warmup Tagging | Adapted from BTC |

### Phase 3 -- Advanced

| Component | Source |
|-----------|--------|
| Breeden-Litzenberger Fair Values | From [[Backtesting-Plan]] Phase 1 |
| Multi-Day Continuity | New (extends BTC Batch Runs) |
| Time-to-Expiry Gamma Management | New |
| Cross-Leg (YES/NO) Parity | Adapted from BTC (requires NO-side data) |
| Performance Optimization | As needed |

### Explicitly Dropped

| Component | Reason |
|-----------|--------|
| Tick-Size Change Handling | No evidence of dynamic tick sizes in stock/index markets |
| Deduplication Logic | Telonex data is pre-deduplicated |
| REST Snapshot Validation | No reconstruction to validate |
| Collector Gap File Parsing | No collector -- using third-party data |
| price_change Explosion | No delta events in Telonex data |
| Dual-Stream Clock | Single-stream snapshots, no gap file stream |

---

## Final Verdict

The BTC engine is well-designed but over-specified for a data source we no longer use. Roughly **60% of the design transfers** (execution simulation, portfolio, latency model, determinism, auditing), **25% needs significant adaptation** (event loop, queue model, data quality), and **15% should be dropped** (tick-size changes, deduplication, book reconstruction from deltas).

The biggest risk is not missing features from the BTC engine -- it is the **gaps** the BTC engine never addressed. Multi-strike portfolio management, fair value integration, and underlying price feeds are the three critical capabilities that must be built from scratch. Get these right and the engine will be substantially more capable than the BTC engine ever was.
