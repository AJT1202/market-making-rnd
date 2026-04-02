# Backtesting Engine: Complete Simulation Reference

This document explains in absolute detail how the backtesting engine simulates strategies against Polymarket's Bitcoin Up/Down 5-minute binary markets. It covers every layer from raw data ingestion to settlement accounting.

---

## Table of Contents

1. [High-Level Architecture](#1-high-level-architecture)
2. [Raw Data Ingestion](#2-raw-data-ingestion)
3. [Price and Size Conversion](#3-price-and-size-conversion)
4. [Event Normalization and Ordering](#4-event-normalization-and-ordering)
5. [Orderbook Reconstruction](#5-orderbook-reconstruction)
6. [The Event Loop (Dual-Stream Clock)](#6-the-event-loop-dual-stream-clock)
7. [Market Phases and Lifecycle](#7-market-phases-and-lifecycle)
8. [Order Submission and Latency Model](#8-order-submission-and-latency-model)
9. [Queue Position Model](#9-queue-position-model)
10. [Fill Simulation](#10-fill-simulation)
11. [Order Cancellation](#11-order-cancellation)
12. [Replace Semantics](#12-replace-semantics)
13. [Engine-Level Safety Checks (Rejections)](#13-engine-level-safety-checks-rejections)
14. [Portfolio and Capital Management](#14-portfolio-and-capital-management)
15. [Fee Computation](#15-fee-computation)
16. [Settlement and Resolution](#16-settlement-and-resolution)
17. [P&L Computation](#17-pnl-computation)
18. [Multi-Market Batch Runs](#18-multi-market-batch-runs)
19. [Segmentation and Data Quality](#19-segmentation-and-data-quality)
20. [Warmup Tagging](#20-warmup-tagging)
21. [Cross-Leg Parity Checks](#21-cross-leg-parity-checks)
22. [Tick-Size Change Handling](#22-tick-size-change-handling)
23. [Deduplication Logic](#23-deduplication-logic)
24. [Audit Journal](#24-audit-journal)
25. [Strategy Interface](#25-strategy-interface)
26. [Determinism Guarantees](#26-determinism-guarantees)
27. [Available Strategy Operations](#27-available-strategy-operations)

---

## 1. High-Level Architecture

The engine is organized into five layers with strict ownership boundaries:

```
Raw Files (D:/data/YYYY-MM-DD/bitcoin/{condition_id}/)
    |
    v
[Layer 1: Raw Adapter]       parse .jsonl.gz, normalize, hash, tag
    |
    v
[Layer 2: Book Reconstruction]  rebuild YES/NO orderbooks, validate vs snapshots
    |
    v
[Layer 3: Execution Simulator]  latency, queue position, fill generation
    |
    v
[Layer 4: Portfolio]           cash, positions, collateral, P&L
    |
    v
[Layer 5: Analytics]          metrics, parity checks, segment filtering
```

Each layer owns a specific concern and communicates through well-defined data structures. The book reconstructor never touches own-order state. The execution simulator never modifies the public book. The portfolio never generates fills.

---

## 2. Raw Data Ingestion

### Source Files

Each 5-minute binary market produces one directory:

```
D:/data/YYYY-MM-DD/bitcoin/{condition_id}/
    events.jsonl.gz    # All WS + REST events (gzip-compressed JSONL)
    meta.json          # Market metadata (condition_id, token IDs, start/end times)
    gaps.jsonl         # WebSocket disconnect/reconnect records
    snapshots.jsonl    # REST snapshot attempt log
    .status            # "open" or "closed"
```

### Envelope Structure

Every line in `events.jsonl.gz` is a JSON envelope:

| Field | Type | Description |
|-------|------|-------------|
| `recv_ts` | float | Collector's local clock (Unix seconds, microsecond precision) |
| `tier` | string | `"cold"` or `"hot"` — which collection tier was active |
| `source` | string | `"ws"` (WebSocket) or `"rest"` (REST snapshot) |
| `asset_id` | string | Token ID (envelope-level for REST events only) |
| `raw` | dict | Raw exchange payload (event-type-specific) |

### Event Types in Raw Data

**1. `book`** (WebSocket) — Full orderbook snapshot. Emitted on WS connect and periodically. Contains complete bid/ask ladder for one token.

```json
{
  "event_type": "book",
  "asset_id": "658186...",
  "market": "0xbd31dc...",
  "bids": [{"price": ".48", "size": "30"}, ...],
  "asks": [{"price": ".52", "size": "25"}, ...],
  "timestamp": "1773273605961"
}
```

**2. `price_change`** (WebSocket) — Most frequent event. Represents an order placement, modification, or cancellation that changed the resting depth at a price level.

```json
{
  "event_type": "price_change",
  "market": "0xbd31dc...",
  "timestamp": "1773273605961",
  "price_changes": [
    {
      "asset_id": "658186...",
      "price": ".48",
      "size": "230",
      "side": "BUY",
      "hash": "0xabc..."
    }
  ]
}
```

**Critical**: `size` is the **new absolute total resting size** at that price level, NOT a delta. If `size` is `"0"`, the level is removed entirely.

**3. `last_trade_price`** (WebSocket) — A trade execution occurred.

```json
{
  "event_type": "last_trade_price",
  "asset_id": "658186...",
  "price": ".50",
  "size": "100",
  "side": "BUY",
  "fee_rate_bps": "100",
  "timestamp": "1773273605961"
}
```

The `side` field is the **taker's side** (BUY means a buyer took a resting sell order; SELL means a seller hit a resting buy order).

**4. `tick_size_change`** (WebSocket) — The tick grid changed (happens when price approaches extremes like 0.96 or 0.04).

```json
{
  "event_type": "tick_size_change",
  "asset_id": "658186...",
  "old_tick_size": "0.01",
  "new_tick_size": "0.001",
  "timestamp": "1773273605961"
}
```

**5. REST snapshots** — Periodic REST polls (every 30s during HOT tier). `source` is `"rest"` at the envelope level. Contains `bids`, `asks`, and `tick_size` inside `raw`. The `asset_id` is at the envelope level, not inside `raw`.

### Token Side Resolution

`meta.json` provides `yes_token_id` and `no_token_id`. Every event's `asset_id` is matched against these to determine whether it refers to the YES or NO token. Events with unrecognized asset IDs are flagged.

---

## 3. Price and Size Conversion

The engine uses **zero floating-point arithmetic** throughout. All prices and sizes are converted to integers at the ingestion boundary.

### Price: String to Integer Ticks

```
Input:  ".48"  (string from exchange)
Step 1: Decimal(".48")                    = 0.48
Step 2: 0.48 / Decimal("0.01")           = 48    (divide by tick size)
Step 3: Verify result is exact integer    = 48 ticks
Output: 48
```

The default tick size is `0.01`, so 1 tick = 0.01 of the [0, 1] price range. Valid prices range from 1 tick (0.01) to 99 ticks (0.99).

If the division does not produce an exact integer, the event is flagged as `MALFORMED_PRICE` — the price doesn't land on the tick grid.

Both price string formats are handled: `".48"` (no leading zero) and `"0.5"` (with leading zero).

### Size: String to Integer Micro-Units

```
Input:  "219.217767"  (string from exchange)
Step 1: Decimal("219.217767")             = 219.217767
Step 2: 219.217767 * 10^6                 = 219217767
Output: 219217767 micro-units
```

The scale factor is always `10^6` (`SIZE_SCALE`). This converts fractional share quantities into integers. Negative sizes are rejected.

### Why This Matters

Every subsequent computation (cost, fee, collateral, P&L) uses integer arithmetic on ticks and micro-units. This guarantees:
- **Determinism**: No floating-point rounding differences across platforms
- **Exact settlement**: P&L reconciliation is exact, not approximate

---

## 4. Event Normalization and Ordering

### Canonical Event Model

After parsing, every event becomes a `NormalizedEvent` with these fields:

| Field | Description |
|-------|-------------|
| `event_id` | Deterministic: `"filename:line_no:sub_idx"` |
| `source_file` | Path to the events.jsonl.gz file |
| `ingest_seq` | Line number in file (preserves raw arrival order) |
| `normalized_seq` | Position in final sorted array (0..N-1) |
| `causal_group_id` | `"market_id:exchange_ts_ms"` — groups same-millisecond events |
| `exchange_ts_ms` | Exchange-side timestamp (milliseconds) |
| `receive_ts_ms` | Collector-side timestamp (converted to ms) |
| `market_id` | Condition ID |
| `asset_id` | Token identifier |
| `token_side` | `YES` or `NO` (resolved via meta.json) |
| `event_type` | `BOOK`, `PRICE_CHANGE`, `LAST_TRADE_PRICE`, `TICK_SIZE_CHANGE`, `REST_SNAPSHOT` |
| `book_side` | `BID` or `ASK` (for price_change: BUY=BID, SELL=ASK) |
| `price_ticks` | Integer ticks (or None) |
| `size_units` | Integer micro-units (or None) |
| `raw_payload_hash` | SHA-256 of the canonical JSON serialization |
| `raw_payload` | Original raw dict (preserved for extraction functions) |
| `flag` | Primary quality flag (first non-OK issue found) |
| `flags` | All quality flags as a tuple |

### price_change Explosion

A single `price_change` envelope can contain multiple sub-entries in `raw.price_changes[]` (one per affected token/level). Each sub-entry becomes its own `NormalizedEvent`. The `event_id` distinguishes them via the sub-index: `"events.jsonl.gz:1042:0"`, `"events.jsonl.gz:1042:1"`.

### Canonical Sort Order

After all events are parsed, they are sorted by a strict three-key ordering:

```
Primary:   exchange_ts_ms    (exchange timestamp)
Secondary: ingest_seq        (line number in file = arrival order)
Tertiary:  normalized_seq    (sub-index for exploded events)
```

After sorting, `normalized_seq` is reassigned to be `0..N-1` in sorted order.

**Why this ordering matters**: It preserves the observed arrival order when timestamps collide (common in market data). The engine deliberately does NOT use a synthetic event-type tie-break (e.g., always process `book` before `price_change` at the same timestamp) because that would inject look-ahead bias.

---

## 5. Orderbook Reconstruction

### Data Structures

The engine maintains two independent `TokenBook` objects — one for YES, one for NO. Each book has:

- `bids: dict[int, PriceLevel]` — keyed by price_ticks, best bid = highest key
- `asks: dict[int, PriceLevel]` — keyed by price_ticks, best ask = lowest key
- `best_bid_ticks` / `best_ask_ticks` — cached BBO, recomputed after every change
- `last_trade_price_ticks` / `last_trade_side` — metadata from last trade

Each `PriceLevel` contains:
- `price_ticks` (int)
- `displayed_size_units` (int) — the total resting size at this level
- `last_update_ts` (int) — when this level was last modified

### How Events Modify the Book

**`book` event (full snapshot reset)**:
1. Clear both bids and asks dictionaries entirely
2. Rebuild from the snapshot's bid/ask arrays
3. Recompute BBO

This is a **destructive reset** — all prior reconstructed state is discarded.

**`price_change` event (absolute level update)**:
1. Look up the price level in the appropriate side (BID or ASK)
2. If `size_units > 0`: overwrite the level with the new absolute size
3. If `size_units == 0`: remove the level entirely
4. Recompute BBO

This is NOT a delta. A `price_change` with `size=230` at price `.48` means "there are now exactly 230 units resting at 0.48", regardless of what was there before.

**`last_trade_price` event (trade metadata)**:
1. Update `last_trade_price_ticks` and `last_trade_side`
2. Do **NOT** modify any depth levels

Trades do not directly change the book's depth. In reality, a trade consumes resting depth, but the exchange sends a separate `price_change` event to reflect the new depth. The engine relies on those explicit updates.

**`tick_size_change` event**:
1. Update the global tick size used for future price parsing
2. Existing levels are NOT re-validated (that's handled at the execution layer)

### REST Snapshot Validation

Every 30 seconds during the HOT tier, the engine receives a REST snapshot (a complete book state from a REST API poll). The engine does NOT use this to reset the book. Instead, it **compares** the reconstructed book against the snapshot to measure drift:

- **Level mismatches**: How many price levels differ in size between reconstructed and snapshot
- **Depth difference**: Sum of absolute size differences across all levels
- **BBO match**: Whether best bid/ask prices agree

This drift measurement feeds into segment quality scoring (TRUSTED / DEGRADED / INVALID).

### Crossed-Book Detection

After every level update, the engine checks if `best_bid >= best_ask` (a crossed book). This is an invalid state that indicates data corruption or reconstruction error. Crossed books are logged but NOT auto-corrected.

---

## 6. The Event Loop (Dual-Stream Clock)

This is the core of the simulation. The engine processes all events through a strict temporal ordering with five phases per timestamp.

### Setup

Before the loop starts:
1. All normalized events are grouped by `exchange_ts_ms`
2. All unique timestamps are collected into a sorted queue (`ts_queue`)
3. The queue is dynamic — new timestamps can be inserted as orders create future internal events

### Per-Timestamp Processing (5 Phases)

```
For each timestamp in ts_queue:

  PHASE 1: External Market Data
  - Process ALL external events at this timestamp
  - Book reconstruction (price_change, book snapshots)
  - Trade processing (triggers fill checks against resting sim orders)
  - Tick-size changes

  PHASE 2: Pending Order/Cancel Requests
  - Submit any scripted order requests due at this timestamp
  - Engine safety checks run here (phase, tick grid, capital)

  PHASE 3: Internal Simulator Events
  - Process order_visible: PENDING_SUBMIT -> ACTIVE (assigns queue position)
  - Process cancel_effective: PENDING_CANCEL -> CANCELLED (releases reservations)
  - Sorted by (ts_ms, seq) for FIFO ordering among same-timestamp internals

  PHASE 4: Expiration Checks
  - Any order past its expire_ts transitions to EXPIRED

  PHASE 5: Strategy Delivery + Action Processing
  - Detect if BBO changed this timestamp (one consolidated BOOK_UPDATE, not per-level)
  - Deliver all queued strategy events to strategy.on_event()
  - Process returned actions (PLACE_ORDER, CANCEL_ORDER, REPLACE_ORDER)
  - Actions create FUTURE internal events (never immediate mutations)
```

### Critical Ordering Invariants

1. **External before internal**: All market-data events at time T are fully applied before any internal events at time T. This means an order cannot "see" a market state change and react at the same instant.

2. **No same-timestamp reaction**: Strategy decisions at time T create events at time T + latency. They never mutate state at time T.

3. **Dynamic queue expansion**: When a strategy places an order at time T, the order's `visible_ts` (T + latency) is inserted into `ts_queue` via binary insertion (`bisect.insort`). This ensures the event loop naturally reaches that timestamp.

4. **FIFO for same-time internals**: If two orders become visible at the same timestamp, they are processed in the order they were submitted (tracked by a monotonic sequence counter).

---

## 7. Market Phases and Lifecycle

Each 5-minute market progresses through four phases:

| Phase | When | Trading Allowed |
|-------|------|-----------------|
| `PRE_OPEN` | Before `market_start_ts` | No |
| `ACTIVE` | Between `market_start_ts` and `market_end_ts` | **Yes** |
| `CLOSED` | After `market_end_ts` | No |
| `RESOLVED` | After settlement (if `resolution_value` provided) | No |

Phase transitions are automatic based on the current timestamp. When a transition occurs:
- A `PHASE_TRANSITION` event is emitted to the strategy
- `MARKET_OPEN` is emitted when entering ACTIVE
- `MARKET_CLOSE` is emitted when entering CLOSED

Any order submitted outside of the ACTIVE phase is **immediately rejected** with reason `"market phase {phase} disallows trading"`.

### Hot Entry

The transition from PRE_OPEN to ACTIVE represents what could be called "hot entry" — the moment the 5-minute market window opens. The engine processes all events before `market_start_ts` in PRE_OPEN phase (book reconstruction runs, but no trading). When the first event at or after `market_start_ts` arrives, the phase transitions to ACTIVE and the strategy receives `MARKET_OPEN`.

---

## 8. Order Submission and Latency Model

### Order Lifecycle

When a strategy places an order, it does NOT immediately appear on the simulated book. Instead:

```
decision_ts (strategy decides)
    |
    +-- decision_to_send_latency_ms (your infrastructure)
    |
    v
send_ts (order leaves your system)
    |
    +-- exchange_network_latency_ms (blockchain/CLOB latency)
    |
    v
visible_ts (order is resting on the book, eligible for fills)
```

The order starts in `PENDING_SUBMIT` status and transitions to `ACTIVE` at `visible_ts`.

### Latency Modes

| Mode | Behavior |
|------|----------|
| `CONSTANT` | Fixed latency values (default: 500ms for exchange network) |
| `EMPIRICAL_DISTRIBUTION` | Uniformly sample from a list of observed latency values |
| `BUCKETED_BY_TIME_OF_DAY` | Different latency values based on time-of-day buckets |

All modes use `random.Random(seed)` with a fixed seed (default: 42) for determinism.

### Latency Components

The model separates two independent latency components:

1. **`decision_to_send_ms`** (default: 0ms) — Your infrastructure latency. The delay between the strategy making a decision and the order being transmitted. This is under your control.

2. **`exchange_network_ms`** (default: 500ms) — Blockchain + CLOB latency. The delay between transmission and the order becoming visible/resting. This is NOT under your control.

Realistic Polymarket submission latency is **200-800ms** (on-chain CLOB via Polygon).

### Same-Timestamp Guard

An order cannot become visible at the same timestamp as the decision that created it (unless latency is explicitly configured to zero). This prevents the strategy from placing an order and having it fill in the same processing step.

---

## 9. Queue Position Model

Queue position is **explicit per-order state** that determines when an order can be filled. It is NOT recomputed on-the-fly — it is assigned once and evolves deterministically.

### Initial Assignment

When an order transitions from `PENDING_SUBMIT` to `ACTIVE` (at `visible_ts`), it is assigned a `queue_ahead_units` value representing how many units of existing resting depth are ahead of it at the same price level:

| Model | Assignment Rule |
|-------|-----------------|
| `CONSERVATIVE` (default) | `queue_ahead = displayed_size` (back of queue — worst case) |
| `PROBABILISTIC` | `queue_ahead = randint(0, displayed_size)` (random position, seeded RNG) |
| `OPTIMISTIC` | `queue_ahead = 0` (front of queue — best case, for sanity checks only) |

The `displayed_size` is the total resting size at that price level from the public book, **excluding the order's own size** (own orders are never mixed into the public book display).

### Queue Evolution

Queue position decreases when trades occur at the same price level:

```
For each trade at this order's price level:
    consumed = min(queue_ahead_units, trade_size_units)
    queue_ahead_units -= consumed
```

**What does NOT improve queue position:**
- Cancellations by other participants (unless empirically validated otherwise)
- New orders placed behind in the queue
- Book snapshots or price_change events

The queue only drains through actual trades consuming depth ahead of the order.

---

## 10. Fill Simulation

Fills are the most critical part of the simulation. A fill represents the simulated execution of a strategy's resting order.

### The 7 Fill Conditions

ALL of the following must be true for a fill to occur:

| # | Condition | What It Prevents |
|---|-----------|------------------|
| 1 | Order status is ACTIVE, PARTIALLY_FILLED, or PENDING_CANCEL | Fills on dead orders |
| 2 | `trade_ts >= order.visible_ts` | Fills before order is resting |
| 3 | `cancel_effective_ts` has NOT been reached | Fills after cancellation took effect |
| 4 | Trade price matches order's resting price level | Fills at wrong price |
| 5 | Trade direction is compatible (BUY taker fills SELL resting, and vice versa) | Fills on same-side orders |
| 6 | `queue_ahead_units == 0` | Fills before queue position reached |
| 7 | Remaining trade size is sufficient | Fills larger than available liquidity |

**Only trades trigger fills.** Depth changes (price_change, book events) never generate fills, even if depth at a level increases. This is a core design principle.

### Fill Size Computation

When a trade occurs, the fill process has two phases:

**Phase 1: Queue Reduction**
For each eligible order at the trade's price level, independently reduce `queue_ahead_units`:
```
original_queue = order.queue_ahead_units
consumed = min(order.queue_ahead_units, trade_size_units)
order.queue_ahead_units -= consumed
```

Each order uses the **full trade size** independently for queue reduction (queue reduction is not shared).

**Phase 2: Fill Allocation**
Orders with `queue_ahead_units == 0` after Phase 1 allocate fills from a shared pool:
```
passthrough = max(0, trade_size_units - original_queue_ahead)
fill_size = min(order.remaining_size_units, passthrough, remaining_trade_in_pool)
```

The `passthrough` uses the **original** `queue_ahead_units` (before Phase 1 reduction), ensuring correct FIFO allocation when multiple orders exist at the same level.

### Fill Output

Each fill produces:
- `fill_id`: Monotonic counter (`fill_000000`, `fill_000001`, ...)
- `order_id`: Which order was filled
- `ts_ms`: The trade timestamp
- `price_ticks`, `size_units`: Execution details
- `aggressor_side`: BUY or SELL (the taker's direction)
- `source_trade_event_id`: Links back to the specific trade event that caused this fill

### What Happens After a Fill

1. **Portfolio update**: Cash debited/credited, position adjusted, fee deducted
2. **Order status update**: `remaining_size_units` decremented; status becomes `PARTIALLY_FILLED` or `FILLED`
3. **Audit journal entry**: Full fill details logged with traceability
4. **Strategy notification**: `FILL` event queued for delivery in Phase 5

### Important: Fills During PENDING_CANCEL

An order in `PENDING_CANCEL` status (cancel requested but not yet effective) **CAN still fill**. This models the real-world race condition where a cancel request is in-flight but the order gets hit before the cancel takes effect. This is one of the most important sources of adverse fills in short-horizon market making.

---

## 11. Order Cancellation

### Cancellation Lifecycle

```
decision_ts (strategy requests cancel)
    |
    +-- decision_to_send_latency_ms
    |
    v
cancel_sent_ts (cancel request leaves your system)
    |
    +-- cancel_latency_ms (can differ from submit latency)
    |
    v
cancel_effective_ts (order is actually removed from book)
```

Between `cancel_sent_ts` and `cancel_effective_ts`:
- Order status is `PENDING_CANCEL`
- **The order CAN still fill** (condition 3 allows fills before cancel_effective_ts)
- This window is where stale-order adverse fills occur

At `cancel_effective_ts`:
- Order status transitions to `CANCELLED`
- Portfolio reservations are released (cash for BUY orders, inventory for SELL orders)
- Strategy receives `CANCEL_EFFECTIVE` event

### Cancel Latency

Cancel latency is configured **independently** from submission latency. In the real world, cancel latency can differ from order placement latency, and this difference matters significantly for market-making strategies.

---

## 12. Replace Semantics

`REPLACE_ORDER` is modeled as `cancel old + submit new`. These are two **independent, non-atomic operations** with separate latency paths:

```
decision_ts
    |
    +-- Cancel old order (goes through PENDING_CANCEL -> CANCELLED path)
    |       cancel_effective_ts = decision_ts + decision_to_send + cancel_latency
    |
    +-- Submit new order (goes through PENDING_SUBMIT -> ACTIVE path)
            visible_ts = decision_ts + decision_to_send + exchange_network_latency
```

**Non-atomicity means:**
- The old order could fill between the cancel request and cancel effective
- Both orders could briefly coexist (old still pending cancel, new already visible)
- The new order's queue position is assigned independently at its own visible_ts

This accurately models real exchange behavior where atomic replace is not guaranteed.

---

## 13. Engine-Level Safety Checks (Rejections)

Before any order reaches the execution simulator, three safety checks are applied:

### 1. Phase Check
- Only `ACTIVE` phase allows trading
- Orders in PRE_OPEN, CLOSED, or RESOLVED are rejected with `"market phase {phase} disallows trading"`

### 2. Tick Grid Check
- Price must be in range [1, 99] ticks
- Price must align with current tick grid: `price_ticks % tick_size_ticks == 0`
- Off-grid orders are rejected with `"price {price} off tick grid (tick_size={tick_size})"`

### 3. Capital Check
- For BUY orders: `price_ticks * size_units <= available_cash`
- For SELL orders (INVENTORY_BACKED): must hold sufficient tokens
- For SELL orders (COLLATERAL_BACKED): use held inventory first, then reserve cash collateral
- Rejected with specific reason: `"insufficient_cash"`, `"insufficient_inventory:YES"`, `"insufficient_cash_for_collateral"`

Rejected orders are logged to the audit journal with their rejection reason and never enter the simulator.

---

## 14. Portfolio and Capital Management

### Portfolio State

```
cash_balance          Total cash (in tick-micro-units: price_ticks * size_units)
reserved_cash         Cash reserved for pending BUY orders and collateral
yes_position          YES tokens held (micro-units)
no_position           NO tokens held (micro-units)
reserved_yes_position YES tokens reserved for pending SELL orders
reserved_no_position  NO tokens reserved for pending SELL orders
fees_paid             Cumulative fees paid
realized_pnl          P&L realized at settlement
initial_cash          Starting cash (set at initialization)
```

### Available Cash

```
available_cash = cash_balance - reserved_cash
```

This is the cash available for new orders. It accounts for all reservations from pending orders.

### Two Position Modes

**INVENTORY_BACKED (default)**:
- Can only sell tokens already held
- Available to sell = `position - reserved_position`
- If insufficient, order is rejected
- Conservative: prevents selling tokens you don't have

**COLLATERAL_BACKED**:
- Can sell tokens beyond held inventory if cash collateral is available
- Uses held inventory first, then reserves cash for the remainder
- Collateral per unit = 100 ticks (worst-case binary option payout)
- Cash collateral formula: `collateral_units * 100 ticks`
- Enables two-sided quoting (simultaneous YES and NO market making)

### Cash Flow on BUY Fill

```
cost = fill.price_ticks * fill.size_units
fee  = (cost * fee_rate_bps) / 10000     (integer division)

cash_balance    -= (cost + fee)
reserved_cash   -= cost                   (release reservation)
token_position  += fill.size_units        (gain tokens)
fees_paid       += fee
```

### Cash Flow on SELL Fill

```
cost = fill.price_ticks * fill.size_units
fee  = (cost * fee_rate_bps) / 10000

cash_balance       += (cost - fee)        (receive proceeds minus fee)
token_position     -= fill.size_units     (lose tokens)
reserved_position  -= fill.size_units     (release inventory reservation)
fees_paid          += fee
```

### Reservation Release on Cancel

When an order is cancelled, unfilled portions are released:
- BUY: release `price_ticks * remaining_size_units` from `reserved_cash`
- SELL (INVENTORY_BACKED): release `remaining_size_units` from `reserved_position`
- SELL (COLLATERAL_BACKED): additionally release any cash collateral still reserved

### Boxed Position and Directional Exposure

In binary markets, holding both YES and NO creates a risk-free box:

```
boxed_units = min(yes_position, no_position)
directional_exposure = yes_position - no_position
```

- Boxed units pay 100 ticks regardless of resolution (YES pays resolution_value, NO pays 100 - resolution_value, sum = 100)
- Only directional exposure carries market risk
- The collateral model can use lower requirements for boxed portions

---

## 15. Fee Computation

Fees are computed as a percentage of the trade cost:

```
cost = fill.price_ticks * fill.size_units
fee  = (cost * fee_rate_bps) // 10000
```

- `fee_rate_bps` is in basis points (100 bps = 1%)
- Integer division ensures determinism
- Fees are deducted from cash on both BUY fills (added to cost) and SELL fills (subtracted from proceeds)
- Cumulative `fees_paid` is tracked for the full backtest

---

## 16. Settlement and Resolution

At the end of a market (when `resolution_value` is provided), all positions are settled to cash:

```
resolution_value = 100 if YES wins, 0 if NO wins

yes_payout = yes_position * resolution_value       (100 ticks per unit if YES wins)
no_payout  = no_position * (100 - resolution_value) (100 ticks per unit if NO wins)

cash_balance += yes_payout + no_payout
yes_position = 0
no_position  = 0
realized_pnl = cash_balance - initial_cash
```

### Settlement Examples

**YES wins (resolution_value = 100):**
- 10,000 YES tokens: payout = 10,000 * 100 = 1,000,000 tick-micro-units
- 5,000 NO tokens: payout = 5,000 * 0 = 0

**NO wins (resolution_value = 0):**
- 10,000 YES tokens: payout = 10,000 * 0 = 0
- 5,000 NO tokens: payout = 5,000 * 100 = 500,000 tick-micro-units

### Settlement Reconciliation Invariant

```
final_cash = initial_cash + sum(trading_cashflows) - fees_paid + settlement_payouts
realized_pnl = final_cash - initial_cash
```

Any violation of this invariant indicates an accounting bug.

---

## 17. P&L Computation

### Realized P&L

Computed only at settlement:
```
realized_pnl = cash_balance_after_settlement - initial_cash
```

This captures:
- Trading profits/losses (bought low, sold high or vice versa)
- Fee costs
- Settlement payouts on held positions

### Unrealized P&L (Mark-to-Market)

Available during the backtest for monitoring:
```
unrealized_pnl = (yes_position * yes_mid_ticks) + (no_position * no_mid_ticks)
```

This is the current market value of held positions at mid-price, not the profit/loss relative to entry cost.

### P&L Transition Tracking

Every fill generates a P&L transition entry in the audit journal that records:
- Cash before/after
- YES position before/after
- NO position before/after
- Fees before/after
- Boxed units before/after
- Directional exposure before/after

This provides complete traceability for every cash movement.

---

## 18. Multi-Market Batch Runs

The batch runner processes multiple markets sequentially with portfolio carry-forward:

```
initial_cash = configured starting capital

For each market (sorted by market open time):
    1. Set this market's initial_cash = carry_cash
    2. Create a FRESH strategy instance (via factory)
    3. Run the market through BacktestEngine
    4. Settlement occurs (positions zeroed, cash adjusted)
    5. carry_cash = final cash_balance
    6. Record per-market P&L = cash_after - cash_before

total_pnl = final_carry_cash - initial_cash
```

### Key Design Decisions

- **Markets in chronological order**: Strategies that depend on capital availability or drawdown limits require strict ordering
- **Fresh strategy per market**: Each market gets a new strategy instance from the factory, ensuring stateless evaluation
- **No position carry-forward**: Positions are settled to zero at each market's resolution; only cash carries
- **Per-market P&L isolation**: Each market's contribution to total P&L is independently tracked

---

## 19. Segmentation and Data Quality

### Gap Detection

Two sources of gaps are monitored:

1. **`gaps.jsonl` (collector-reported)**: WebSocket disconnects (pong_timeout, error, reconnect). Contains disconnect/reconnect timestamps and duration.

2. **Event-stream timestamp jumps**: If consecutive events have a timestamp delta exceeding a configurable threshold, this is treated as an unreported gap.

Gaps are classified by severity:
- **MINOR**: Short duration, within tolerance — log and tag segment DEGRADED
- **MAJOR**: Long duration — force a segment boundary; new segment starts DEGRADED until next snapshot anchors it

### Segment Boundaries

The event stream is partitioned into segments anchored by REST snapshots and gap boundaries. Each segment tracks:

- Start/end timestamps
- Drift score (comparison against REST snapshots)
- Missing levels, out-of-order events, duplicate events
- Crossed-book incidents
- Timestamp collision density (fraction of events sharing identical `exchange_ts_ms`)

### Quality Labels

| Label | Meaning | Impact |
|-------|---------|--------|
| `TRUSTED` | Reconstruction matches snapshots within tolerance | Full confidence in simulation results |
| `DEGRADED` | Some drift detected | Results should be treated with caution |
| `INVALID` | Drift too large to trust | Should be excluded from strategy evaluation |

### Quality Scoring

Drift score is computed from:
- Level mismatches between reconstructed and snapshot books
- Total depth difference
- BBO match/mismatch
- Presence of gaps or crossed-book conditions

---

## 20. Warmup Tagging

The first portion of each segment can be tagged as "warmup" to exclude noisy post-reset fills from headline metrics.

### Configuration

```
warmup_ms: int      # Duration after segment start (e.g., 5000ms)
warmup_events: int  # Or: first N events after segment start
```

### Behavior

- Strategy runs **normally** during warmup — it receives events, places orders, and fills can occur
- Fills during warmup get tagged with `warmup=True`
- Headline metrics exclude warmup-tagged fills by default
- Warmup fills are available separately for diagnostics

**Why not suppress the strategy during warmup?** Suppressing execution would shift queue positions and introduce artifacts. The engine runs everything normally but tags the fills for separate analysis.

### Accessing Fills

```
result.fills           # ALL fills (including warmup)
result.headline_fills  # Only non-warmup fills (for metrics)
result.warmup_fills    # Only warmup fills (for diagnostics)
```

---

## 21. Cross-Leg Parity Checks

After every book or price_change event, the engine computes cross-leg parity:

```
yes_ask + no_ask    >= 100 ticks (should always hold)
yes_bid + no_bid    <= 100 ticks (should always hold)

synthetic_yes_bid = 100 - no_ask
synthetic_no_bid  = 100 - yes_ask
effective_spread  = 2 * (yes_ask + no_ask) - 200
```

Violations of these inequalities indicate potential arbitrage edges. Each edge is tagged:

| Tag | Meaning |
|-----|---------|
| `NONE` | No parity edge |
| `EXECUTABLE` | Edge exists and is executable after fees/latency/capital |
| `BLOCKED_FEES` | Edge exists but <= fee cost |
| `BLOCKED_LATENCY` | Edge exists but latency prevents execution |
| `BLOCKED_CAPITAL` | Edge exists but insufficient capital |

These are **diagnostic signals**, not guaranteed arbitrage opportunities.

---

## 22. Tick-Size Change Handling

When a `tick_size_change` event occurs, the engine checks all resting simulated orders:

**Default behavior (`cancel_offgrid_on_tick_change = true`):**
- Any order whose `price_ticks` does not align with the new tick grid (`price_ticks % new_tick_size_ticks != 0`) is auto-cancelled
- Cancel is immediate (no cancel latency for this type)
- Logged with reason code `TICK_SIZE_CHANGE`

**Alternative behavior:**
- Off-grid orders are marked `REJECTED` with reason `UNFILLABLE_OFFGRID`
- They remain in the order list but cannot fill

The strategy receives a `TICK_SIZE_CHANGE` event so it can re-quote at valid prices.

---

## 23. Deduplication Logic

### Hash-Based Dedup (All Non-Trade Events)

Every event's `raw_payload` is hashed with SHA-256 (canonical JSON serialization with sorted keys). If the same hash appears twice, the event is flagged as `DUPLICATE_HASH`.

**Exception**: `last_trade_price` events are **never** flagged as duplicates, because multiple trades can legitimately occur at the same millisecond, price, and size.

### Semantic Dedup (price_change Only)

For `price_change` events, a secondary dedup checks the tuple `(asset_id, book_side, price_ticks, order_hash)`. If this combination repeats, the event is flagged as `DUPLICATE_SEMANTIC`.

### Handling

Flagged duplicates are **tagged, not dropped**. They still flow through the pipeline (the book reconstructor processes them), but the flags are available for quality analysis.

---

## 24. Audit Journal

The engine maintains an append-only journal that logs every significant event:

### Entry Types (22 total)

- `ENGINE_CONFIG`: Configuration hash at run start
- `DATA_FILES`: Source file hashes
- `ORDER_SUBMITTED`, `ORDER_VISIBLE`, `ORDER_REJECTED`, `ORDER_EXPIRED`
- `CANCEL_REQUESTED`, `CANCEL_EFFECTIVE`
- `FILL`: With `source_trade_event_id` and `causal_group_id`
- `PNL_TRANSITION`: Before/after snapshots of all financial state
- `SETTLEMENT`: Resolution value and final P&L
- `PHASE_TRANSITION`: Market phase changes
- `SEGMENT_BOUNDARY`, `BOOK_RESET`: Data quality events
- `WARNING`: Anomalies detected
- And more...

### Determinism

- Each entry is serialized as canonical JSON (sorted keys, no extra whitespace)
- SHA-256 hash computed per entry
- The journal is sufficient to replay any run exactly

---

## 25. Strategy Interface

### Events Delivered to Strategy

| Event | When | Key Data |
|-------|------|----------|
| `MARKET_OPEN` | Phase becomes ACTIVE | BBO state |
| `PHASE_TRANSITION` | Any phase change | old_phase, new_phase |
| `BOOK_UPDATE` | BBO changed (consolidated, not per-level) | All 4 BBO values (YES bid/ask, NO bid/ask) |
| `TRADE_UPDATE` | A trade occurred | price, size, side, BBO |
| `ORDER_VISIBLE` | Own order now resting | Full SimOrder |
| `FILL` | Own order filled | Fill details + order |
| `CANCEL_EFFECTIVE` | Own cancel took effect | Full SimOrder |
| `TICK_SIZE_CHANGE` | Tick grid changed | New tick size |
| `MARKET_CLOSE` | Phase becomes CLOSED | - |
| `RESOLUTION` | Market settled | resolution_value (0 or 100) |

### Book Update Consolidation

The engine does NOT flood the strategy with one event per price level change. Instead, it tracks whether the BBO changed during a timestamp. If it did, one consolidated `BOOK_UPDATE` event is delivered with all four BBO values. This prevents the strategy from reacting to intermediate states.

### Strategy Protocol

```python
class Strategy(Protocol):
    def on_event(self, event: StrategyEvent) -> list[StrategyAction]:
        """React to an event. Return zero or more actions."""
        ...

    def on_init(self, market_id: str, yes_asset_id: str, no_asset_id: str) -> None:
        """Called once before the first event."""
        ...
```

---

## 27. Available Strategy Operations

A strategy can return four types of actions:

### 1. PLACE_ORDER

Place a new limit order (buy or sell) on either the YES or NO token.

```python
StrategyAction(
    action_type=ActionType.PLACE_ORDER,
    asset_id="658186...",       # YES or NO token ID
    side="BUY",                 # "BUY" or "SELL"
    price_ticks=48,             # Price in integer ticks
    size_units=10000000,        # Size in micro-units
)
```

- Subject to engine safety checks (phase, tick grid, capital)
- Creates a PENDING_SUBMIT order that becomes ACTIVE after latency
- Reserves cash (BUY) or inventory (SELL) immediately

### 2. CANCEL_ORDER

Cancel a previously placed order by ID.

```python
StrategyAction(
    action_type=ActionType.CANCEL_ORDER,
    order_id="ord_000003",
)
```

- Initiates cancel latency path (PENDING_CANCEL -> CANCELLED)
- Order can still fill during cancel latency window
- Reservations released when cancel becomes effective

### 3. REPLACE_ORDER

Replace an existing order with a new one (cancel + new, non-atomic).

```python
StrategyAction(
    action_type=ActionType.REPLACE_ORDER,
    order_id="ord_000003",      # Order to cancel
    asset_id="658186...",       # New order details
    side="BUY",
    price_ticks=49,
    size_units=10000000,
)
```

- Old order goes through cancel path independently
- New order goes through submit path independently
- Both latency paths evolve separately (see Replace Semantics)

### 4. NO_OP

Do nothing. Explicitly indicates the strategy has no action to take.

```python
StrategyAction(action_type=ActionType.NO_OP)
```

### What Is NOT Available

- **Market orders**: Not supported. All orders are limit orders with a specific price.
- **Immediate-or-cancel**: Not modeled. All orders rest until filled, cancelled, or expired.
- **Cross-market orders**: Each market is independent. No inter-market hedging within the engine.

---

## 26. Determinism Guarantees

The engine guarantees that identical inputs and configuration always produce identical outputs:

1. **Integer arithmetic**: No floating-point anywhere in the hot path
2. **Seeded RNG**: Probabilistic queue model and empirical latency distributions use `random.Random(seed)` with fixed seeds
3. **Canonical ordering**: Events sorted by `(exchange_ts_ms, ingest_seq, normalized_seq)` — no ambiguity
4. **Deterministic hashing**: SHA-256 of canonical JSON (sorted keys, minimal whitespace)
5. **Monotonic counters**: Order IDs (`ord_000000`) and fill IDs (`fill_000000`) are sequential
6. **FIFO internals**: Same-timestamp internal events processed by monotonic sequence counter

The determinism guarantee means you can:
- Run the same backtest twice and get bit-identical results
- Hash the output to verify reproducibility
- Debug by replaying to any specific timestamp

---

## Appendix: Unit Reference

| Quantity | Unit | Example |
|----------|------|---------|
| Price | Integer ticks (1 tick = 0.01) | 48 ticks = price 0.48 |
| Size | Integer micro-units (1 unit = 10^-6 shares) | 200,000,000 = 200 shares |
| Cash/Cost | tick-micro-units (price_ticks * size_units) | 48 * 200,000,000 = 9,600,000,000 |
| Fee rate | Basis points (100 bps = 1%) | 100 bps |
| Latency | Milliseconds | 500ms |
| Timestamps | Milliseconds since epoch | 1773273605961 |
| Resolution | 0 or 100 (NO wins or YES wins) | 100 |
