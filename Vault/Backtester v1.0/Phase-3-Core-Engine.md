---
title: "Phase 3: Core Backtesting Engine"
created: 2026-04-02
status: plan
phase: 3
milestone: "Backtester v1.0"
tags:
  - backtesting
  - engine
  - event-loop
  - fill-simulation
  - order-management
  - queue-position
  - deterministic
  - market-making
  - polymarket
depends-on:
  - "Phase 2: Data Alignment & DataProvider"
related:
  - "[[Engine-Architecture-Plan]]"
  - "[[Fill-Simulation-Research]]"
  - "[[NVDA-POC-Results]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[Polymarket-CLOB-Mechanics]]"
  - "[[Data-Alignment-Architecture]]"
---

# Phase 3: Core Backtesting Engine

Build the event-processing engine that consumes Phase 2's aligned DataProvider output and simulates market making on Polymarket stock/index binary event markets with realistic fill behavior. This is the heart of Backtester v1.0 -- every design decision here directly impacts whether backtest results are trustworthy or fiction.

> [!important] The $639 Lesson
> The NVDA POC demonstrated that fill simulation is the single most critical component. The midpoint simulator overstated P&L by **$638.80** and fills by **16.5x** compared to the L2 simulator. Phase 3 must get this right. See [[NVDA-POC-Results]].

---

## Engine Architecture Diagram

```
                         Phase 2 DataProvider
                                |
                  +-------------+-------------+
                  |                           |
           Market Timeline              Internal Queue
           (pre-sorted)               (bisect-inserted)
           [UNDERLYING_PRICE]          [ORDER_VISIBLE]
           [BOOK_SNAPSHOT]             [CANCEL_EFFECTIVE]
           [TRADE]                     [TIMER]
                  |                           |
                  +------+--------------------+
                         |
                    Event Loop
                   (5-phase per timestamp)
                         |
         +-------+-------+-------+-------+
         |       |       |       |       |
      Phase 1  Phase 2  Phase 3  Phase 4  Phase 5
      External Internal  Fill    Fair    Strategy
       Data    Events   Checks  Value   Delivery
         |       |       |       |       |
         v       v       v       v       v
    +--------+ +-----+ +------+ +----+ +--------+
    |Dual-Book| |Order| | Fill | | FV | |Strategy|
    |  State  | | Mgr | |Engine| |Mgr | |Protocol|
    +--------+ +-----+ +------+ +----+ +--------+
         |       |       |               |
         |       +---+---+               |
         |           |                   |
         |      +---------+              |
         |      |Portfolio|<-------------+
         |      +---------+
         |           |
         |      +---------+
         +----->| Audit   |
                | Journal |
                +---------+

    Data Flow:
    DataProvider --> Event Loop --> [Book State, Order Mgr, Fill Engine,
                                    Fair Value, Portfolio] --> Strategy
    Strategy --> [PLACE, CANCEL actions] --> Order Mgr --> Internal Queue
    Fill Engine --> Portfolio --> Audit Journal
```

**Component ownership boundaries** (strict -- no cross-layer mutation):

| Component | Owns | Never Touches |
|-----------|------|---------------|
| Dual-Book State | Snapshot storage, latest book per (strike, token_side), parity checks | Order state, positions, cash |
| Order Manager | Order lifecycle, latency scheduling, status transitions | Book state, portfolio cash |
| Fill Engine | Fill condition checks, queue drain, fill generation | Book mutation, order creation |
| Portfolio | Cash, positions, reservations, settlement | Order state, book state |
| Strategy | Action generation (PLACE, CANCEL) | Anything -- read-only view of state |

---

## 1. Event Loop Specification

### 1.1 Overview

The event loop is the engine's master clock. It merges two event streams -- the pre-sorted market timeline from DataProvider and dynamically-inserted internal events -- into a single chronological sequence. At each timestamp, it executes five phases in strict order.

This design is adapted from the BTC engine's 5-phase model (see [[Engine-Architecture-Plan]] Section 3) and proven in the production engine at `bt_engine/engine/loop.py`.

### 1.2 Event Stream Merging

```
Market Timeline (immutable, from DataProvider):
  [UNDERLYING_PRICE@T1, BOOK_SNAPSHOT@T2, TRADE@T3, BOOK_SNAPSHOT@T4, ...]

Internal Queue (dynamic, from engine actions):
  [ORDER_VISIBLE@T2+500ms, CANCEL_EFFECTIVE@T3+200ms, TIMER@T5, ...]

Merged processing order:
  For each external event E at timestamp T:
    1. Pop and process all internal events with timestamp < T
    2. Set current_time = T
    3. Process E (Phase 1)
    4. Pop and process all internal events with timestamp == T (Phase 2)
    5. Continue to Phases 3-5
```

The `InternalEventQueue` uses `bisect.insort` for O(log n) insertion, matching the existing production implementation. Internal events are `(timestamp_us, kind_priority, sequence)` tuples for deterministic ordering.

### 1.3 Per-Timestamp Processing (5 Phases)

