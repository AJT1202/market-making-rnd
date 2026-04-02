---
title: "Backtesting Engine Implementation Reference"
created: 2026-04-01
updated: 2026-04-01
tags:
  - backtesting
  - engine
  - architecture
  - implementation
  - reference
  - market-making
  - polymarket
related:
  - "[[Engine-Architecture-Plan]]"
  - "[[Backtesting-Architecture]]"
  - "[[NVDA-POC-Results]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[Data-Download-Plan]]"
  - "[[Performance-Metrics-and-Pitfalls]]"
---

# Backtesting Engine Implementation Reference

> **Purpose**: Complete reference for the production backtesting engine at `backtesting-engine/bt_engine/`. Written for future-you when updating, debugging, or extending the engine.
>
> **Lineage**: Implements the spec from [[Engine-Architecture-Plan]]. Replaces the NVDA POC in `Telonex testing/src/`. Incorporates all lessons from [[NVDA-POC-Results]] (16.5x fill overcount without L2 data) and the 3-auditor code review (15 issues found and fixed).

---

## 1. Package Layout

```
backtesting-engine/
  pyproject.toml                          # Python 3.11+, deps: pyarrow, numpy, scipy, pandas
  bt_engine/
    __init__.py                           # v0.1.0
    types.py                              # All enums (TokenSide, EventKind, Side, OrderStatus, etc.)
    units.py                              # Integer conversion boundary (ticks, centishares, tc)
    config.py                             # EngineConfig and sub-configs (frozen dataclasses)

    data/
      schema.py                           # TimelineEvent, BookSnapshot (numpy-backed), TradeEvent, UnderlyingPrice
      store.py                            # DataStore: holds timeline + payload arrays
      loader.py                           # DataLoader: parquet -> DataStore (vectorized)

    execution/
      order.py                            # SimOrder (mutable lifecycle), Fill (frozen), OrderManager
      latency.py                          # LatencyModel (CONSTANT mode)
      queue_position.py                   # QueuePositionModel (CONSERVATIVE/PROBABILISTIC/OPTIMISTIC)
      fill_engine.py                      # TradeDrivenFillEngine (7-condition model, production)
      fill_engine_snapshot.py             # SnapshotFillEngine (BBO-movement fallback)

    portfolio/
      positions.py                        # StrikePosition, Portfolio (cash, reservations, settlement)
      settlement.py                       # SettlementEngine (resolution, reconciliation)

    fair_value/
      pricer.py                           # FairValuePricer Protocol, BlackScholesPricer
      manager.py                          # FairValueManager (multi-strike, monotonicity)

    strategy/
      interface.py                        # Strategy Protocol, StrategyAction, StrategyUpdate
      probability_quoting.py              # ProbabilityQuotingStrategy (cancel-and-replace)

    analytics/
      journal.py                          # AuditJournal (in-memory)
      metrics.py                          # BacktestMetrics, compute_metrics(), print_metrics()
      pnl.py                              # Placeholder for future 5-component decomposition

    engine/
      internal_queue.py                   # InternalEventQueue (bisect-sorted)
      loop.py                             # BacktestEngine (5-phase event loop), BacktestResult

    runner.py                             # run_backtest() entry point, CSV export

  scripts/
    run_nvda_poc.py                       # NVDA March 30 replication
  tests/
    (planned, not yet implemented)
```

---

## 2. Integer Arithmetic System

**All prices, sizes, and cash use integers internally.** Float conversion happens exactly once at the data loading boundary (`data/loader.py`) and once for display (`units.py` display helpers). This guarantees deterministic, bit-identical results across runs.

### Unit System

| Concept | Unit | Name | Conversion | Example |
|---|---|---|---|---|
| Price | 1 tick = $0.01 | ticks | `price * 100` | $0.48 = 48 ticks |
| Size | 1 cs = 0.01 shares | centishares | `size * 100` | 10 shares = 1000 cs |
| Cash | tick-centishares | tc | `ticks * centishares` | Buy 10 shares @ $0.50 = 50 * 1000 = 50,000 tc = $5.00 |
| Fair value | 1 bp = 0.01% | basis points | `probability * 10000` | 0.5138 = 5138 bps |
| Underlying | 1 cent = $0.01 | cents | `price * 100` | $165.06 = 16506 cents |

