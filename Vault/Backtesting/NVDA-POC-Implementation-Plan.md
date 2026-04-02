---
title: "NVDA POC: Telonex Viability & Strategy Backtest"
created: 2026-03-31
tags:
  - poc
  - implementation-plan
  - telonex
  - nvda
  - backtesting
  - market-making
status: in-progress
---

# NVDA POC: Telonex Viability & Strategy Backtest

## Objective

Build a minimal but rigorous backtesting framework around a single Polymarket event to answer two questions:

1. **Is Telonex worth $79/month?** — Evaluate data quality, completeness, schema usability, and whether L2 orderbook data materially improves backtest realism.
2. **Does the core thesis hold?** — Run a probability-based quoting strategy on real historical data and see if the mispricing edge exists.

### Target Event

**Event slug:** `nvda-close-above-on-march-30-2026`

| Market | Strike | Question |
|--------|--------|----------|
| 1 | $160 | Will NVDA close above $160 on March 30, 2026? |
| 2 | $165 | Will NVDA close above $165 on March 30, 2026? |
| 3 | $170 | Will NVDA close above $170 on March 30, 2026? |
| 4 | $175 | Will NVDA close above $175 on March 30, 2026? |
| 5 | $180 | Will NVDA close above $180 on March 30, 2026? |

March 30, 2026 was a Monday. These markets have already resolved. Key dates:
- **March 27 (Friday)** — Last trading session before the weekend, 1 trading day before expiry
- **March 30 (Monday)** — Resolution day, market resolves at NVDA closing price

---

## Phase 0: Discovery & Download Planning (Free, No Downloads Used)

### 0.1 Download Free Markets Dataset

The markets dataset is free and requires no API key.

```python
from telonex import get_markets_dataframe

markets = get_markets_dataframe(exchange="polymarket")

# Filter to our event
nvda_event = markets[markets['event_slug'] == 'nvda-close-above-on-march-30-2026']
print(nvda_event[['slug', 'question', 'asset_id_0', 'asset_id_1', 'status', 'result_id']].to_string())
```

**Deliverables:**
- Exact `slug` and `asset_id` for each of the 5 markets
- Resolution outcomes (which resolved YES, which NO)
- Data availability date ranges per channel

### 0.2 Check Availability (Free)

```python
from telonex import get_availability

for market_slug in nvda_slugs:
    avail = get_availability(
        exchange="polymarket",
        slug=market_slug,
        outcome="Yes"
    )
    print(f"\n{market_slug}:")
    for channel, dates in avail['channels'].items():
        print(f"  {channel}: {dates['from_date']} -> {dates['to_date']}")
```

This tells us:
- Which dates have data for each market
- Which channels are available
- Whether March 30 (resolution day) data is included

### 0.3 Download Strategy Decision

We have **5 free downloads**. Each download = 1 channel x 1 date x 1 asset.

**Recommended allocation:** 5 markets x 1 channel x 1 date = exactly 5 downloads.

| Decision | Recommendation | Rationale |
|----------|---------------|-----------|
| **Channel** | `book_snapshot_25` | L2 depth is Telonex's unique value — we can't get this anywhere else. Gives us top 25 bid/ask levels. Deeper depth allows better fill simulation and slippage modeling. |
| **Date** | March 30, 2026 (resolution day) | Most dynamic pricing, highest activity, resolution mechanics visible, maximum information for evaluation. |
| **Outcome** | `Yes` (outcome_id=0) | YES token is the primary pricing target; NO is its complement. |