```
For each timestamp T in the merged event stream:

  PHASE 1: External Market Data
  ─────────────────────────────
  Process ALL external events at timestamp T in kind-priority order:

    Priority 0: UNDERLYING_PRICE
      - Update latest_underlying_cents
      - Trigger fair value recomputation for all strikes

    Priority 1: OPTIONS_CHAIN (future -- B-L integration)
      - Update options-implied probabilities

    Priority 2: BOOK_SNAPSHOT
      - Replace authoritative book state for (strike, token_side)
      - YES and NO books are INDEPENDENT updates
      - Update cross-leg parity metrics
      - Snapshot-mode fill check (fallback mode only)

    Priority 3: TRADE
      - >>> TRADES TRIGGER FILL CHECKS <<<
      - For each trade, check all resting orders on the SAME
        (strike, token_side) against the 7 fill conditions
      - This is the core architectural principle from the BTC engine

  PHASE 2: Internal Simulator Events
  ───────────────────────────────────
  Process all internal events scheduled at or before T:

    ORDER_VISIBLE: PENDING_SUBMIT -> ACTIVE
      - Read depth at order's price from current snapshot
      - Assign queue position via QueuePositionModel
      - Check for aggressive fill (order crosses BBO at visibility)

    CANCEL_EFFECTIVE: PENDING_CANCEL -> CANCELLED
      - Release cash reservation (proportional to unfilled portion)
      - Order removed from active tracking

    TIMER: periodic strategy callbacks
      - Fair value refresh, inventory check, position decay

  Sorted by (scheduled_ts_us, kind_priority, submission_sequence)

  PHASE 3: Expiration Checks
  ──────────────────────────
  Orders past their expire_ts -> EXPIRED
  Release associated reservations

  PHASE 4: Fair Value Computation
  ───────────────────────────────
  - Recompute fair values if underlying price changed this timestamp
  - Enforce strike monotonicity (P(S > K1) >= P(S > K2) when K1 < K2)
  - Record fair values for analytics

  PHASE 5: Strategy Delivery + Action Processing
  ───────────────────────────────────────────────
  - Deliver StrategyUpdate for each (strike, token_side) with changes
  - Collect strategy actions (PLACE, CANCEL)
  - Validate actions against safety checks:
      a. Market hours check
      b. Tick grid check (price_ticks in 1..99, on grid)
      c. Capital check (sufficient available cash)
  - Schedule internal events for accepted actions
```

### 1.4 Timer Events

Timer events are a Phase 3 addition not present in the POC. They support periodic callbacks that do not depend on market data arriving:

| Timer Type | Interval | Purpose |
|-----------|----------|---------|
| `FAIR_VALUE_REFRESH` | 60s | Recompute FV even without new underlying price (time decay) |
| `INVENTORY_CHECK` | 30s | Strategy can check aggregate inventory across strikes |
| `POSITION_DECAY` | Configurable | Reduce max position as expiry approaches |

Timer events are pre-computed at engine initialization and inserted into the internal queue. They are processed in Phase 2 alongside ORDER_VISIBLE and CANCEL_EFFECTIVE events.

### 1.5 Determinism Guarantee

The event loop is deterministic: identical inputs and configuration always produce bit-identical results. This is enforced by:

1. **Pre-sorted timeline**: Market events sorted by `(timestamp_us, kind_priority, sequence)` at load time
2. **Deterministic internal queue**: `bisect.insort` with monotonic sequence tiebreaker
3. **Seeded RNG**: `QueuePositionModel` uses `np.random.RandomState(seed)` -- not global random
4. **Integer arithmetic**: All prices in ticks, sizes in centishares, cash in tick-centishares
5. **No wall-clock dependencies**: Engine time is purely event-driven
6. **Stable sort within same timestamp**: Kind-priority then sequence number

---

## 2. Dual-Book State Specification

### 2.1 Two Independent Orderbooks Per Market

Each Polymarket binary market (e.g., "NVDA > $165") has two independent orderbooks: one for the YES token and one for the NO token. This is not a derived relationship -- they are genuinely separate CLOB markets with independent bid/ask ladders.

```
Strike $165:
  YES Token Book                    NO Token Book
  ┌─────────────────────┐          ┌─────────────────────┐
  │ Bids        Asks    │          │ Bids        Asks    │
  │ 0.48 (200)  0.52 (150)│        │ 0.47 (180)  0.53 (120)│
  │ 0.47 (350)  0.53 (200)│        │ 0.46 (250)  0.54 (180)│
  │ 0.46 (500)  0.54 (100)│        │ 0.45 (400)  0.55 (200)│
  └─────────────────────┘          └─────────────────────┘

  Cross-leg parity check:
    YES_best_ask + NO_best_ask = 0.52 + 0.53 = 1.05 >= 1.00  (valid)
    YES_best_bid + NO_best_bid = 0.48 + 0.47 = 0.95 <= 1.00  (valid)
```

### 2.2 Book State Storage

The engine maintains a latest-snapshot index per `(strike, token_side)`. Each `BookSnapshot` is a lightweight view into pre-allocated numpy arrays (as implemented in `bt_engine/data/schema.py`):

```python
# Existing production implementation -- reuse directly
class BookSnapshot:
    """Lightweight view into book data arrays."""
    # Properties: best_bid_ticks, best_ask_ticks, best_bid_size_cs,
    #   best_ask_size_cs, mid_ticks_x2, spread_ticks, is_valid
    # Methods: depth_at_price(price_ticks), total_bid_depth_cs(),
    #   total_ask_depth_cs()
```

For 5 strikes x 2 tokens = 10 book states tracked simultaneously. The `DataStore._latest_snapshot_idx` dict maps `(strike, TokenSide) -> int` for O(1) lookup.

### 2.3 Cross-Leg Parity Monitor

Parity violations indicate data quality issues or exploitable arbitrage. The engine checks parity after every snapshot update and logs violations:

```
Invariant: YES_best_ask + NO_best_ask >= 100 ticks (sum >= $1.00)
Invariant: YES_best_bid + NO_best_bid <= 100 ticks (sum <= $1.00)

Violation categories:
  MINOR: sum off by 1 tick (likely timing -- books not perfectly synced)
  MAJOR: sum off by 2+ ticks (possible data issue or real arbitrage)
  STALE: one side has no valid BBO (book gap)
```