**Cash conversion**: `dollars = tc / 10,000`. This works because 1 tick = $0.01 and 1 cs = 0.01 shares, so the product `ticks * cs` has unit `$0.01 * 0.01 = $0.0001`, and $1.00 = 10,000 tc.

**Double midpoint**: `mid_ticks_x2 = best_bid_ticks + best_ask_ticks`. Avoids float division. Actual mid = `mid_ticks_x2 / 2` (for display only).

### Where Conversion Happens

- **Data load time** (`loader.py`): String/float parquet columns -> int64 numpy arrays -> BookSnapshot/TradeEvent
- **Fair value boundary** (`pricer.py`): `scipy.stats.norm.cdf` returns float -> `probability_to_bps()` -> int
- **Strategy boundary** (`probability_quoting.py`): `fair_value_bps / 100` -> ticks (integer division)
- **Display only** (`runner.py`, `metrics.py`): tc -> dollars, ticks -> price, cs -> shares

---

## 3. Data Layer

### Data Sources

The engine consumes two types of data from Telonex, plus underlying stock/index prices:

| Channel | Data | Used For |
|---|---|---|
| `book_snapshot_full` (or `book_snapshot_25`) | Full orderbook snapshots at every tick | Book state, spread, depth, BBO-movement fills |
| `trades` | Individual trade events | Trade-driven fills (production mode) |
| Underlying prices (yfinance/ThetaData) | 1-minute OHLCV bars | Fair value computation via Black-Scholes |

### BookSnapshot — Lightweight Numpy Views

The critical performance decision: BookSnapshot does **not** materialize tuples of BookLevel objects. Instead, each snapshot holds references to numpy array slices from the 2D arrays built at load time.

```python
class BookSnapshot:
    # Constructor receives numpy arrays directly (row slices from 2D arrays)
    bid_prices: np.ndarray   # shape (max_levels,), dtype int64 (ticks)
    bid_sizes: np.ndarray    # shape (max_levels,), dtype int64 (centishares)
    ask_prices: np.ndarray   # same
    ask_sizes: np.ndarray    # same
```

This reduces load time from 33s (materializing 140K frozen dataclasses with tuples) to 2.9s (numpy array slicing).

Properties (`best_bid_ticks`, `best_ask_ticks`, `is_valid`, `depth_at_price()`, etc.) access the underlying arrays on demand.

### Unified Timeline

All data goes into a single sorted list of `TimelineEvent` objects. Each event stores:
- `timestamp_us`: microsecond timestamp (int64)
- `kind`: EventKind enum (determines processing priority at same timestamp)
- `strike`: which market (0 for underlying prices)
- `token_side`: YES or NO
- `payload_index`: index into the corresponding payload store (snapshots, trades, or underlying_prices)
- `sequence`: monotonic counter for deterministic tie-breaking

**Sort order**: `(timestamp_us, kind.value, sequence)`. The `kind.value` priority ensures:
1. Underlying prices (0) processed first — FV is current before book data arrives
2. Book snapshots (2) processed next — market state updated
3. Trades (3) processed last — fills triggered after book state is current

### OHLCV Lookahead Prevention

Standard 1-minute bar data has the Close price timestamped at bar-open (e.g., 10:00 bar has Close at 10:01, but timestamp says 10:00). The loader shifts all bar timestamps forward by `underlying_bar_duration_us` (default 60s) to fix this:

```python
if bar_duration > 0:
    ts_arr = ts_arr + bar_duration
```

Set `underlying_bar_duration_us = 0` for tick-level data that already has point-in-time timestamps.

### Two Data Modes