**Alternative if March 30 data is unavailable** (Telonex updates daily by early morning UTC — if the pipeline hasn't caught up yet):
- Fall back to March 27, 2026 (Friday before expiry)
- This is still valuable: shows pre-weekend dynamics with 1 trading day remaining

> [!warning] Download Budget
> We have exactly 5 downloads. No room for error. Phase 0 discovery and availability checks are free and must be completed before spending any downloads. If a download fails (404), it should NOT count against our quota — but we should verify this from Telonex docs.

---

## Phase 1: Data Download & Exploration

### 1.1 Download Data

```python
from telonex import get_dataframe

TARGET_DATE = "2026-03-30"  # or "2026-03-27" as fallback
CHANNEL = "book_snapshot_5"

data = {}
for slug in nvda_slugs:
    df = get_dataframe(
        api_key=API_KEY,
        exchange="polymarket",
        channel=CHANNEL,
        slug=slug,
        outcome="Yes",
        from_date=TARGET_DATE,
        to_date=TARGET_DATE,
        engine="pandas",
    )
    data[slug] = df
    print(f"{slug}: {len(df)} rows, {df.columns.tolist()}")
```

Also save raw Parquet files to `data/telonex/nvda-poc/` for reproducibility.

### 1.2 Schema Discovery

Since Telonex doesn't publicly document exact column names for book snapshots, we need to inspect:

```python
for slug, df in data.items():
    print(f"\n=== {slug} ===")
    print(f"Shape: {df.shape}")
    print(f"Columns: {df.columns.tolist()}")
    print(f"Dtypes:\n{df.dtypes}")
    print(f"Time range: {df['timestamp'].min()} -> {df['timestamp'].max()}")
    print(f"Sample:\n{df.head(3)}")
```

Expected columns for `book_snapshot_5` (to be verified):
- `timestamp` — Snapshot time
- `bid_price_0..4`, `bid_size_0..4` — 5 bid levels
- `ask_price_0..4`, `ask_size_0..4` — 5 ask levels

### 1.3 Data Quality Assessment

Produce a **Telonex Data Quality Report** answering:

| Question | How to Measure |
|----------|---------------|
| Coverage completeness | Are there gaps in the timestamp series? How many snapshots per hour? |
| Depth consistency | Do all 5 levels always have data, or are some empty? |
| Spread reasonableness | Is best bid < best ask? What's the typical spread? |
| Cross-strike consistency | Are probabilities monotonically decreasing across strikes at each point in time? |
| Timestamp precision | What resolution? Milliseconds? Microseconds? |
| File sizes | How large is 1 day of `book_snapshot_5` for a typical market? |

**Deliverable:** `~/market-making-rnd/Backtesting/Telonex-Data-Quality-Report.md`

---

## Phase 2: Build Minimal Backtesting Engine

### 2.1 Architecture

A stripped-down event-driven engine, purpose-built for this POC. Not a general framework — just enough to run one strategy on one event.

```
data/telonex/nvda-poc/          # Raw Parquet files from Telonex
src/
  data_loader.py                # Load and align Telonex + price data
  fair_value.py                 # B-S probability calculator (B-L upgrade later)
  engine.py                     # Event loop, state management
  strategy.py                   # Probability-based quoting strategy
  fill_simulator.py             # L2-aware fill simulation
  metrics.py                    # P&L, spread capture, fill rate
  run_backtest.py               # Main entry point
notebooks/
  01_data_exploration.ipynb     # Telonex data quality analysis
  02_backtest_results.ipynb     # Results visualization
```

### 2.2 Fair Value Computation

We need P(NVDA closes above K on March 30) at each point during the trading period.

**Primary approach — Black-Scholes binary pricing:**

For this POC, use the Black-Scholes binary call formula as a fair value proxy. This avoids a ThetaData dependency and is sufficient to test the framework.

$$
V_{\text{YES}} = \Phi(d_2), \quad d_2 = \frac{\ln(S/K) + (r - \frac{1}{2}\sigma^2)\tau}{\sigma\sqrt{\tau}}
$$

Where:
- $S$ = current NVDA stock price (we'll source this — see 2.3)
- $K$ = strike ($160, $165, $170, $175, $180)
- $\sigma$ = implied volatility (use NVDA ATM IV from market close on March 27, or a reasonable estimate like 40-60% annualized)
- $\tau$ = time to expiry in years
- $r$ = 0 (negligible for <3 days)

**Upgrade path — Full Breeden-Litzenberger:**

If ThetaData Theta Terminal is running and accessible, implement the full [[Breeden-Litzenberger-Pipeline]]:
1. Pull NVDA options chain for March 28 expiry (or nearest weekly containing $160-$180 strikes)
2. Fit SABR to the IV smile
3. Extract P(S > K) for each strike
4. This gives us a proper risk-neutral probability accounting for skew

We should implement the B-S version first, verify the engine works, then swap in B-L if ThetaData is available.

### 2.3 NVDA Stock Price Source

We need NVDA's intraday price on March 30 to update fair values. Options:

| Source | Method | Effort |
|--------|--------|--------|
| **ThetaData** | `/v3/stock/history/ohlc` for 1-min bars | Requires running Theta Terminal |
| **Alpaca** | Historical bars API (free tier) | Simple REST call, no local server |
| **Yahoo Finance** | `yfinance` Python package | Simplest, but reliability concerns |
| **Manual** | NVDA opened ~$X, closed ~$Y, interpolate | Last resort for POC |

**Recommendation:** Try Alpaca first (we have access via the alpaca-docs MCP server), fall back to yfinance.

### 2.4 Event-Driven Engine (Minimal)

```python
@dataclass
class BacktestState:
    timestamp: pd.Timestamp
    positions: dict[str, float]      # slug -> quantity (+ = long YES)
    cash: float
    orders: list[Order]              # Resting limit orders
    fills: list[Fill]                # Historical fills
    pnl_history: list[PnLSnapshot]   # Per-timestamp P&L

class Engine:
    def __init__(self, state: BacktestState, strategy, fill_sim):
        self.state = state
        self.strategy = strategy
        self.fill_sim = fill_sim

    def run(self, events: Iterator[Event]):
        for event in events:
            if event.type == "BOOK_UPDATE":
                # 1. Check if any resting orders would fill given new book
                fills = self.fill_sim.check_fills(self.state.orders, event.book)
                for fill in fills:
                    self.state.apply_fill(fill)

                # 2. Strategy decides new quotes
                new_orders = self.strategy.on_book_update(
                    event, self.state, fair_value=self.get_fair_value(event.timestamp)
                )

                # 3. Update resting orders
                self.state.orders = new_orders

                # 4. Record state
                self.state.record_snapshot(event.timestamp)

            elif event.type == "RESOLUTION":
                self.state.apply_resolution(event.outcome)
```

### 2.5 L2-Aware Fill Simulation

This is where Telonex data pays off. Instead of assuming constant fill probability, we use actual depth:

```python
class L2FillSimulator:
    def check_fills(self, orders: list[Order], book: BookSnapshot) -> list[Fill]:
        fills = []
        for order in orders:
            if order.side == "BUY":
                # Buy order fills if our bid >= best ask
                if order.price >= book.best_ask_price:
                    fill_price = order.price  # Limit order, we get our price
                    fills.append(Fill(order, fill_price, order.size))
                # Resting bid: estimate queue position from depth at our price level
                elif order.price <= book.best_bid_price:
                    queue_ahead = self._estimate_queue_position(order, book.bids)
                    # Fill probability based on trade flow through this level
                    # (calibrated from trades data if available)
            # ... symmetric for SELL
        return fills

    def _estimate_queue_position(self, order, depth_levels):
        """Estimate how much size is ahead of us at our price level."""
        for level in depth_levels:
            if level.price == order.price:
                # Conservative: assume we're at the back of the queue
                return level.size
        return float('inf')  # Our price level not even in the book
```

**Key improvement over midpoint-only simulation:** We know the actual depth at each price level, so we can make informed decisions about:
- Whether our quote is competitive (inside the spread or not)
- How much size is ahead of us in the queue
- Whether a fill is realistic given the actual liquidity

### 2.6 Resolution Handling

At market resolution, all positions settle:

```python
def apply_resolution(self, outcomes: dict[str, bool]):
    """outcomes: {slug: True/False} where True = closed above strike"""
    for slug, above_strike in outcomes.items():
        position = self.positions.get(slug, 0)
        if position > 0:  # Long YES
            payout = position * 1.0 if above_strike else 0.0
        elif position < 0:  # Short YES (= Long NO)
            payout = abs(position) * (0.0 if above_strike else 1.0)
        self.cash += payout
        self.positions[slug] = 0
```

---

## Phase 3: Implement Probability-Based Quoting Strategy

### Strategy Choice

**Probability-based quoting** (see [[Core-Market-Making-Strategies#1. Probability-Based Quoting]]) — the simplest strategy that directly tests the core thesis.

### Parameters

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `half_spread` | 0.02 ($0.02) | Tight enough to get fills, wide enough for edge. From research: 2-5 cents for ATM, 1 day to expiry. |
| `max_position` | 50 shares per market | Conservative for POC. ~$25-40 capital per market. |
| `min_edge` | 0.03 ($0.03) | Only quote when \|fair_value - polymarket_mid\| > 3 cents. Threshold from [[Core-Market-Making-Strategies#5. Cross-Market Arbitrage]]. |
| `update_interval` | Every book snapshot | React to every depth change. |
| `initial_capital` | $500 | Enough for 5 markets at $50 max exposure each. |

### Strategy Logic

```python
class ProbabilityBasedQuoting:
    def on_book_update(self, event, state, fair_value):
        mid_poly = (event.book.best_bid + event.book.best_ask) / 2
        edge = fair_value - mid_poly

        orders = []

        # Only quote if edge exceeds minimum threshold
        if abs(edge) < self.min_edge:
            return orders  # No orders — edge too small

        position = state.positions.get(event.slug, 0)

        # Position limits
        can_buy = position < self.max_position
        can_sell = position > -self.max_position

        # Quote around OUR fair value, not Polymarket's mid
        bid_price = round(fair_value - self.half_spread, 2)
        ask_price = round(fair_value + self.half_spread, 2)

        # Clamp to [0.01, 0.99]
        bid_price = max(0.01, min(0.99, bid_price))
        ask_price = max(0.01, min(0.99, ask_price))

        if can_buy and bid_price > 0:
            orders.append(Order("BUY", event.slug, bid_price, self.order_size))
        if can_sell and ask_price < 1.0:
            orders.append(Order("SELL", event.slug, ask_price, self.order_size))

        return orders
```

### Multi-Strike Extension

Since we have all 5 strikes, apply the monotonicity constraint from [[Core-Market-Making-Strategies#4. Multi-Market Quoting]]:

```python
def enforce_monotonicity(fair_values: dict[float, float]) -> dict[float, float]:
    """Ensure P(S > K1) > P(S > K2) for K1 < K2"""
    strikes = sorted(fair_values.keys())
    corrected = {}
    prev = 1.0
    for k in strikes:
        corrected[k] = min(fair_values[k], prev - 0.001)
        prev = corrected[k]
    return corrected
```

---

## Phase 4: Analysis & Telonex Evaluation

### 4.1 Backtest Metrics

Compute all metrics from [[Performance-Metrics-and-Pitfalls]]:

| Metric | Description |
|--------|-------------|
| **Total P&L** | Net profit/loss after resolution |
| **Spread capture** | Revenue from bid-ask spread on round trips |
| **Inventory P&L** | Gain/loss from directional positions at resolution |
| **Fill count** | How many orders filled |
| **Fill rate** | Fills / quotes placed |
| **Average spread captured** | Mean revenue per round-trip |
| **Max inventory** | Peak directional exposure |
| **Capital utilization** | Max capital deployed / initial capital |

### 4.2 Telonex Viability Scorecard

| Criterion | Weight | How to Evaluate |
|-----------|--------|----------------|
| **Data completeness** | 25% | Gaps in timestamps? Missing levels? Coverage of full trading day? |
| **Schema usability** | 15% | Clean column names? Proper types? Easy to work with in pandas? |
| **Depth usefulness** | 25% | Does L2 data meaningfully change fill simulation results vs midpoint-only? |
| **Update frequency** | 15% | How many snapshots per minute? Sufficient for strategy simulation? |
| **Data size & cost** | 10% | At $79/mo, is the data volume reasonable for systematic backtesting? |
| **Coverage breadth** | 10% | Enough stock/index markets to support the full strategy? |

### 4.3 Comparative Analysis

Run the same strategy twice:
1. **With Telonex L2 data** — Use actual depth for fill simulation
2. **Without L2 data (midpoint-only)** — Use only BBO midpoint, assume constant queue depth

Compare results. If the two backtests produce materially different P&L estimates, Telonex is worth it.

### 4.4 Deliverables

| Output | Location |
|--------|----------|
| Data quality report | `Backtesting/Telonex-Data-Quality-Report.md` |
| Backtest results & analysis | `Backtesting/NVDA-POC-Results.md` |
| Telonex viability verdict | `Research/Telonex-Viability-Verdict.md` |
| Source code | `src/` directory |
| Analysis notebooks | `notebooks/` directory |

---

## Implementation Plan: Agent Team

The build will be parallelized across agents:

### Wave 1 (Parallel — Data & Infrastructure)

| Agent | Task | Dependencies |
|-------|------|-------------|
| **Data Agent** | Phase 0 + Phase 1: Discovery, download, schema inspection, quality report | Telonex API key |
| **Engine Agent** | Phase 2.4-2.6: Build `engine.py`, `fill_simulator.py`, `metrics.py` | None (can work from spec) |

### Wave 2 (Parallel — Strategy & Fair Value)

| Agent | Task | Dependencies |
|-------|------|-------------|
| **Fair Value Agent** | Phase 2.2-2.3: `fair_value.py` with B-S pricing + NVDA price source | NVDA price data |
| **Strategy Agent** | Phase 3: `strategy.py` with probability-based quoting + monotonicity | Engine API (from Wave 1) |

### Wave 3 (Sequential — Integration & Analysis)

| Agent | Task | Dependencies |
|-------|------|-------------|
| **Integration Agent** | Wire everything together in `run_backtest.py`, run the backtest | All Wave 1 & 2 outputs |
| **Analysis Agent** | Phase 4: Run comparative analysis, produce reports and notebooks | Backtest results |

### Estimated Scope

| Component | Estimated Lines | Complexity |
|-----------|----------------|------------|
| `data_loader.py` | ~100 | Low |
| `fair_value.py` | ~80 | Medium |
| `engine.py` | ~200 | Medium |
| `strategy.py` | ~120 | Low-Medium |
| `fill_simulator.py` | ~150 | Medium |
| `metrics.py` | ~100 | Low |
| `run_backtest.py` | ~80 | Low |
| Notebooks (2) | ~200 | Low |
| **Total** | ~1,000 | Medium |

---

## Prerequisites & Open Questions

### Prerequisites

- [ ] Telonex API key (free plan — sign up at telonex.io)
- [ ] Python environment with: `telonex[all]`, `pandas`, `numpy`, `scipy`, `matplotlib`
- [ ] NVDA intraday price data for March 27-30 (Alpaca or yfinance)
- [ ] Actual NVDA closing price on March 30 (for resolution)

### Open Questions for Review

1. **Channel choice:** `book_snapshot_5` vs `trades` vs `quotes`? I recommend `book_snapshot_5` because L2 depth is the unique value of Telonex. Trades/quotes can be approximated from other sources.

2. **Date choice:** March 30 (resolution day) vs March 27 (Friday before)? I recommend March 30 for maximum activity, but March 27 gives us pre-weekend dynamics if March 30 data isn't available yet.

3. **Fair value approach:** Start with Black-Scholes (no ThetaData dependency) and optionally upgrade to full Breeden-Litzenberger. Is this acceptable for the POC, or do you want full B-L from the start?

4. **Telonex API key:** Do you already have one, or should the data agent handle signup?

5. **NVDA price source:** Alpaca (you have the MCP server), yfinance, or ThetaData?

---

## Related Notes

- [[Telonex-Data-Platform]] — Full Telonex API reference
- [[Orderbook-Backtesting-with-Telonex]] — L2 fill simulation research
- [[Backtesting-Architecture]] — General backtesting design
- [[Core-Market-Making-Strategies]] — Strategy details
- [[Breeden-Litzenberger-Pipeline]] — Full probability extraction (upgrade path)
- [[Performance-Metrics-and-Pitfalls]] — Evaluation methodology