Parity checks are informational and logged to the audit journal. They do not block engine processing -- the engine operates on whatever data it receives.

---

## 3. Order Management Specification

### 3.1 Order State Machine

```
                    ┌──────────┐
                    │ Strategy │
                    │ submits  │
                    └────┬─────┘
                         │
                    ┌────v─────┐
                    │PENDING_  │  validate: tick grid, capital, market hours
              ┌─────│ SUBMIT   │─────┐
              │     └────┬─────┘     │
              │          │           │
         (fails)    (latency)   (zero latency)
              │      elapsed     shortcut
              │          │           │
         ┌────v──┐  ┌───v────┐      │
         │REJECTED│  │ ACTIVE │<─────┘
         └────────┘  └───┬────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
         (partial)  (cancel req) (full fill)
              │          │          │
         ┌────v──────┐ ┌─v────────┐│
         │PARTIALLY_ │ │PENDING_  ││
         │  FILLED   │ │ CANCEL   ││
         └────┬──────┘ └──┬───────┘│
              │           │        │
              │    ┌──────┼────┐   │
              │    │      │    │   │
              │ (effective)(race)  │
              │    │      │    │   │
              │ ┌──v──┐ ┌─v──┐│   │
              │ │CANC.│ │FILL││   │
              │ └─────┘ │    ││   │
              │         └─┬──┘│   │
              │           │   │   │
              └───────────┴───┴───┘
                          │
                     ┌────v────┐
                     │ FILLED  │
                     └─────────┘
```

Key transitions:
- **PENDING_SUBMIT -> ACTIVE**: At `visible_ts_us = decision_ts + submit_latency + visible_latency`. Queue position assigned here.
- **ACTIVE -> PENDING_CANCEL**: Cancel requested. Order can still fill during the cancel window (race condition).
- **PENDING_CANCEL -> FILLED**: Race condition fill -- real Polymarket behavior. Cancel was in flight but a trade matched first.
- **PENDING_CANCEL -> CANCELLED**: Cancel took effect at `cancel_effective_ts_us`. Reservation released.

### 3.2 SimOrder Data Structure

Reuse the production `SimOrder` from `bt_engine/execution/order.py` directly:

```python
@dataclass
class SimOrder:
    order_id: str                   # "ord_000042"
    strike: int
    token_side: TokenSide           # YES or NO
    side: Side                      # BUY or SELL
    price_ticks: int                # 1..99 (integer ticks, 1 tick = $0.01)
    size_cs: int                    # centishares, original size
    remaining_cs: int               # decreases on partial fills
    status: OrderStatus
    decision_ts_us: int             # when strategy decided
    submit_ts_us: int               # decision + submit latency
    visible_ts_us: int              # submit + visible latency
    cancel_request_ts_us: int = 0
    cancel_effective_ts_us: int = 0
    queue_ahead_cs: int = 0         # centishares ahead in queue
    reserved_tc: int = 0            # tick-centishares reserved for this order
```

### 3.3 Latency Model

Latency prevents look-ahead bias. A strategy decision at time T cannot affect the book until T + latency.

```
Timeline:
  T          T + submit_us    T + submit_us + visible_us
  |               |                    |
  Strategy      Order enters          Order visible in
  decides       exchange              book (queue assigned)
  (PLACE)       (ACTIVE)              (ORDER_VISIBLE)

  T             T + cancel_us
  |               |
  Strategy      Cancel takes
  decides       effect
  (CANCEL)      (CANCEL_EFFECTIVE)
```

**Configuration** (from `bt_engine/config.py`, keep as-is):

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `submit_us` | 200,000 (200ms) | Infrastructure latency to Polymarket |
| `visible_us` | 800,000 (800ms) | Exchange processing until order rests in book |
| `cancel_us` | 200,000 (200ms) | Cancel propagation delay |

Future extension: EMPIRICAL mode sampling from observed Polymarket latency distributions, and BUCKETED mode varying by time of day. The `LatencyConfig` in [[Engine-Architecture-Plan]] Section 4.4 specifies these modes.

### 3.4 Integer Arithmetic

All internal arithmetic uses integers to prevent floating-point drift. The boundary conversion happens once at data load time.

| Domain | Unit | Symbol | Examples |
|--------|------|--------|----------|
| Price | ticks | `_ticks` | 48 ticks = $0.48 |
| Size | centishares | `_cs` | 1000 cs = 10.00 shares |
| Cash | tick-centishares | `_tc` | 48,000 tc = 48 ticks * 1000 cs = $4.80 |
| Underlying | cents | `_cents` | 16506 cents = $165.06 |
| Fair value | basis points | `_bps` | 5000 bps = 50.00% probability |

Conversion functions from `bt_engine/units.py` (reuse directly):
- `price_str_to_ticks("0.48")` -> 48
- `size_str_to_cs("219.22")` -> 21922
- `tc_to_dollars(48000)` -> 4.80
- `probability_to_bps(0.50)` -> 5000

### 3.5 Batch Order Support

Polymarket supports batching up to 15 orders per API call. The engine models this as:

- All orders in a batch share the same `decision_ts_us`
- Each order gets an independent `visible_ts_us` (same latency, but unique sequence numbers)
- The `OrderManager` processes them in submission order (FIFO via monotonic `_next_id`)
- Capital reservation is checked per-order within the batch -- later orders may be rejected if earlier ones consumed available capital

---

## 4. Fill Simulation Specification

### 4.1 Design Principle

> Only trades trigger fills. Book snapshots never generate fills.