| Mode | When | Data Available | Fill Trigger |
|---|---|---|---|
| `SNAPSHOT_ONLY` | POC data, pre-download | `book_snapshot_25` (YES only) | BBO movement through order price |
| `TRADE_DRIVEN` | Production, post-download | `book_snapshot_full` + `trades` (YES + NO) | Actual trade events only |

The `DataStore.fill_mode` flag is set at load time based on `EngineConfig.fill.mode`. The engine selects the corresponding fill engine at initialization.

---

## 4. Engine Event Loop

The engine processes events in strict chronological order. For each timestamp, it runs 5 phases:

```
for each external event in timeline:
    [M2] Process any internal events with earlier timestamps (interleaved)

    Phase 1: Process external event
      - UNDERLYING_PRICE: update latest_underlying_cents, recompute fair values
      - BOOK_SNAPSHOT: update book state, check snapshot fills, run strategy
      - TRADE: check trade-driven fills

    Phase 2: Process internal events at this timestamp
      - ORDER_VISIBLE: assign queue position, check aggressive fill
      - CANCEL_EFFECTIVE: transition to CANCELLED, release reservation

    Phase 3: (embedded in Phase 1 for snapshots) Fill checks
    Phase 4: (embedded in Phase 1) Fair value is current from underlying price
    Phase 5: (embedded in Phase 1 for snapshots) Strategy receives update, emits actions
```

### Internal Event Interleaving

Internal events (ORDER_VISIBLE, CANCEL_EFFECTIVE) are scheduled for future timestamps and stored in a bisect-sorted priority queue. Before processing each external event, the loop drains all internal events with strictly earlier timestamps, setting `current_time_us` to the internal event's timestamp. This ensures events are processed in their actual chronological order.

### Snapshot Processing Flow (most common path)

When a BOOK_SNAPSHOT event arrives:

```
1. Update latest_snapshot_idx for (strike, token_side)
2. If snapshot-only mode:
   a. Get resting orders for this (strike, token_side)
   b. [C4 guard] Filter to orders visible BEFORE the previous snapshot
   c. Check fills against BBO movement
   d. Apply fills through OrderManager
3. Update prev_snapshot_ts for lookahead guard
4. If within market hours:
   a. Build StrategyUpdate with current book state + fair value + position
   b. Call strategy.on_market_update()
   c. Process returned actions (PLACE/CANCEL orders)
```

### Lookahead Guards

Three mechanisms prevent the engine from using future information:

1. **C4 — BBO-movement fill guard**: Orders must have been visible at or before the previous snapshot's timestamp to be eligible for BBO-movement fills. An order placed in response to snapshot T cannot fill on the T→T+1 movement — it must survive until at least T+1→T+2.

2. **C5 — Bar timestamp offset**: Underlying price bar timestamps are shifted forward by bar duration, so the Close price of the 10:00-10:01 bar arrives at 10:01, not 10:00.

3. **H2 — Decision-time queue position**: When an order becomes visible (ORDER_VISIBLE event), its queue position is assigned based on the book depth at the snapshot the strategy saw when making the decision, not the latest snapshot at visibility time.

---

## 5. Order Lifecycle

Orders follow a state machine managed by `OrderManager`:

```
PENDING_SUBMIT ──(submit_latency)──> ACTIVE ──(visible_latency)──> [visible in book]
       │                                │                                │
       v                                v                                v
   REJECTED                    PARTIALLY_FILLED ───> FILLED         PENDING_CANCEL
                                                                         │
                                                              (cancel_latency)
                                                                         v
                                                                    CANCELLED
                                                                    (or FILLED
                                                                     if race)
```

### Key Fields on SimOrder

| Field | Set When | Purpose |
|---|---|---|
| `decision_ts_us` | Strategy emits PLACE action | When the strategy decided to place the order |
| `submit_ts_us` | `decision_ts + submit_latency` | When the exchange receives the order |
| `visible_ts_us` | `submit_ts + visible_latency` | When the order appears in the book |
| `cancel_effective_ts_us` | `cancel_request_ts + cancel_latency` | When the cancel takes effect |
| `queue_ahead_cs` | ORDER_VISIBLE event fires | How many centishares are ahead in queue |
| `reserved_tc` | `_handle_place` in engine | Cash reserved for this order (for correct cancel release) |

### Latency Defaults

| Parameter | Default | Meaning |
|---|---|---|
| `submit_us` | 200,000 (200ms) | Network + exchange processing |
| `visible_us` | 800,000 (800ms) | Time for order to appear in book feed |
| `cancel_us` | 200,000 (200ms) | Cancel round-trip |

For POC replication, all latencies are set to 0 to match the original POC's instant-order model.

### Fill-During-Cancel Race

An order in `PENDING_CANCEL` state is still `is_live = True` and can receive fills. This models real exchange behavior where a cancel request is in-flight but a trade arrives first. When `CANCEL_EFFECTIVE` fires:
- If the order is already `FILLED` (remaining_cs == 0): skip reservation release
- If partially filled: release reservation proportional to unfilled portion: `reserved_tc * remaining_cs // size_cs`
- If unfilled: release full `reserved_tc`

---

## 6. Fill Simulation

### Snapshot-Only Mode (SnapshotFillEngine)

Used when only orderbook snapshot data is available (no trades channel). Three fill paths:

**Path 1 — Aggressive**: Order price crosses the current BBO.
- BUY at price >= best_ask → immediate fill, capped by `best_ask_size_cs`
- SELL at price <= best_bid → immediate fill, capped by `best_bid_size_cs`

**Path 2 — BBO Movement**: The BBO moved through the order price since the last snapshot.
- BUY: previous ask was above order price, current ask is at or below → fill, capped by depth at order price
- SELL: previous bid was below order price, current bid is at or above → fill
- **Guarded by C4**: order must have been visible before the previous snapshot

**Path 3 — Queue at BBO**: The order is resting at the BBO and depth decreased.
- Compute `consumed = prev_size - curr_size` at the BBO level
- Apply `cancel_discount` (default 0.5): `trade_proxy = consumed * (1 - cancel_discount)`
  - Not all depth decreases are trades — some are cancellations
- Drain `queue_ahead_cs` by `trade_proxy`
- Fill if `queue_ahead_cs` reaches 0
- **Queue drain is persistent**: accumulated across snapshots (M1 fix)
- Fill size capped by `trade_proxy`

### Trade-Driven Mode (TradeDrivenFillEngine)

Used when Telonex `trades` channel data is available. Implements the 7-condition fill model from the BTC engine.

**7 conditions** (all must be true for a fill):
1. Order is live (ACTIVE, PARTIALLY_FILLED, or PENDING_CANCEL)
2. Trade timestamp >= order's visible_ts_us
3. Cancel not yet effective (trade timestamp < cancel_effective_ts_us, or no cancel pending)
4. Trade price == order price (exact tick match)
5. Trade side compatible: taker BUY fills resting SELL, taker SELL fills resting BUY
6. Queue drain: `queue_ahead -= min(queue_ahead, remaining_trade_size)` from a shared pool
7. Fill if `queue_ahead == 0` and remaining trade size > 0

**Two phases per trade**:
- **Phase 1 (Queue Reduction)**: For each eligible order, drain queue_ahead from a shared pool. Deduct drained amount from the pool before moving to the next order. Stop when pool is empty.
- **Phase 2 (Fill Allocation)**: Orders with `queue_ahead == 0` fill from the remaining pool, in order. Each order takes `min(remaining_cs, pool_cs)`.

**Aggressive fill check**: When an ORDER_VISIBLE event fires, check if the order already crosses the BBO. If so, fill immediately (the order was marketable when it arrived).

### Queue Position Model

When an order becomes visible, it gets a queue position based on depth at its price level:

| Mode | queue_ahead_cs | Realism |
|---|---|---|
| CONSERVATIVE | All depth at price (back of queue) | Most realistic for latency 200-800ms |
| PROBABILISTIC | Uniform random fraction of depth | Sensitivity testing |
| OPTIMISTIC | 0 (front of queue) | Sanity check / upper bound |

Queue is drained only by trades (trade-driven mode) or by estimated trade volume from depth decreases (snapshot mode).

---

## 7. Portfolio Management

### Cash and Positions

```python
Portfolio:
  cash_tc: int                    # Current cash in tick-centishares
  positions: dict[int, StrikePosition]  # Per-strike positions
  mode: PositionMode              # COLLATERAL_BACKED or INVENTORY_BACKED
```

Each `StrikePosition` tracks `yes_position_cs` and `no_position_cs` separately (dual-token).

### Reservation System

When an order is placed, cash is reserved to ensure the portfolio can cover it:

| Action | Reservation (tc) |
|---|---|
| BUY YES/NO | `price_ticks * size_cs` |
| SELL YES/NO from inventory | 0 (already hold the token) |
| SELL YES/NO short (COLLATERAL_BACKED) | `(100 - price_ticks) * short_cs` |

The reserved amount is stored on `SimOrder.reserved_tc` so the correct amount is released on cancel, regardless of order side.

### Settlement

Binary markets resolve to either YES ($1.00 = 100 ticks) or NO ($0.00):

| Position | Resolved YES | Resolved NO |
|---|---|---|
| Long YES (+N cs) | cash += N * 100 tc | cash += 0 |
| Short YES (-N cs) | cash -= N * 100 tc (loss) | cash += 0 (keep sale proceeds) |
| Long NO (+N cs) | cash += 0 | cash += N * 100 tc |
| Short NO (-N cs) | cash += 0 (keep sale proceeds) | cash -= N * 100 tc (loss) |

---

## 8. Fair Value

### Black-Scholes Binary Call

$$V_{YES} = \Phi(d_2), \quad d_2 = \frac{\ln(S/K) + (r - \frac{1}{2}\sigma^2)\tau}{\sigma\sqrt{\tau}}$$

- S = underlying price (from `underlying_price_cents / 100`)
- K = strike price (integer dollars)
- tau = time to expiry in years (from microsecond timestamps)
- sigma = annualized IV (default 0.50)
- r = risk-free rate (default 0.0)

Output: integer basis points (0-10000). At expiry: 10000 if S > K, else 0.

### Monotonicity Enforcement

For strikes K1 < K2, P(S > K1) >= P(S > K2) must hold. If violated, the manager averages the violating pair using integer arithmetic: `avg = (v1 + v2) // 2`. Iterates until no violations remain.

### NO Token Fair Value

`FV_NO = 10000 - FV_YES` (complementary probability).

---

## 9. Strategy Interface

Strategies implement the `Strategy` Protocol:

```python
class Strategy(Protocol):
    def on_market_update(self, update: StrategyUpdate) -> list[StrategyAction]: ...
    def on_fill(self, strike, token_side, side, price_ticks, size_cs) -> list[StrategyAction]: ...
```

### StrategyUpdate — What the Strategy Sees

| Field | Type | Description |
|---|---|---|
| `timestamp_us` | int | Current time |
| `strike` | int | Which market |
| `token_side` | TokenSide | YES or NO |
| `best_bid_ticks` | int | Current best bid |
| `best_ask_ticks` | int | Current best ask |
| `best_bid_size_cs` | int | Depth at best bid |
| `best_ask_size_cs` | int | Depth at best ask |
| `mid_ticks_x2` | int | Double midpoint (avoid float) |
| `spread_ticks` | int | Ask - bid |
| `fair_value_bps` | int | B-S fair value for YES token |
| `underlying_price_cents` | int | Current underlying price |
| `position_yes_cs` | int | Current YES position |
| `position_no_cs` | int | Current NO position |
| `available_cash_tc` | int | Cash minus reservations |

The update deliberately **excludes**: full depth, historical data, resolution outcomes, other strikes' state. The strategy can only see what a live trader would see for one market at one moment.