This is the BTC engine's core fill principle, validated by the NVDA POC: the midpoint simulator (which inferred fills from book state) overstated fills by 16.5x. The trade-driven approach eliminates phantom fills entirely.

The engine supports two fill modes:
1. **TRADE_DRIVEN** (production): Actual trade events trigger the 7-condition fill check
2. **SNAPSHOT_ONLY** (fallback): BBO movement infers fills when trade data unavailable

### 4.2 The 7 Fill Conditions

All seven must be true for a fill to occur. Directly from [[Engine-Architecture-Plan]] Section 6.2:

| # | Condition | Code | What It Prevents |
|---|-----------|------|------------------|
| 1 | Order is live | `order.is_live` (ACTIVE, PARTIALLY_FILLED, PENDING_CANCEL) | Fills on dead orders |
| 2 | Trade after visibility | `trade.timestamp_us >= order.visible_ts_us` | Look-ahead fills |
| 3 | Cancel not effective | `trade.timestamp_us < order.cancel_effective_ts_us` (or no cancel) | Post-cancel fills |
| 4 | Price match | `trade.price_ticks == order.price_ticks` | Wrong-price fills |
| 5 | Side compatible | BUY taker fills SELL resting; SELL taker fills BUY resting | Same-side fills |
| 6 | Queue drained | `order.queue_ahead_cs == 0` after drain | Queue-skipping |
| 7 | Sufficient size | `fill_cs = min(order.remaining_cs, passthrough_cs)` > 0 | Overfilling |

### 4.3 Two-Phase Fill Processing

Per the production `TradeDrivenFillEngine` at `bt_engine/execution/fill_engine.py`:

```
For each trade event:

  Phase 1: Queue Reduction
  ────────────────────────
  For each eligible order (conditions 1-5 pass):
    drained = min(order.queue_ahead_cs, remaining_trade_size)
    order.queue_ahead_cs -= drained
    remaining_trade_size -= drained

  Phase 2: Fill Allocation
  ────────────────────────
  For each eligible order with queue_ahead_cs == 0:
    fill_cs = min(order.remaining_cs, remaining_pool)
    if fill_cs > 0:
      emit Fill event
      remaining_pool -= fill_cs
```

**Why two phases?** Multiple simulated orders can rest at the same price level. Phase 1 drains all queues from a shared trade volume pool. Phase 2 allocates remaining volume to orders that reached the front. This prevents double-counting trade volume.

### 4.4 Queue Position Model

When an order becomes visible (ORDER_VISIBLE internal event), the engine assigns its queue position using depth from the current book snapshot.

**Three modes** (from `bt_engine/execution/queue_position.py`, reuse directly):

| Mode | Formula | Use Case |
|------|---------|----------|
| CONSERVATIVE (default) | `queue_ahead = depth_at_price_cs` | Production. All existing depth is ahead. |
| PROBABILISTIC | `queue_ahead = depth * uniform(0, 1)` | Sensitivity analysis. Seeded RNG. |
| OPTIMISTIC | `queue_ahead = 0` | Sanity check only. |

**Queue drain rules:**
- Trades at our price level drain `queue_ahead` (condition 6)
- Cancellations do NOT improve queue position (conservative assumption, matches BTC engine)
- New orders placed behind us do NOT affect our position

### 4.5 Trade-Driven Queue Drain Formula

```
Given: trade at price P with size S on token T (taker side = BUY or SELL)

For each resting order O on token T:
  if O.price_ticks != P:
    skip (wrong price level)
  if not side_compatible(trade.taker_side, O.side):
    skip (same side)

  # Drain from shared pool
  drain_amount = min(O.queue_ahead_cs, remaining_pool_cs)
  O.queue_ahead_cs -= drain_amount
  remaining_pool_cs -= drain_amount

  # Fill if queue drained
  if O.queue_ahead_cs == 0 and remaining_pool_cs > 0:
    fill_cs = min(O.remaining_cs, remaining_pool_cs)
    emit_fill(O, fill_cs)
    remaining_pool_cs -= fill_cs
```

### 4.6 Aggressive Fill Detection

An order that crosses the spread at visibility (e.g., BUY at $0.52 when best ask is $0.50) fills immediately:

```
On ORDER_VISIBLE:
  snapshot = latest_snapshot(order.strike, order.token_side)
  if order.side == BUY and order.price_ticks >= snapshot.best_ask_ticks:
    fill(order, order.remaining_cs, snapshot.timestamp_us, aggressive=True)
  elif order.side == SELL and order.price_ticks <= snapshot.best_bid_ticks:
    fill(order, order.remaining_cs, snapshot.timestamp_us, aggressive=True)
```

### 4.7 Snapshot-Only Fallback (SNAPSHOT_ONLY Mode)

When trade data is unavailable, fills are inferred from BBO movement between consecutive snapshots. This is implemented in `bt_engine/execution/fill_engine_snapshot.py` with three paths:

1. **Aggressive**: Order price crosses current BBO
2. **BBO movement**: Price moved through our order since last snapshot (e.g., prev_ask > our_bid >= curr_ask)
3. **Queue at BBO**: Depth decreased at our price level, with `cancel_discount` (default 0.5) to account for cancellations vs trades

**Lookahead guard** (C4 from production engine): An order placed at snapshot T is NOT eligible for the T->T+1 BBO movement check. It becomes eligible starting at T+1->T+2. This prevents the "free option" where placing an order and immediately benefiting from the next price movement creates look-ahead bias.

### 4.8 Adverse Selection Tracking

Every fill records the book state before and after, enabling post-hoc adverse selection analysis:

```python
@dataclass(frozen=True)
class FillContext:
    """Captured at fill time for adverse selection analysis."""
    fill: Fill
    book_before: BookSnapshot        # Last snapshot before the fill
    book_after: BookSnapshot | None  # Next snapshot after the fill (set post-hoc)
    mid_before_ticks_x2: int
    mid_after_ticks_x2: int | None
    spread_before_ticks: int
    depth_imbalance_before: int      # bid_depth - ask_depth at top 5 levels
```

Adverse selection metrics computed in analytics:
- **Immediate adverse movement**: `mid_after - mid_before` in the direction against our fill
- **Toxicity score**: Spread widening + depth depletion after fill
- **Fill-to-mid slippage**: Difference between fill price and contemporaneous midpoint

### 4.9 Improvements Over POC Fill Simulation

| Aspect | POC (`fill_simulator.py`) | Phase 3 Engine |
|--------|--------------------------|----------------|
| Fill trigger | BBO movement between snapshots | Actual trade events (TRADE_DRIVEN) |
| Queue model | Binary: "crosses spread or not" | Explicit queue_ahead_cs with trade-driven drain |
| Queue assignment | None (no queue tracking) | Depth at price from book_snapshot_full |
| Partial fills | Not supported | Supported via remaining_cs tracking |
| Latency | None (instant fills) | Configurable submit + visible latency |
| Cancel race | Not modeled | PENDING_CANCEL window with race condition fills |
| Arithmetic | Float throughout | Integer ticks/centishares/tick-centishares |
| Adverse selection | Not tracked | Full book context captured per fill |
| Aggressive detection | Implicit in BBO check | Explicit path at ORDER_VISIBLE |

### 4.10 Fill Probability Calibration (Future)

With sufficient backtest data, we can calibrate the queue model against observed Polymarket fill rates. The `PROBABILISTIC` queue mode with adjustable distribution (uniform, beta, empirical) provides the knob. This is a Phase 5 (Analytics) concern, not Phase 3.

---

## 5. Multi-Market Specification

### 5.1 Multiple Markets Per Underlying

A single event (e.g., "NVDA close on 2026-03-30") generates 5-10 simultaneous binary markets at different strikes. The engine processes all of them in a single run:

```
Event: "Will NVDA close above $X on March 30?"

  Strike $160: YES/NO books (deep ITM, mid ~0.95)
  Strike $165: YES/NO books (near ATM, mid ~0.71)  <-- most active
  Strike $170: YES/NO books (slightly OTM, mid ~0.24)
  Strike $175: YES/NO books (deep OTM, mid ~0.015)
  Strike $180: YES/NO books (very deep OTM, mid ~0.005)

  Total books: 5 strikes x 2 tokens = 10 orderbooks
  Total data channels: 10 books + 10 trade streams = 20 Telonex downloads
```

### 5.2 Cross-Strike Position Tracking

The `Portfolio` class (from `bt_engine/portfolio/positions.py`) tracks positions per strike with independent YES and NO token positions:

```python
# Existing production structure -- reuse
positions: dict[int, StrikePosition]  # strike -> StrikePosition
# StrikePosition: yes_position_cs, no_position_cs, reserved_cash_tc
```

Cross-strike awareness is exposed to the strategy via the `StrategyUpdate`:
- `position_yes_cs`: This strike's YES position
- `position_no_cs`: This strike's NO position
- `available_cash_tc`: Portfolio-level available cash (shared across all strikes)

### 5.3 Strike Monotonicity

Fair values must satisfy: `P(S > K1) >= P(S > K2)` when `K1 < K2`. The `FairValueManager` enforces this after every recomputation. Violations indicate model error, not market opportunity.

```
Example (valid):
  FV($160) = 0.92 >= FV($165) = 0.55 >= FV($170) = 0.22 >= FV($175) = 0.04

Enforcement: isotonic regression (max-min smoothing)
  If FV($165) > FV($160), set FV($165) = FV($160)
  Sweep from lowest strike to highest, clamping violations
```

### 5.4 Cross-Strike Risk

The strategy needs aggregate risk metrics beyond per-strike positions:

| Metric | Formula | Meaning |
|--------|---------|---------|
| Total YES exposure | `sum(pos.yes_position_cs for pos in positions.values())` | Net long probability-weighted exposure |
| Total NO exposure | `sum(pos.no_position_cs for pos in positions.values())` | Net short probability-weighted exposure |
| Aggregate delta (cs) | `sum(fv_bps * pos.yes_cs - (10000 - fv_bps) * pos.no_cs)` | Directional exposure to underlying |
| Capital utilization | `(initial - available) / initial` | Fraction of capital deployed |
| Boxed positions | `min(pos.yes_cs, pos.no_cs)` per strike | Risk-free locked capital (redeemable for $1) |

These are computed in the strategy layer, not the engine -- the engine provides the raw position data.

---

## 6. Settlement and Resolution

### 6.1 Resolution Logic

At market expiry, each strike resolves based on the underlying's closing price:

```
Resolution rule:
  If underlying_close > strike:  YES wins (YES -> $1.00, NO -> $0.00)
  If underlying_close <= strike: NO wins  (YES -> $0.00, NO -> $1.00)

Settlement per strike:
  YES position: yes_cs * (100 if resolved_yes else 0) tick-centishares
  NO position:  no_cs  * (100 if not resolved_yes else 0) tick-centishares
```

### 6.2 Batch Settlement

All strikes on the same event resolve simultaneously at the equity close (4:00 PM ET). The engine settles all strikes in a single pass:

```python
# From bt_engine/portfolio/positions.py -- reuse directly
def settle(self, strike: int, resolved_yes: bool) -> int:
    """Settle a strike. Returns settlement PnL in tc."""
    # YES token: resolved_yes -> 100 tc/cs; else 0
    # NO token: not resolved_yes -> 100 tc/cs; else 0
    # Short positions are liabilities (negative pnl)
    # Release all remaining reservations
```

### 6.3 Post-Settlement Reconciliation

After all strikes are settled, verify portfolio invariants:

```
Invariant 1: All positions are zero (yes_cs == 0, no_cs == 0 for all strikes)
Invariant 2: All reservations are zero (reserved_cash_tc == 0 for all strikes)
Invariant 3: final_cash = initial_cash + sum(trading_cashflows) - fees + settlement_payouts
```

The `SettlementEngine.check_reconciliation()` from `bt_engine/portfolio/settlement.py` verifies these.

---

## 7. Determinism Guarantees

### 7.1 What Makes the Engine Deterministic

| Property | Mechanism | Location |
|----------|-----------|----------|
| Event ordering | Pre-sorted timeline with `(timestamp_us, kind_priority, sequence)` | `DataStore.timeline.sort()` |
| Internal event ordering | `bisect.insort` with monotonic sequence counter | `InternalEventQueue` |
| Queue position (PROBABILISTIC) | `np.random.RandomState(seed)` -- not global RNG | `QueuePositionModel.__init__` |
| Integer arithmetic | All prices/sizes/cash in integer units | `bt_engine/units.py` |
| No wall-clock | Engine time driven purely by event timestamps | `BacktestEngine.current_time_us` |
| Stable fill ordering | Orders checked in submission sequence (monotonic `_next_id`) | `OrderManager._next_id` |
| Deterministic iteration | `dict` insertion order (Python 3.7+) | All `dict` usage |

### 7.2 What Could Break Determinism

| Risk | Impact | Prevention |
|------|--------|------------|
| Using `random` module without seed | Different queue positions per run | Always use `self._rng`, never `random.random()` |
| Floating-point arithmetic | Rounding differences across platforms | All internal arithmetic is integer |
| Dict iteration order changing | Different processing order | Python 3.7+ guarantees insertion order |
| Set iteration order | Non-deterministic ordering | Never use sets for order processing |
| Thread-unsafe global state | Race conditions | Engine is single-threaded by design |
| Different numpy versions | Different RNG sequences | Pin numpy version in requirements |
| Timestamp ties without tiebreaker | Ambiguous processing order | `sequence` field on all events |

### 7.3 Verification Protocol

Run the engine twice with identical inputs and config. Assert:
- Same number of fills
- Same fill timestamps, prices, sizes
- Same final cash
- Same settlement PnL per strike
- Bit-identical audit journal

---

## 8. What We Keep From Existing Code

### 8.1 Reuse Directly (No Changes)

These production modules are battle-tested and transfer without modification:

| Module | Path | Rationale |
|--------|------|-----------|
| `types.py` | `bt_engine/types.py` | All enums (TokenSide, EventKind, Side, OrderStatus, QueueMode, FillMode, PositionMode) |
| `units.py` | `bt_engine/units.py` | Integer conversion boundary functions |
| `queue_position.py` | `bt_engine/execution/queue_position.py` | 3-mode queue model with seeded RNG |
| `fill_engine.py` | `bt_engine/execution/fill_engine.py` | 7-condition trade-driven fill engine with 2-phase processing |
| `fill_engine_snapshot.py` | `bt_engine/execution/fill_engine_snapshot.py` | Snapshot-only fallback with cancel discount and lookahead guard |
| `order.py` | `bt_engine/execution/order.py` | SimOrder, Fill, OrderManager (lifecycle, latency, cancel) |
| `latency.py` | `bt_engine/execution/latency.py` | CONSTANT mode latency model |
| `settlement.py` | `bt_engine/portfolio/settlement.py` | Settlement engine with reconciliation check |
| `positions.py` | `bt_engine/portfolio/positions.py` | Portfolio with dual-token positions, collateral, reservation |
| `internal_queue.py` | `bt_engine/engine/internal_queue.py` | bisect-based internal event scheduling |
| `schema.py` | `bt_engine/data/schema.py` | BookSnapshot (numpy view), TradeEvent, UnderlyingPrice, TimelineEvent |
| `store.py` | `bt_engine/data/store.py` | DataStore container with latest-snapshot index |
| `interface.py` | `bt_engine/strategy/interface.py` | Strategy protocol, StrategyAction, StrategyUpdate |

### 8.2 Adapt (Modify for Phase 3)

| Module | Path | Changes Needed |
|--------|------|----------------|
| `loop.py` | `bt_engine/engine/loop.py` | Add timer event support; integrate with Phase 2 DataProvider API; add adverse selection context capture; add cross-leg parity monitoring |
| `config.py` | `bt_engine/config.py` | Add timer interval configs; extend LatencyConfig for EMPIRICAL/BUCKETED modes; add data quality thresholds |
| `loader.py` | `bt_engine/data/loader.py` | Replace with Phase 2 DataProvider interface (loader becomes a thin adapter) |
| `runner.py` | `bt_engine/runner.py` | Add multi-day support; export adverse selection metrics; structured result output |

### 8.3 What We Drop From the POC

The POC at `Code/Telonex testing/src/` is superseded entirely. Key lessons carried forward, but no code reuse:

| POC Module | Lesson Carried Forward | Why Not Reuse |
|-----------|----------------------|---------------|
| `engine.py` | 5-step event processing pattern | Uses floats, no latency, no dual-book, cancel-and-replace every snapshot |
| `fill_simulator.py` | L2 >> midpoint validation | No queue model, no trade-driven fills, float arithmetic |
| `strategy.py` | Cancel-and-replace pattern, min-edge concept | Coupled to POC data structures, uses floats |