### StrategyAction — What the Strategy Can Do

```python
StrategyAction(kind="PLACE", strike=165, token_side=YES, side=BUY, price_ticks=48, size_cs=1000)
StrategyAction(kind="CANCEL", order_id="ord_000042")
```

### Included Strategy: ProbabilityQuotingStrategy

Cancel-and-replace on every update:
1. Cancel all resting orders for this strike
2. Compute `fv_ticks = fair_value_bps / 100`
3. Compute `edge = |fv_ticks - poly_mid_ticks|`
4. If edge < `min_edge_ticks` (default 3): don't quote
5. Bid at `fv - half_spread_ticks`, ask at `fv + half_spread_ticks`
6. Clamp to [1, 99]
7. Size to `min(order_size_cs, max_position_cs - current_position_cs)`

---

## 10. Metrics and PnL

### 3-Component PnL Decomposition

```
Total PnL = Spread Capture + Inventory PnL + Settlement PnL
```

| Component | Meaning | How Computed |
|---|---|---|
| Spread capture | Revenue from bid-ask spread | Pro-rata: matched_sell_revenue - matched_buy_cost |
| Inventory PnL | Gain/loss from unmatched positions | Residual: total - spread - settlement |
| Settlement PnL | Terminal payoff at resolution | Sum of settle() returns |

### Key Metrics

- **Fill rate**: fills / orders placed
- **Per-strike breakdown**: fills, volume, cash flow, settlement, PnL by strike
- **Adverse selection**: negative spread capture indicates fills happening when price moves against us

### Healthy Ranges (from [[Performance-Metrics-and-Pitfalls]])

| Metric | Suspicious | Acceptable | Good |
|---|---|---|---|
| Fill rate | >50% | 5-15% | 15-30% |
| Adverse selection ratio | <20% | 50-70% | 70-85% |
| Sharpe (OOS) | >5.0 | 0.5-1.5 | 1.5-3.0 |
| Profit factor | >3.0 | 1.0-1.3 | 1.3-2.0 |

---

## 11. Configuration Reference

### Running a Backtest

```python
from bt_engine.config import EngineConfig, EventConfig, MarketConfig, FillConfig, LatencyConfig
from bt_engine.types import FillMode
from bt_engine.strategy.probability_quoting import ProbabilityQuotingStrategy
from bt_engine.runner import run_backtest

config = EngineConfig(
    event=EventConfig(
        event_slug="nvda-close-above-on-march-30-2026",
        ticker="NVDA",
        expiry_utc_us=1743364800000000,   # 2026-03-30 20:00 UTC
        markets=(
            MarketConfig(strike=160, resolution=True),
            MarketConfig(strike=165, resolution=True),
            MarketConfig(strike=170, resolution=False),
        ),
    ),
    data_dir=Path("data/telonex/nvda-poc"),
    underlying_price_file=Path("data/nvda_prices_1m.parquet"),
    fill=FillConfig(mode=FillMode.SNAPSHOT_ONLY),
    latency=LatencyConfig(submit_us=200_000, visible_us=800_000, cancel_us=200_000),
    sigma=0.50,
    initial_cash_tc=100_000_000,  # $10,000
)

strategy = ProbabilityQuotingStrategy(
    half_spread_ticks=2,    # 2 cents each side
    max_position_cs=5000,   # 50 shares max
    min_edge_ticks=3,       # 3 cent minimum edge to quote
    order_size_cs=1000,     # 10 shares per order
)

result = run_backtest(config, strategy)
```

### Key Config Knobs

| Parameter | Effect When Changed |
|---|---|
| `fill.cancel_discount` | Higher = fewer fills in snapshot mode (more conservative) |
| `fill.queue_mode` | CONSERVATIVE vs PROBABILISTIC changes fill rate significantly |
| `latency.visible_us` | Higher = orders take longer to enter book, fewer fills |
| `sigma` | Higher IV = wider fair value range, more quoting opportunities |
| `only_market_hours` | False = strategy runs 24/7 (Polymarket trades 24/7 but FV unreliable without stock market) |
| `underlying_bar_duration_us` | 0 for tick data, 60M for 1-min bars, 300M for 5-min bars |

---

## 12. Audit Fixes Applied

The engine underwent a 3-auditor review (fill simulation, lookahead bias, PnL accuracy). All 15 issues were fixed:

### Critical Fixes

| ID | Issue | Fix |
|---|---|---|
| C1 | Trade-driven fills didn't update SimOrder state | Both fill paths now use `OrderManager.apply_fill()` |
| C2 | Cancel reservation wrong formula for SELL | Store `reserved_tc` on SimOrder, release proportionally |
| C3 | Spread capture integer division truncation | Pro-rata calculation without intermediate division |
| C4 | Zero-latency snapshot fill = free option | Orders must be visible at or before previous snapshot |
| C5 | OHLCV Close price timestamped at bar-open | Shift timestamps forward by `bar_duration_us` |

### High Fixes

| ID | Issue | Fix |
|---|---|---|
| H2 | Queue position from future snapshot | Use decision-time snapshot, not latest |
| H3 | Trade-driven queue drain too aggressive | Shared drain pool, deduct before next order |
| H4 | Snapshot fills always fill entire order | Cap by available liquidity at price level |
| H5 | Fill-during-cancel double-releases reservation | Skip release for already-filled orders, proportional for partial |

### Medium Fixes

| ID | Issue | Fix |
|---|---|---|
| M1 | Queue drain not persistent across snapshots | Write back `order.queue_ahead_cs` after each drain |
| M2 | Internal events at wrong timestamp | Interleave internal and external by actual timestamp |
| M3 | Settlement journal logs zeroed positions | Capture positions before calling settle() |
| M4 | PnLComponents dead code | Removed, placeholder docstring |
| M5 | available_cash_tc can go negative | Warning on negative cash (non-blocking) |

---

## 13. NVDA POC Validation Results

Ran on NVDA March 30, 2026 close-above event (5 strikes: $160-$180), `book_snapshot_25` data (YES only), 1-minute NVDA bar prices.

| Metric | Original POC | New Engine (post-audit) |
|---|---|---|
| Total fills | 188 | 266 |
| Strike 160 fills | 0 | 5 |
| Strike 165 fills | 163 | 178 |
| Strike 170 fills | 25 | 83 |
| Strike 175/180 fills | 0 | 0 |
| Total PnL | -$18.10 | -$39.80 |
| Settlement PnL | -$50.00 | $0.00 (net: -50 + 50) |
| Spread capture | — | -$60.43 |
| Engine time | ~30s | 117s |

**Key observations**:
- More fills than POC: persistent queue drain (M1) accumulates across snapshots
- More conservative PnL: bar-offset fix (C5) and lookahead guard (C4) eliminated artificial edge
- Negative spread capture confirms adverse selection dominates (as expected from [[NVDA-POC-Results]])
- Same strike distribution pattern: $165 ATM dominates, $170 secondary, deep ITM/OTM get few/no fills

---

## 14. What's Not Yet Implemented

| Feature | Status | Needed When |
|---|---|---|
| Trade-driven mode (with real trades data) | Code written, untested | Telonex data download complete |
| Dual-book (YES + NO tokens) | Supported in data model, strategy only quotes YES | Same |
| Breeden-Litzenberger pricer | Interface defined, not implemented | ThetaData options data available |
| Walk-forward validation | Not started | Strategy optimization phase |
| Multi-event concurrent simulation | Data model supports it, engine runs one event | Multiple events downloaded |
| Determinism tests | Not written | Before any parameter optimization |
| Performance optimization | 117s for 140K events | Before scaling to full dataset |
| 5-component PnL decomposition | Placeholder | Trade data for post-fill price analysis |
| `fair_value_staleness_us` usage | Config field exists, not wired into loop | Testing propagation delay sensitivity |