---

## 9. Testing Strategy

### 9.1 Unit Tests: Fill Simulation

The fill engine is the highest-risk component. Every condition in the 7-condition model gets dedicated test coverage.

**Test suite: `test_fill_engine.py`**

| Test | Setup | Expected |
|------|-------|----------|
| `test_condition_1_dead_order` | CANCELLED order + matching trade | No fill |
| `test_condition_2_not_visible` | Trade before `visible_ts_us` | No fill |
| `test_condition_3_cancel_effective` | Trade after `cancel_effective_ts_us` | No fill |
| `test_condition_4_wrong_price` | Trade at 49 ticks, order at 48 ticks | No fill |
| `test_condition_5_same_side` | BUY taker trade + BUY resting order | No fill |
| `test_condition_5_compatible` | BUY taker trade + SELL resting order | Fill |
| `test_condition_6_queue_not_drained` | Trade size < queue_ahead | No fill, queue reduced |
| `test_condition_7_zero_remaining` | Already fully filled order | No fill |
| `test_passive_fill_basic` | SELL order at 52, BUY trade at 52, queue=0 | Fill at 52 |
| `test_partial_fill` | Order size 1000cs, trade size 500cs, queue=0 | Fill 500cs, remaining 500cs |
| `test_queue_drain_then_fill` | Queue=300cs, trade=500cs, order=200cs | Drain 300, fill 200 |
| `test_multiple_orders_share_trade` | 3 orders at same price, 1 trade | First order fills, others drain |
| `test_aggressive_fill_at_visibility` | BUY at 52, best_ask=50 | Immediate fill at visibility |
| `test_pending_cancel_race_fill` | PENDING_CANCEL order + matching trade before cancel_effective | Fill (race condition) |
| `test_cancel_effective_no_fill` | PENDING_CANCEL + trade after cancel_effective | No fill |

**Test suite: `test_queue_position.py`**

| Test | Setup | Expected |
|------|-------|----------|
| `test_conservative_back_of_queue` | Depth=500cs | queue_ahead=500cs |
| `test_optimistic_front` | Depth=500cs | queue_ahead=0 |
| `test_probabilistic_seeded` | Depth=500cs, seed=42 | Deterministic value in [0, 500] |
| `test_drain_partial` | queue=300, trade=200 | queue=100 |
| `test_drain_exact` | queue=300, trade=300 | queue=0 |
| `test_drain_overflow` | queue=300, trade=500 | queue=0, 200 passthrough |
| `test_cancel_does_not_drain` | Cancel at our price level | queue unchanged |

### 9.2 Unit Tests: Order Management

**Test suite: `test_order_manager.py`**

| Test | Focus |
|------|-------|
| `test_submit_creates_pending` | Status is PENDING_SUBMIT, timestamps correct |
| `test_activate_transitions` | PENDING_SUBMIT -> ACTIVE |
| `test_apply_fill_partial` | ACTIVE -> PARTIALLY_FILLED, remaining_cs correct |
| `test_apply_fill_complete` | PARTIALLY_FILLED -> FILLED |
| `test_cancel_lifecycle` | ACTIVE -> PENDING_CANCEL -> CANCELLED |
| `test_cancel_reservation_release` | Reserved cash released proportionally |
| `test_replace_semantics` | Cancel old + submit new, both latencies independent |

### 9.3 Integration Tests: Known-Answer Scenarios

End-to-end tests with synthetic data where the expected outcome is computed by hand.

**Test suite: `test_engine_integration.py`**

| Scenario | Setup | Expected Outcome |
|----------|-------|------------------|
| **Single fill** | 1 strike, 1 BUY order at 48, 1 trade (SELL taker at 48, size > queue+order) | Exactly 1 fill at 48, portfolio cash decremented |
| **No fill (queue too deep)** | Order at 48, queue_ahead=1000cs, trade size=500cs | Zero fills, queue reduced to 500cs |
| **Settlement YES** | Buy 1000cs YES at 48, strike resolves YES | Cash: -48*1000 + 100*1000 = +52,000 tc profit |
| **Settlement NO** | Buy 1000cs YES at 48, strike resolves NO | Cash: -48*1000 + 0 = -48,000 tc loss |
| **Cross-strike** | Buy YES on $165, sell YES on $170, both resolve YES | Net position from two independent books |
| **Latency prevents fill** | Place order at T, trade at T+100us (before visible_ts) | No fill |
| **Cancel race** | Cancel at T, trade at T+100us (before cancel_effective) | Fill occurs (race condition) |
| **Determinism** | Run same scenario twice | Bit-identical results |

### 9.4 Regression Test: NVDA POC Replication

Replicate the NVDA 2026-03-30 backtest with the same strategy parameters and verify:
- L2 (snapshot-only) mode produces similar fill count to POC's 188 fills
- Trade-driven mode produces a fill count in the same order of magnitude
- Settlement P&L direction matches POC (-$28.60 on $165, +$10.50 on $170)
- Midpoint-equivalent mode (if implemented) still shows massive overcount

This is not an exact-match test (the engine is different), but a sanity check that the results are directionally consistent.

---

## 10. Task Breakdown

### Legend

- **Effort**: S (< 2h), M (2-4h), L (4-8h), XL (8-16h)
- **Dependencies**: Tasks that must complete first
- **Risk**: Impact if this task is wrong

### Phase 3A: Engine Core (Event Loop + Order Management)

| # | Task | Effort | Depends | Risk | Description |
|---|------|--------|---------|------|-------------|
| 3.1 | Timer event infrastructure | M | -- | Low | Add TIMER EventKind, pre-compute timer events at engine init, insert into InternalEventQueue. Extend `InternalEvent` to carry timer metadata. |
| 3.2 | DataProvider integration | M | Phase 2 | Medium | Replace `DataLoader.load()` with Phase 2 DataProvider API. Adapt `DataStore` construction to consume aligned multi-channel data. |
| 3.3 | Engine loop timer support | S | 3.1 | Low | Add Phase 2 handling for TIMER events in `loop.py`. Route to strategy via new `on_timer()` callback. |
| 3.4 | Cross-leg parity monitor | S | -- | Low | After each BOOK_SNAPSHOT, check YES+NO ask sum >= 100 and bid sum <= 100. Log violations to audit journal. |
| 3.5 | Expiration check (Phase 3) | S | -- | Low | Add expire_ts_us to SimOrder. Check in the event loop after Phase 2. Transition expired orders, release reservations. |
| 3.6 | Unit tests: order management | M | -- | -- | Tests from Section 9.2. Verify lifecycle, latency, cancellation, reservation. |

### Phase 3B: Fill Simulation

| # | Task | Effort | Depends | Risk | Description |
|---|------|--------|---------|------|-------------|
| 3.7 | Adverse selection context capture | M | -- | Medium | Create `FillContext` dataclass. On each fill, capture book state, midpoint, depth imbalance. Store in engine for analytics export. |
| 3.8 | Fill engine unit tests | L | -- | **High** | Full test suite from Section 9.1. Every condition, edge case, and race condition tested. This is the highest-priority testing task. |
| 3.9 | Snapshot-only lookahead guard test | S | -- | Medium | Verify C4 guard: order placed at snapshot T cannot benefit from T->T+1 BBO movement. |

### Phase 3C: Multi-Market + Settlement

| # | Task | Effort | Depends | Risk | Description |
|---|------|--------|---------|------|-------------|
| 3.10 | Cross-strike risk metrics | M | -- | Low | Compute aggregate delta, total exposure, capital utilization. Expose via StrategyUpdate or dedicated method. |
| 3.11 | Batch settlement verification | S | -- | Medium | Add post-settlement reconciliation assertion. All positions zero, all reservations zero, cash non-negative. Fail loudly if violated. |
| 3.12 | Multi-day support | M | Phase 2 | Low | Extend runner to iterate over multiple dates. Reset book state between days, carry positions forward. |

### Phase 3D: Integration + Validation

| # | Task | Effort | Depends | Risk | Description |
|---|------|--------|---------|------|-------------|
| 3.13 | Known-answer integration tests | L | 3.2, 3.8 | **High** | Scenarios from Section 9.3 with synthetic data. Hand-computed expected outcomes. |
| 3.14 | NVDA POC replication test | L | 3.2, 3.13 | **High** | Run NVDA 2026-03-30 through the new engine. Compare fill counts, P&L direction, settlement with POC results. |
| 3.15 | Determinism verification | M | 3.13 | **High** | Run engine twice with same inputs. Assert bit-identical fills, cash, journal. |
| 3.16 | Latency sensitivity sweep | M | 3.14 | Medium | Run NVDA scenario with latency 0ms, 200ms, 500ms, 1000ms. Document fill count and P&L impact. Verify that zero-latency produces more fills (sanity). |

### Dependency Graph

```
Phase 2 (DataProvider)
    |
    v
  [3.2] DataProvider integration
    |
    +------+------+
    |      |      |
    v      v      v
  [3.1]  [3.4]  [3.5]   (can run in parallel)
  Timer  Parity  Expiry
    |
    v
  [3.3] Loop timer support
    |
    v
  [3.7] Adverse selection capture
    |
    v
  [3.8] Fill engine unit tests  <-- CRITICAL PATH
    |
    v
  [3.9] Lookahead guard test
    |
    v
  [3.6] Order mgmt unit tests  (can run in parallel with 3.8)
    |
    +------+------+
    |      |      |
    v      v      v
  [3.10] [3.11] [3.12]  (can run in parallel)
  Risk   Settle  Multi-day
    |      |      |
    +------+------+
           |
           v
        [3.13] Known-answer integration tests  <-- CRITICAL PATH
           |
           v
        [3.14] NVDA POC replication
           |
           +------+
           |      |
           v      v
        [3.15] [3.16]  (can run in parallel)
        Determ. Latency
```

### Estimated Total Effort

| Category | Tasks | Effort |
|----------|-------|--------|
| Engine core | 3.1-3.6 | ~12h |
| Fill simulation | 3.7-3.9 | ~8h |
| Multi-market | 3.10-3.12 | ~6h |
| Integration/validation | 3.13-3.16 | ~14h |
| **Total** | **16 tasks** | **~40h** |

Critical path: Phase 2 -> 3.2 -> 3.8 -> 3.13 -> 3.14 -> 3.15

---

## Related Notes

- [[Engine-Architecture-Plan]] -- Full 2500-line specification this plan draws from
- [[Fill-Simulation-Research]] -- Academic research on queue models, adverse selection, HftBacktest comparison
- [[NVDA-POC-Results]] -- The $639 lesson: midpoint overstates P&L by 16.5x fill overcount
- [[Orderbook-Backtesting-with-Telonex]] -- L2 data integration approach and fill simulation improvements
- [[Polymarket-CLOB-Mechanics]] -- CLOB mechanics, FIFO matching, fee structure, batch orders
- [[Data-Alignment-Architecture]] -- Phase 2 DataProvider that feeds this engine
