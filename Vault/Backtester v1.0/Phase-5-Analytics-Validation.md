---
title: "Phase 5: Analytics, Validation, and Polish"
phase: 5
status: planned
created: 2026-04-02
tags:
  - backtester-v1
  - analytics
  - metrics
  - validation
  - determinism
  - audit-trail
  - pnl-decomposition
  - adverse-selection
depends_on:
  - Phase 1 (Data Pipeline)
  - Phase 2 (Order Matching Engine)
  - Phase 3 (Fill Simulation)
  - Phase 4 (Fair Value Integration)
related:
  - "[[Performance-Metrics-and-Pitfalls]]"
  - "[[NVDA-POC-Results]]"
  - "[[Engine-Architecture-Plan]]"
  - "[[Capital-Efficiency-and-Edge-Cases]]"
  - "[[Order-Flow-Analysis-Strategies]]"
  - "[[Inventory-and-Risk-Management]]"
---

# Phase 5: Analytics, Validation, and Polish

Build the analytics, validation, and output layer that makes backtesting results **trustworthy and actionable**. This is the phase that transforms raw fill and position data into the metrics, decompositions, audit logs, and validation evidence required to make real capital allocation decisions.

The [[NVDA-POC-Results]] demonstrated that naive metrics hide catastrophic errors (midpoint simulator showed +$621 while L2 showed -$18). Phase 5 ensures that every number the engine produces can be traced, decomposed, verified, and reproduced.

---

## 1. Metrics Catalog

Every metric computed by the engine, organized by category. All monetary values use the engine's integer tick-cent representation internally (`_tc` suffix = ticks * centishares) and are converted to dollars only at the reporting boundary.

### 1.1 P&L Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Total P&L** | `spread_capture + inventory_pnl + resolution_pnl - fees + adverse_selection` | Net profit/loss across all components | Negative for strategy; investigate decomposition |
| **Gross Spread Capture** | `SUM(abs(fill_price - fair_value) * fill_size)` for each fill where fill is on the "right side" of fair value | Revenue from quoting; the primary MM income source | < $0 means quoting is losing money before any other costs |
| **Net Spread Capture** | `gross_spread_capture + adverse_selection_cost` | True edge after adverse selection erodes the spread | Negative = adverse selection exceeds spread; strategy is unviable |
| **Inventory P&L** | `SUM(position_t * (mid_{t+1} - mid_t))` over all time steps | Mark-to-market gain/loss from holding inventory | Dominates total P&L = strategy is taking directional bets, not market making |
| **Resolution P&L** | `SUM(final_position_i * settlement_price_i)` per strike, where settlement = $1 (YES resolved) or $0 (NO resolved) | Terminal payoff from positions held to resolution | Large magnitude relative to spread capture = binary risk is unmanaged |
| **Fees Paid** | `SUM(taker_fee_rate * notional)` for any aggressive fills | Transaction costs | Should be ~$0 with Polymarket's zero maker fee; nonzero indicates aggressive order usage |
| **Adverse Selection Cost** | See Section 3 (Glosten-Harris decomposition) | Cost of being filled by informed traders | > 80% of gross spread capture = fatal; see [[Performance-Metrics-and-Pitfalls]] Section 3.1 |
| **Opportunity Cost** | `locked_capital * (risk_free_rate - holding_reward_rate) * tau / 365` | Cost of capital locked until resolution; net of Polymarket's 4% holding reward | Material only for monthly+ markets; see [[Capital-Efficiency-and-Edge-Cases]] Section 2 |

### 1.2 Fill Quality Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Fill Count** | Count of all fills | Total execution activity | < 10 per market/day = insufficient data for analysis |
| **Fill Rate** | `total_fills / total_quote_periods` | Fraction of quoting intervals that produce a fill | > 50% = too aggressive (red flag per [[Performance-Metrics-and-Pitfalls]]); < 5% = too passive |
| **Fill Asymmetry** | `abs(buy_fills - sell_fills) / total_fills` | Imbalance between buy and sell fills; 0 = perfectly balanced | > 0.4 = systematic one-sided filling; likely accumulating inventory |
| **Realized Spread** | `avg_sell_price - avg_buy_price` for matched round-trips (FIFO) | Actual spread captured per round-trip | < 0 = losing money on spread; likely adverse selection |
| **Effective Spread** | `SUM(abs(fill_price - midpoint_at_fill) * sign(side)) / total_fills` | Average distance between fill price and mid at execution | < half the quoted spread = significant slippage |
| **Avg Time to Fill** | `mean(fill_timestamp - order_timestamp)` in microseconds | Latency from order submission to fill | Very short (<1s) may indicate unrealistic fill model |
| **Aggressive Fill Fraction** | `aggressive_fills / total_fills` | Proportion of fills that crossed the spread | > 20% = strategy is paying spread, not earning it |

### 1.3 Adverse Selection Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Adverse Selection Ratio** | `fills_where_mid_moved_against / total_fills` | Fraction of fills followed by unfavorable price movement | < 40% = fill model is too optimistic (red flag); 65-89% = realistic per empirical benchmarks |
| **AS Cost at t+1s** | `mean(sign(side) * (mid_{t+1s} - mid_fill))` | Immediate post-fill mark-to-market loss | Most granular adverse selection signal |
| **AS Cost at t+10s** | `mean(sign(side) * (mid_{t+10s} - mid_fill))` | Short-term information leakage | |
| **AS Cost at t+60s** | `mean(sign(side) * (mid_{t+60s} - mid_fill))` | Medium-term adverse selection | Core metric; if still negative at 60s, the fill was genuinely toxic |
| **AS Cost at t+5min** | `mean(sign(side) * (mid_{t+5min} - mid_fill))` | Long-horizon adverse selection | Primary adverse selection measure used in [[Performance-Metrics-and-Pitfalls]] |
| **Edge Decay Curve** | Per-fill PnL at 1s, 10s, 60s, 5min, 15min, 30min, 60min | How quickly the edge at fill time decays | Edge should persist past 60s for a genuine signal |

### 1.4 Inventory Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Max Inventory** | `max(abs(net_position))` per market | Peak directional exposure | > 75% of position limit = approaching constraint |
| **Avg Inventory** | `mean(abs(net_position))` over time | Typical inventory level | High relative to max = inventory is sticky, not cycling |
| **Time at Limit** | Fraction of time `abs(net_position) >= position_limit` | How often the strategy is constrained | > 20% = position limits binding too frequently |
| **Inventory Half-Life** | `-ln(2) / ln(autocorr(inventory, lag=1))` | Mean time for inventory to decay by half; from [[Inventory-and-Risk-Management]] | > 4 hours for daily expiry = inventory not mean-reverting fast enough |
| **Inventory Turnover** | `total_volume_traded / avg(abs(inventory))` | Rate of inventory cycling | < 2x per session = slow turnover |
| **Net YES Exposure** | `yes_position - no_position` (net directional) | Effective directional bet on the event | Large magnitude = market making has become directional speculation |
| **Boxed Position** | `min(yes_position, no_position)` | Hedged portion of the position | Should be high relative to gross position for risk-managed strategies |

### 1.5 Risk-Adjusted Return Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Sharpe Ratio** | `sqrt(252) * mean(daily_return) / std(daily_return)` | Annualized risk-adjusted return | > 5.0 = suspicious (likely overfit or simulation artifact); 1.5-3.0 = good; see [[Capital-Efficiency-and-Edge-Cases]] Section 3 |
| **Sortino Ratio** | `sqrt(252) * mean(daily_return) / downside_std` | Like Sharpe but only penalizes downside; more appropriate for binary payoffs | Preferred over Sharpe for asymmetric distributions |
| **Max Drawdown** | `min((equity - peak) / peak)` | Largest peak-to-trough decline | > 30% = poor; 5-15% = good; 0% = suspicious |
| **Calmar Ratio** | `annualized_return / abs(max_drawdown)` | Return per unit of drawdown risk | < 1.0 = inadequate compensation for drawdown |
| **Profit Factor** | `gross_profit / abs(gross_loss)` | Ratio of winning to losing PnL | < 1.0 = losing strategy; > 3.0 = suspicious |
| **Return on Locked Capital** | `total_pnl / max_capital_deployed` | Capital efficiency; critical for binary markets where capital is locked | < risk-free rate = not worth the risk |
| **Win Rate (per market)** | `profitable_markets / total_markets` | Fraction of markets that resolved profitably | 100% = suspicious; < 45% for 2+ weeks = red flag per [[Capital-Efficiency-and-Edge-Cases]] |

### 1.6 Probability Quality Metrics

| Metric | Formula | Interpretation | Alert Threshold |
|---|---|---|---|
| **Brier Score** | `mean((predicted_prob - outcome)^2)` | Probability forecast accuracy; 0 = perfect, 0.25 = coin flip | > 0.20 = fair values are poorly calibrated |
| **Calibration Gap** | `max(abs(predicted_mean - observed_frequency))` across bins | Worst-case miscalibration | > 0.10 in any bin = systematic bias |
| **Log Loss** | `-mean(outcome * log(p) + (1-outcome) * log(1-p))` | Penalizes confident wrong predictions heavily | > 0.60 = poor; < 0.40 = good |

---

## 2. P&L Decomposition Specification

### 2.1 Five-Component Decomposition

The engine decomposes P&L into five independent, additive components. This extends the three-component model from [[Performance-Metrics-and-Pitfalls]] Section 1 with explicit resolution and fee terms required for binary markets.

```
Total PnL = Spread Capture + Adverse Selection + Inventory PnL + Resolution PnL - Fees
```

### 2.2 Exact Formulas

**Spread Capture** (per fill):

For a fill at price $p_f$ with fair value $V$ at time of fill:

```
If BUY:   spread_capture_i = (V - p_f) * size     [positive when buying below fair value]
If SELL:  spread_capture_i = (p_f - V) * size      [positive when selling above fair value]

Gross Spread Capture = SUM(spread_capture_i) for all fills
```

The fair value $V$ here is the Breeden-Litzenberger probability (or Black-Scholes in v1.0), **not** the Polymarket midpoint. Using the model fair value rather than the market midpoint separates the alpha component (mispricing signal) from the execution component (spread capture).

**Adverse Selection** (per fill):

Measured using the Polymarket midpoint at configurable horizons post-fill. See Section 3 for the full Glosten-Harris decomposition.

```
If BUY:   adverse_selection_i = min(0, (mid_{t+horizon} - mid_t) * size)
If SELL:  adverse_selection_i = min(0, (mid_t - mid_{t+horizon}) * size)

Total Adverse Selection = SUM(adverse_selection_i) for all fills
```

The `min(0, ...)` clamp ensures adverse selection is always non-positive: we count losses from price moving against us but do not credit "favorable selection" because that conflates with spread capture.

**Inventory P&L** (continuous mark-to-market):

```
inventory_pnl = SUM over t: net_position_t * (mid_{t+1} - mid_t)
```

Where `net_position_t` = YES position minus NO position (net directional exposure) and `mid_t` is the Polymarket midpoint at time step $t$. This captures the mark-to-market gain or loss from holding inventory as the market moves, independent of any fills.

**Resolution P&L** (terminal):

```
For each strike k:
  If resolved YES: resolution_pnl_k = net_yes_position_k * $1.00
  If resolved NO:  resolution_pnl_k = net_no_position_k * $1.00
  (the complementary token resolves to $0.00)

Total Resolution PnL = SUM(resolution_pnl_k) + accumulated_cash_from_fills
```

In practice, resolution PnL is the difference between the settlement value and the mark-to-market value of the position just before resolution. For a market maker, a large resolution PnL (positive or negative) indicates the strategy is taking directional risk that dwarfs spread capture -- the key warning from [[NVDA-POC-Results]] where the $165 strike had -$50 resolution loss against only $21.40 cash from trading.

**Fees:**

```
fees = SUM(taker_fee_rate * abs(fill_price * fill_size)) for aggressive fills
```

On Polymarket stock/index markets, maker fees are zero, so this should be $0 for a properly passive strategy.

### 2.3 Consistency Check

The decomposition must satisfy the accounting identity:

```
total_pnl = final_cash + final_settlement_value - initial_cash
          = spread_capture + adverse_selection + inventory_pnl + resolution_pnl - fees
```

If these differ by more than 1 tick (rounding tolerance for integer arithmetic), log a `PNL_DECOMPOSITION_MISMATCH` audit event.

### 2.4 Per-Market vs Portfolio Aggregation

Compute the decomposition at three levels:

1. **Per-strike, per-token**: Finest granularity (e.g., NVDA $165 YES)
2. **Per-strike**: Combine YES and NO token P&L for the strike
3. **Portfolio**: Sum across all strikes and all underlyings

Report all three levels. The per-strike breakdown is essential because -- as the NVDA POC showed -- aggregate numbers can hide that all profit comes from one strike while another is hemorrhaging.

---

## 3. Adverse Selection Measurement: Glosten-Harris Decomposition for Binary Markets

### 3.1 Theoretical Foundation

The Glosten-Harris (1988) model decomposes the effective spread into a transitory component (inventory/order-processing costs) and a permanent component (adverse selection / information asymmetry). Adapted for Polymarket binary markets per [[Order-Flow-Analysis-Strategies]]:

```
Effective Spread = Transitory Component + Permanent Component
                 = Order Processing Cost + Adverse Selection Cost
```

In the original model, the trade price $p_t$ relates to the efficient price $m_t$:

```
p_t = m_t + c * q_t + z * q_t + noise

where:
  c = transitory cost (spread capture for the market maker)
  z = permanent price impact (adverse selection cost)
  q_t = trade direction indicator (+1 for buy, -1 for sell)
```

### 3.2 Binary Market Adaptation

Binary markets differ from continuous markets in three key ways:

1. **Bounded price space** ($0 to $1): Adverse selection impact is bounded; a fill at $0.95 on a YES token can move against you by at most $0.05 (if fair value drops to $0.90) or $0.95 (if event resolves NO and price goes to $0).

2. **Terminal resolution**: The "permanent" price impact is ultimately resolved by the binary outcome. A fill that appears adversely selected at t+5min may be profitable at resolution (or vice versa). Measure adverse selection at multiple horizons.

3. **Dual-token structure**: Adverse selection on YES is mechanically the inverse of adverse selection on NO. Measure per-token, report both.

### 3.3 Multi-Horizon Implementation

For each fill $i$ at time $t$, measure the post-fill midpoint at horizons $h \in \{1\text{s}, 10\text{s}, 60\text{s}, 5\text{min}, 15\text{min}, 30\text{min}, 60\text{min}\}$:

```python
@dataclass
class AdverseSelectionRecord:
    """Per-fill adverse selection decomposition."""
    fill_id: str
    fill_timestamp_us: int
    strike: int
    token: str                    # "YES" or "NO"
    side: str                     # "BUY" or "SELL"
    fill_price_ticks: int
    fill_size_cs: int
    fair_value_at_fill_bps: int   # B-L/B-S model value
    mid_at_fill_ticks: int        # Polymarket midpoint

    # Post-fill midpoints at each horizon
    mid_t_plus_1s_ticks: int | None
    mid_t_plus_10s_ticks: int | None
    mid_t_plus_60s_ticks: int | None
    mid_t_plus_5min_ticks: int | None
    mid_t_plus_15min_ticks: int | None
    mid_t_plus_30min_ticks: int | None
    mid_t_plus_60min_ticks: int | None

    # Computed fields (all in ticks * centishares for integer arithmetic)
    spread_component_tc: int      # Distance from mid at fill time
    as_1s_tc: int                 # Adverse selection at t+1s
    as_10s_tc: int                # Adverse selection at t+10s
    as_60s_tc: int                # Adverse selection at t+60s
    as_5min_tc: int               # Adverse selection at t+5min (primary)
    as_15min_tc: int
    as_30min_tc: int
    as_60min_tc: int

    is_adverse_at_60s: bool       # True if mid moved against us
    is_adverse_at_5min: bool
```

**Computation for a BUY fill:**
```
spread_component = (mid_at_fill - fill_price) * size   [positive = captured spread]
as_at_horizon_h  = (mid_t+h - mid_at_fill) * size      [negative = adverse]
```

**Computation for a SELL fill:**
```
spread_component = (fill_price - mid_at_fill) * size    [positive = captured spread]
as_at_horizon_h  = (mid_at_fill - mid_t+h) * size      [negative = adverse]
```

### 3.4 Aggregate Adverse Selection Statistics

From the per-fill records, compute:

| Statistic | Formula | Purpose |
|---|---|---|
| AS Ratio (at 60s) | `count(is_adverse_at_60s) / total_fills` | Overall toxicity rate |
| AS Ratio (at 5min) | `count(is_adverse_at_5min) / total_fills` | Longer-horizon toxicity |
| Mean AS Cost (at 60s) | `mean(as_60s_tc)` over all fills | Average cost per fill |
| Median AS Cost (at 60s) | `median(as_60s_tc)` | Robust center (less sensitive to outliers) |
| AS Cost / Spread | `sum(as_60s_tc) / sum(spread_component_tc)` | What fraction of spread is eaten by AS |
| Edge Decay Profile | `[mean(as_1s), mean(as_10s), mean(as_60s), mean(as_5min), ...]` | Shape of information leakage over time |
| AS by Fill Size | Group by size buckets, compute per-bucket AS ratio | Do large fills carry more adverse selection? |
| AS by Time of Day | Group by 30-min windows, compute per-window AS ratio | Intraday toxicity patterns |
| AS by Distance from ATM | Group by `abs(strike - underlying_price)`, compute per-group | Near-ATM fills are typically more adversely selected |

### 3.5 Realism Validation

Per [[Performance-Metrics-and-Pitfalls]] Section 3.1, empirical adverse selection ratios in liquid markets range from 65-89%. For Polymarket binary markets with our fill simulation:

| Adverse Selection Ratio | Diagnosis |
|---|---|
| < 40% | **Red flag**: Fill model is too optimistic. Investigate. |
| 40-65% | Plausible for a thin, somewhat informed market |
| 65-85% | Realistic range for actively traded binary markets |
| > 90% | Either highly toxic market or fill model is overly pessimistic |

If the backtest shows AS ratio < 40%, the fill simulation from Phase 3 likely has a bug or is not accounting for queue-priority adverse selection.

---

## 4. Determinism Specification

### 4.1 What Guarantees Determinism

Per [[Engine-Architecture-Plan]] Section 13, the engine guarantees bitwise-identical results across runs through:

| Mechanism | Implementation | Why It Matters |
|---|---|---|
| Integer arithmetic | All `price * size` computations use `int` (ticks * centishares) | Eliminates floating-point non-associativity |
| Seeded RNG | `random.Random(seed)` for queue position jitter and latency noise | Reproducible randomness |
| Canonical event ordering | Sort key: `(timestamp_us, kind_priority, sequence)` | No ambiguity when events share timestamps |
| Float boundary isolation | Black-Scholes uses floats internally; output rounded to `int` bps at the boundary before entering the engine | Float nondeterminism is contained |
| Monotonic counters | `ord_000042`, `fill_000042` | Deterministic IDs |
| FIFO tiebreaking | Submission sequence number as final tiebreaker for same-timestamp events | No hash-order dependence |
| No external I/O during sim | All data pre-loaded into memory before simulation starts | No network jitter or file-system ordering effects |

### 4.2 What Could Break Determinism

| Risk | Scenario | Mitigation |
|---|---|---|
| Python dict ordering | Iterating over dicts assumes insertion order (guaranteed since Python 3.7) | Use Python >= 3.7; add assertion in CI |
| Set iteration | Sets have non-deterministic iteration order | Never iterate over sets in the hot path; use sorted lists |
| Parallel execution | Multi-threaded fill processing could reorder events | Engine is single-threaded by design |
| Floating-point across platforms | x86 vs ARM may produce different float results | B-S float computation is isolated; output rounded to int bps; add cross-platform CI test |
| NumPy/SciPy version differences | Different library versions may have different numerical implementations | Pin exact versions in `pyproject.toml` |
| Timestamp ties in input data | Two snapshots with identical `timestamp_us` | Canonical ordering uses `(timestamp_us, data_source, sequence_number)` |
| Hash-based data structures | `hash()` randomization in Python 3.3+ | `PYTHONHASHSEED=0` for determinism; or avoid hash-dependent iteration |

### 4.3 Verification Protocol

```python
def verify_determinism(config: EngineConfig, data: DataStore) -> DeterminismReport:
    """
    Run the same backtest twice and verify bitwise-identical results.
    Returns a report with pass/fail and diagnostic details.
    """
    result_a = run_backtest(config, data)
    result_b = run_backtest(config, data)

    report = DeterminismReport()

    # Level 1: Scalar identity
    report.cash_match = (result_a.final_cash_tc == result_b.final_cash_tc)
    report.fill_count_match = (result_a.total_fills == result_b.total_fills)

    # Level 2: Journal hash (covers every event in sequence)
    report.journal_hash_match = (result_a.journal_hash == result_b.journal_hash)

    # Level 3: Full output hash (covers CSV/Parquet byte-level identity)
    hash_a = sha256_of_outputs(result_a.output_dir)
    hash_b = sha256_of_outputs(result_b.output_dir)
    report.output_hash_match = (hash_a == hash_b)

    report.hash_a = hash_a
    report.hash_b = hash_b
    report.passed = all([
        report.cash_match,
        report.fill_count_match,
        report.journal_hash_match,
        report.output_hash_match,
    ])

    return report
```

The output hash is computed as: `SHA-256(sorted concatenation of SHA-256(file_bytes) for each output file)`. This single hash serves as a fingerprint for the entire backtest run.

### 4.4 Determinism in CI

Every CI run executes the determinism verification on the NVDA POC dataset. If the hashes diverge, the build fails. This catches:
- Accidental introduction of nondeterministic code paths
- Library upgrades that change numerical behavior
- Platform-specific float differences (if CI runs on multiple platforms)

---

## 5. Audit Trail Schema

### 5.1 Design Principles

Per [[Engine-Architecture-Plan]] Section 14:
- Every state change is logged with full context (before and after)
- Every fill is traceable to the source trade event that caused it
- Every fair value computation is logged with all inputs
- Logs are append-only during simulation (no mutation)
- Two persistence modes: `MEMORY` (development) and `FILE` (JSONL for production)

### 5.2 Order Log Schema

Every order submission, amendment, cancellation, and expiry.

| Field | Type | Description |
|---|---|---|
| `event_type` | `str` | One of: `ORDER_SUBMITTED`, `ORDER_VISIBLE`, `ORDER_REJECTED`, `ORDER_CANCELLED`, `ORDER_EXPIRED`, `ORDER_REPLACED` |
| `timestamp_us` | `int` | Engine clock at event time |
| `order_id` | `str` | Monotonic ID, e.g., `ord_000042` |
| `strike` | `int` | Strike price |
| `token` | `str` | `YES` or `NO` |
| `side` | `str` | `BUY` or `SELL` |
| `price_ticks` | `int` | Order price in ticks (1 tick = $0.01) |
| `size_cs` | `int` | Order size in centishares (100 cs = 1 share) |
| `expire_ts_us` | `int` | Expiry timestamp (0 = GTC) |
| `strategy_name` | `str` | Which strategy submitted this order |
| `reject_reason` | `str | None` | If rejected: reason (e.g., "POSITION_LIMIT", "INSUFFICIENT_CASH", "CROSSED_BOOK") |
| `cancel_reason` | `str | None` | If cancelled: reason (e.g., "STRATEGY_CANCEL", "EXPIRY", "ENGINE_SHUTDOWN") |
| `queue_position` | `int | None` | Assigned queue position at the price level (for visibility events) |
| `state_snapshot` | `dict` | Current positions, cash, and resting orders at time of event |

### 5.3 Fill Log Schema

Every fill, with full provenance linking to the source trade event and the order that was filled.

| Field | Type | Description |
|---|---|---|
| `event_type` | `str` | `FILL` |
| `timestamp_us` | `int` | Engine clock at fill time |
| `fill_id` | `str` | Monotonic ID, e.g., `fill_000042` |
| `order_id` | `str` | The resting order that was filled |
| `source_trade_idx` | `int` | Index of the Telonex trade event that triggered this fill |
| `strike` | `int` | Strike price |
| `token` | `str` | `YES` or `NO` |
| `side` | `str` | `BUY` or `SELL` |
| `price_ticks` | `int` | Fill price |
| `size_cs` | `int` | Fill size |
| `fill_type` | `str` | `PASSIVE` (resting order hit) or `AGGRESSIVE` (crossing the spread) |
| `fair_value_bps` | `int` | Model fair value at time of fill (in basis points) |
| `mid_ticks` | `int` | Polymarket midpoint at time of fill |
| `spread_ticks` | `int` | Book spread at time of fill |
| `position_before_cs` | `int` | Net position before this fill |
| `position_after_cs` | `int` | Net position after this fill |
| `cash_before_tc` | `int` | Cash before this fill |
| `cash_after_tc` | `int` | Cash after this fill |
| `queue_position_at_fill` | `int` | Queue position when filled |
| `depth_at_price_cs` | `int` | Total depth at the fill price level |

### 5.4 Fair Value Log Schema

Every fair value computation, logged when the model recalculates probabilities.

| Field | Type | Description |
|---|---|---|
| `event_type` | `str` | `FAIR_VALUE_UPDATE` |
| `timestamp_us` | `int` | Engine clock at computation time |
| `strike` | `int` | Strike price |
| `yes_fair_value_bps` | `int` | Probability of YES in basis points (0-10000) |
| `no_fair_value_bps` | `int` | `10000 - yes_fair_value_bps` |
| `yes_fair_value_ticks` | `int` | Rounded to ticks (0-100) |
| `model` | `str` | Which model produced this value (e.g., `black_scholes`, `breeden_litzenberger`) |
| `underlying_price_cents` | `int` | Underlying stock price at computation time |
| `sigma_bps` | `int` | Implied volatility used (in basis points, e.g., 5000 = 50%) |
| `time_to_expiry_s` | `int` | Seconds until market resolution |
| `risk_free_rate_bps` | `int` | Risk-free rate used |
| `options_snapshot_ts_us` | `int | None` | Timestamp of the options data used (for look-ahead audit) |

### 5.5 Book State Log Schema

Logged at each snapshot processed by the engine.

| Field | Type | Description |
|---|---|---|
| `event_type` | `str` | `BOOK_STATE` |
| `timestamp_us` | `int` | Snapshot timestamp |
| `strike` | `int` | Strike price |
| `token` | `str` | `YES` or `NO` |
| `best_bid_ticks` | `int` | Best bid price |
| `best_ask_ticks` | `int` | Best ask price |
| `best_bid_size_cs` | `int` | Size at best bid |
| `best_ask_size_cs` | `int` | Size at best ask |
| `bid_depth_total_cs` | `int` | Total bid depth across all levels |
| `ask_depth_total_cs` | `int` | Total ask depth across all levels |
| `spread_ticks` | `int` | `best_ask - best_bid` |
| `mid_ticks` | `int` | `(best_bid + best_ask) / 2` (rounded) |
| `data_quality` | `str` | `TRUSTED`, `DEGRADED`, or `INVALID` |

### 5.6 Queryability

The audit trail supports post-hoc queries. With JSONL persistence, use `jq` for ad-hoc queries or load into a DataFrame:

**Example queries:**
- "Show me all fills for NVDA >$165 YES where adverse selection at 60s > 2 cents":
  ```
  jq 'select(.event_type == "FILL" and .strike == 165 and .token == "YES")' journal.jsonl
  ```
  Then join with the adverse selection records and filter `as_60s_tc < -200` (2 cents in tick-cents).

- "Show all fair value updates where the model disagreed with market mid by > 5 cents":
  ```
  jq 'select(.event_type == "FAIR_VALUE_UPDATE" and
      ((.yes_fair_value_ticks - .mid_ticks) | fabs) > 5)' journal.jsonl
  ```

- "Show all order rejections":
  ```
  jq 'select(.event_type == "ORDER_REJECTED")' journal.jsonl
  ```

---

## 6. Validation Test Cases

### 6.1 NVDA POC Replay (Integration Test)

**Purpose:** Verify that the v1.0 engine produces results consistent with the POC when given the same data and strategy.

| Parameter | Value |
|---|---|
| Data | NVDA March 30, 2026 Telonex L2 snapshots and trades |
| Strategy | `ProbabilityBasedQuoting` with B-S fair value, sigma=50%, half_spread=0.02, min_edge=0.03, max_pos=50, order_size=10 |
| Fill simulator | L2-based (Phase 3) |
| Expected P&L | Close to -$18.10 (POC L2 result) |
| Expected fills | Close to 188 (POC L2 result) |
| Expected per-strike | $165: ~163 fills, final pos ~ -50 YES; $170: ~25 fills |

**Pass criteria:**
- Total P&L within $5 of POC result (-$18.10)
- Fill count within 20% of POC (150-225)
- Per-strike fill distribution qualitatively matches (most fills at $165, some at $170, zero at $160/$175/$180)
- $165 final position is net short YES (matching POC)

**Failure investigation:** If results diverge by more than the tolerance, compare the fill-by-fill log with POC output (`fills_l2.csv`) to identify where the engines diverge.

### 6.2 Synthetic Orderbook: Known Spread Capture

**Purpose:** Verify that spread capture accounting is correct with a deterministic, trivial scenario.

**Setup:**
- Single strike, YES token only
- Fair value = $0.50 (constant throughout)
- Strategy quotes bid=$0.48, ask=$0.52, size=10 each
- Synthetic book: best bid=$0.47, best ask=$0.53 (our orders are inside the spread)
- Inject trade events: alternating buy and sell trades that fill our orders

**Sequence:**
1. Trade 1: Market sell hits our bid at $0.48, size 10 -> BUY fill
2. Trade 2: Market buy lifts our ask at $0.52, size 10 -> SELL fill
3. Repeat 5 times (10 fills total: 5 buys at $0.48, 5 sells at $0.52)

**Expected outcome:**
- Total fills: 10
- Final position: 0 (perfectly balanced)
- Gross spread capture: 5 * 10 * ($0.52 - $0.48) = $2.00
- Inventory PnL: $0.00 (fair value constant, round-tripped)
- Resolution PnL: $0.00 (no position at resolution)
- Total PnL: $2.00

### 6.3 Synthetic Orderbook: Adverse Selection Measurement

**Purpose:** Verify adverse selection computation produces correct values when price moves are known.

**Setup:**
- Fair value starts at $0.50, then jumps to $0.55 after a buy fill
- Our bid at $0.48 gets filled (buy at $0.48)
- Midpoint moves from $0.50 to $0.55 over 60 seconds

**Expected outcome:**
- Spread component: ($0.50 - $0.48) * size = $0.02 per share (positive)
- AS at 60s: ($0.55 - $0.50) * size = $0.05 per share (positive -- this was actually *favorable* selection for a buy)
- Now reverse: fair value drops from $0.50 to $0.45 after the buy fill
- AS at 60s: ($0.45 - $0.50) * size = -$0.05 per share (negative -- genuine adverse selection)

Verify that the adverse selection ratio correctly classifies the first fill as non-adverse and the second as adverse.

### 6.4 Determinism Verification

**Purpose:** Confirm bitwise-identical outputs across two runs.

**Setup:**
- Run the NVDA POC Replay (Test 6.1) twice with identical configuration
- Compute SHA-256 hash of all output files

**Expected outcome:**
- `result_a.journal_hash == result_b.journal_hash`
- `result_a.final_cash_tc == result_b.final_cash_tc`
- `sha256(outputs_a) == sha256(outputs_b)`

### 6.5 No-Lookahead Audit

**Purpose:** Post-hoc verification that no data point used in a decision had a future timestamp.

**Method:**
1. For every `FAIR_VALUE_UPDATE` in the journal, verify:
   - `options_snapshot_ts_us <= timestamp_us` (options data is from the past)
   - `underlying_price` was available at or before `timestamp_us`
2. For every `ORDER_SUBMITTED`, verify:
   - The fair value used was computed before the order timestamp
   - The book state used for the strategy decision was from a snapshot with `snapshot_ts <= order_ts`
3. For every `FILL`, verify:
   - The fill was triggered by a trade event with `trade_ts >= order_visible_ts + latency` (order was visible before the trade arrived)

**Expected outcome:** Zero violations. Any violation is a bug in the data alignment (Phase 1) or the engine event loop (Phase 2).

### 6.6 Inventory Limit Enforcement

**Purpose:** Verify that the engine never allows positions to exceed configured limits.

**Setup:**
- max_position = 50 per strike
- Feed a stream of one-sided trades designed to fill all buy orders

**Expected outcome:**
- Position never exceeds 50
- Once at limit, orders on the accumulating side are rejected with reason `POSITION_LIMIT`
- Orders on the reducing side (sells when long) continue to be accepted

### 6.7 Resolution Settlement

**Purpose:** Verify correct settlement calculation for all resolution scenarios.

**Setup:**
- Create positions across 4 strikes: long YES at $160 (resolves YES), short YES at $165 (resolves YES), long YES at $170 (resolves NO), short YES at $175 (resolves NO)
- Trigger resolution

**Expected outcome:**
- $160 long YES: position * $1.00 (profitable if bought below $1.00)
- $165 short YES: -position * $1.00 (loss -- short the winning side)
- $170 long YES: position * $0.00 (total loss of cost basis)
- $175 short YES: -position * $0.00 (full retention of sale proceeds)
- Cash + settlement = total P&L; verify against decomposition

---

## 7. Output Format Specification

### 7.1 CSV Schemas

All CSV files use UTF-8 encoding, comma delimiter, and include a header row. Timestamps are in microseconds (integer). Monetary values are in dollars (float, 6 decimal places) for human readability; the engine internally uses integer tick-cents.

#### `fills.csv`

Extends the POC format (`order_id,strike,side,price,size,fill_timestamp_us,order_timestamp_us`) with full context:

| Column | Type | Description |
|---|---|---|
| `fill_id` | `str` | `fill_000001`, `fill_000002`, ... |
| `order_id` | `str` | `ord_000001`, ... |
| `timestamp_us` | `int` | Fill timestamp in microseconds |
| `strike` | `int` | Strike price |
| `token` | `str` | `YES` or `NO` |
| `side` | `str` | `BUY` or `SELL` |
| `price` | `float` | Fill price in dollars (e.g., 0.48) |
| `size` | `float` | Fill size in shares |
| `fill_type` | `str` | `PASSIVE` or `AGGRESSIVE` |
| `fair_value` | `float` | Model fair value at fill time |
| `mid` | `float` | Polymarket midpoint at fill time |
| `spread` | `float` | Book spread at fill time |
| `position_after` | `float` | Net position after fill |
| `cash_after` | `float` | Cash after fill |
| `spread_capture` | `float` | Spread component of this fill (dollars) |
| `as_60s` | `float` | Adverse selection at 60s post-fill (dollars) |
| `as_5min` | `float` | Adverse selection at 5min post-fill (dollars) |

#### `pnl_history.csv`

Extends the POC format (`timestamp_us,cash,mtm_pnl,n_fills,pos_160,...`) with decomposition:

| Column | Type | Description |
|---|---|---|
| `timestamp_us` | `int` | Timestamp |
| `cash` | `float` | Accumulated cash from fills |
| `unrealized_pnl_fair` | `float` | Mark-to-market at model fair value |
| `unrealized_pnl_mid` | `float` | Mark-to-market at Polymarket midpoint |
| `total_pnl_fair` | `float` | `cash + unrealized_pnl_fair` |
| `total_pnl_mid` | `float` | `cash + unrealized_pnl_mid` |
| `cum_spread_capture` | `float` | Cumulative spread capture |
| `cum_adverse_selection` | `float` | Cumulative adverse selection cost |
| `cum_inventory_pnl` | `float` | Cumulative inventory mark-to-market |
| `n_fills` | `int` | Cumulative fill count |
| `pos_{strike}` | `float` | Net position per strike (one column per strike) |

#### `adverse_selection.csv`

Per-fill adverse selection decomposition at multiple horizons:

| Column | Type | Description |
|---|---|---|
| `fill_id` | `str` | Links to `fills.csv` |
| `timestamp_us` | `int` | Fill timestamp |
| `strike` | `int` | Strike |
| `token` | `str` | `YES` or `NO` |
| `side` | `str` | `BUY` or `SELL` |
| `price` | `float` | Fill price |
| `size` | `float` | Fill size |
| `mid_at_fill` | `float` | Midpoint at fill |
| `mid_t1s` | `float` | Midpoint at t+1s |
| `mid_t10s` | `float` | Midpoint at t+10s |
| `mid_t60s` | `float` | Midpoint at t+60s |
| `mid_t5min` | `float` | Midpoint at t+5min |
| `mid_t15min` | `float` | Midpoint at t+15min |
| `mid_t30min` | `float` | Midpoint at t+30min |
| `mid_t60min` | `float` | Midpoint at t+60min |
| `is_adverse_60s` | `bool` | True if mid moved against us by 60s |
| `is_adverse_5min` | `bool` | True if mid moved against us by 5min |

#### `fair_values.csv`

Time series of model fair values and Polymarket midpoints for each strike:

| Column | Type | Description |
|---|---|---|
| `timestamp_us` | `int` | Timestamp |
| `underlying_price` | `float` | Stock price |
| `sigma` | `float` | Implied volatility used |
| `time_to_expiry_s` | `int` | Seconds to expiry |
| `{strike}_fv` | `float` | Fair value per strike |
| `{strike}_mid` | `float` | Polymarket midpoint per strike |
| `{strike}_edge` | `float` | `fv - mid` per strike (positive = YES underpriced) |

#### `inventory.csv`

Position and inventory metrics over time:

| Column | Type | Description |
|---|---|---|
| `timestamp_us` | `int` | Timestamp |
| `{strike}_yes_pos` | `float` | YES position per strike |
| `{strike}_no_pos` | `float` | NO position per strike |
| `{strike}_net` | `float` | Net YES exposure per strike |
| `{strike}_boxed` | `float` | Boxed (hedged) position per strike |
| `total_abs_inventory` | `float` | Sum of absolute positions |
| `total_capital_deployed` | `float` | Capital locked in positions |

### 7.2 Parquet Schemas

For large datasets (multi-day, multi-ticker backtests), output Parquet files with zstd compression. The schemas mirror the CSV schemas above. Parquet files are written using `pyarrow` with the following conventions:

- Timestamps stored as `int64` (microseconds, not datetime)
- Monetary values stored as `int64` tick-cents (no float conversion) for precision
- String columns as `dictionary`-encoded for compression
- Partition by `(underlying_ticker, date)` for multi-day runs
- Row group size: 10,000 rows

### 7.3 Journal Output

The full audit trail is written to `journal.jsonl` (one JSON object per line, canonical sorted keys). For large backtests, this file can be substantial (100MB+); it is not loaded into memory after the simulation completes.

For quick comparison across runs, the engine writes `run_manifest.json` containing:

```json
{
    "run_id": "run_20260402_143022_abc123",
    "engine_version": "1.0.0",
    "config_hash": "sha256:...",
    "data_hash": "sha256:...",
    "output_hash": "sha256:...",
    "journal_hash": "sha256:...",
    "started_at": "2026-04-02T14:30:22Z",
    "completed_at": "2026-04-02T14:30:45Z",
    "duration_s": 23.4,
    "total_fills": 188,
    "total_pnl_dollars": -18.10,
    "determinism_verified": true
}
```

---

## 8. Reporting Template

### 8.1 Summary Report

Generated as a Markdown file after each backtest run. Contains all key information needed to evaluate whether a strategy is viable.

```markdown
# Backtest Report: {strategy_name}

## Run Info
- **Run ID:** {run_id}
- **Engine Version:** {engine_version}
- **Date:** {run_date}
- **Duration:** {duration_s}s
- **Determinism Verified:** {yes/no}
- **Output Hash:** {hash}

## Data
- **Underlying:** {ticker}
- **Strikes:** {strike_list}
- **Period:** {start_date} to {end_date}
- **Data Quality:** {pct_trusted}% TRUSTED, {pct_degraded}% DEGRADED, {pct_invalid}% INVALID

## Strategy Parameters
| Parameter | Value |
|-----------|-------|
| Strategy | {strategy_name} |
| Fair value model | {model_name} |
| Half-spread | ${half_spread} |
| Min edge | ${min_edge} |
| Max position | {max_position} |
| Order size | {order_size} |
| ... | ... |

## Performance Summary
| Metric | Value | Status |
|--------|-------|--------|
| Total P&L | ${total_pnl} | {OK/WARNING/ALERT} |
| Sharpe Ratio | {sharpe} | {OK if 1.5-3.0, SUSPICIOUS if >5} |
| Sortino Ratio | {sortino} | |
| Max Drawdown | {max_dd}% | {OK if 5-15%, WARNING if >15%} |
| Profit Factor | {pf} | {OK if 1.3-2.0} |
| Return on Capital | {roc}% | |

## P&L Decomposition
| Component | Value | % of Gross Revenue |
|-----------|-------|--------------------|
| Gross Spread Capture | ${spread_capture} | 100% (baseline) |
| Adverse Selection | ${adverse_selection} | {as_pct}% |
| Net Spread Capture | ${net_spread} | {nsc_pct}% |
| Inventory P&L | ${inv_pnl} | {inv_pct}% |
| Resolution P&L | ${res_pnl} | {res_pct}% |
| Fees | ${fees} | {fee_pct}% |
| **Total** | **${total}** | |

## Fill Quality
| Metric | Value | Status |
|--------|-------|--------|
| Total Fills | {n_fills} | |
| Fill Rate | {fill_rate}% | {OK if 5-30%, RED if >50%} |
| Fill Asymmetry | {asymmetry} | {OK if <0.4} |
| Adverse Selection Rate (60s) | {as_rate_60s}% | {RED if <40%} |
| Adverse Selection Rate (5min) | {as_rate_5min}% | |
| Realized Spread (per fill) | ${realized_spread} | {OK if >0} |

## Per-Strike Breakdown
| Strike | Fills | Buy | Sell | Final Pos | Cash | Settlement | P&L |
|--------|-------|-----|------|-----------|------|------------|-----|
{per_strike_rows}

## Inventory Profile
| Metric | Value |
|--------|-------|
| Max |Position| | {max_inv} |
| Avg |Position| | {avg_inv} |
| Time at Limit | {time_at_limit}% |
| Inventory Half-Life | {half_life} |

## Red Flags
{list of any metrics that crossed alert thresholds, with explanations}
```

### 8.2 Comparison Report

When running strategy A vs strategy B on the same data:

```markdown
# Comparison Report: {strategy_a} vs {strategy_b}

## Data (identical for both runs)
- **Output Hash Match on Data:** {yes/no}

## Side-by-Side
| Metric | {strategy_a} | {strategy_b} | Delta |
|--------|-------------|-------------|-------|
| Total P&L | ${a_pnl} | ${b_pnl} | ${delta} |
| Sharpe | {a_sharpe} | {b_sharpe} | {delta} |
| Fill Rate | {a_fr}% | {b_fr}% | {delta}pp |
| AS Rate (60s) | {a_as}% | {b_as}% | {delta}pp |
| Max Drawdown | {a_dd}% | {b_dd}% | {delta}pp |
| Profit Factor | {a_pf} | {b_pf} | {delta} |
{...all metrics from the summary report}

## P&L Decomposition Comparison
{side-by-side decomposition tables}

## Per-Strike Comparison
{per-strike tables for each strategy}

## Conclusion
{auto-generated summary: which strategy wins on which dimensions}
```

---

## 9. Task Breakdown

### Task 5.1: P&L Tracking Core

**Files:** `bt_engine/analytics/pnl.py`

1. Implement `PnLTracker` class that hooks into the engine event loop
2. Track real-time unrealized P&L at both fair value and Polymarket midpoint
3. Track realized P&L per fill (cash flow accounting)
4. Compute settlement P&L at resolution
5. Maintain running decomposition: spread capture, inventory P&L, resolution P&L, fees
6. Implement the accounting identity consistency check (Section 2.3)
7. Support per-strike, per-token, and portfolio-level aggregation

**Depends on:** Phase 2 (engine event loop), Phase 4 (fair value availability)

### Task 5.2: Adverse Selection Analyzer

**Files:** `bt_engine/analytics/adverse_selection.py`

1. Implement `AdverseSelectionAnalyzer` class
2. For each fill, look up future midpoints at t+1s, t+10s, t+60s, t+5min, t+15min, t+30min, t+60min
3. Compute per-fill adverse selection records (Section 3.3 schema)
4. Compute aggregate statistics: AS ratio, mean/median AS cost, AS cost / spread ratio
5. Compute edge decay profile
6. Implement breakdown by fill size, time of day, and distance from ATM
7. Output `adverse_selection.csv`

**Depends on:** Phase 1 (midpoint lookup from book snapshots), Phase 3 (fill records)

### Task 5.3: Performance Metrics Calculator

**Files:** `bt_engine/analytics/metrics.py`

1. Implement Sharpe ratio, Sortino ratio, max drawdown, Calmar ratio, profit factor
2. Implement fill quality metrics: fill rate, fill asymmetry, realized spread, effective spread
3. Implement inventory metrics: max inventory, avg inventory, time at limit, half-life, turnover
4. Implement binary-specific metrics: Brier score, calibration gap, log loss
5. Implement return on locked capital
6. Implement win/loss ratio per market, per strategy
7. Implement alert threshold checks (flag any metric crossing thresholds from Section 1)

**Depends on:** Task 5.1 (P&L data), Task 5.2 (adverse selection data)

### Task 5.4: Audit Trail Implementation

**Files:** `bt_engine/journal/journal.py`, `bt_engine/journal/schemas.py`

1. Define all journal entry types as frozen dataclasses (Section 5 schemas)
2. Implement `Journal` class with `MEMORY` and `FILE` (JSONL) modes
3. Integrate journal hooks into the engine: order lifecycle, fills, fair values, book states
4. Implement journal hash computation (SHA-256 over canonical JSON)
5. Implement JSONL writer with canonical sorted keys
6. Implement `run_manifest.json` generation

**Depends on:** Phase 2 (engine event hooks exist)

### Task 5.5: Determinism Verification

**Files:** `bt_engine/verification/determinism.py`

1. Implement `verify_determinism()` function (Section 4.3)
2. Implement SHA-256 output hashing
3. Add `PYTHONHASHSEED=0` enforcement in the engine entry point
4. Create CI test that runs determinism verification on the NVDA POC dataset
5. Document all determinism guarantees and risks in a code-level docstring

**Depends on:** Task 5.4 (journal hash), Phase 2 (engine must be fully functional)

### Task 5.6: No-Lookahead Audit

**Files:** `bt_engine/verification/lookahead.py`

1. Implement post-hoc timestamp audit (Section 6.5)
2. Scan all `FAIR_VALUE_UPDATE` entries for future-dated options data
3. Scan all `ORDER_SUBMITTED` entries for future-dated book state
4. Scan all `FILL` entries for orders that weren't visible yet
5. Output a `lookahead_audit.json` report with zero violations expected

**Depends on:** Task 5.4 (journal must contain all entries with timestamps)

### Task 5.7: Validation Test Suite

**Files:** `tests/test_analytics.py`, `tests/test_validation.py`, `tests/fixtures/`

1. Implement NVDA POC Replay test (Section 6.1)
2. Implement Synthetic Spread Capture test (Section 6.2)
3. Implement Synthetic Adverse Selection test (Section 6.3)
4. Implement Determinism Verification test (Section 6.4)
5. Implement Inventory Limit Enforcement test (Section 6.6)
6. Implement Resolution Settlement test (Section 6.7)
7. Create synthetic orderbook fixtures for tests 6.2, 6.3, 6.6, 6.7

**Depends on:** Tasks 5.1-5.6 (all analytics and verification code)

### Task 5.8: CSV/Parquet Output Writers

**Files:** `bt_engine/output/csv_writer.py`, `bt_engine/output/parquet_writer.py`

1. Implement CSV writers for all schemas in Section 7.1 (`fills.csv`, `pnl_history.csv`, `adverse_selection.csv`, `fair_values.csv`, `inventory.csv`)
2. Implement Parquet writers mirroring CSV schemas with zstd compression
3. Implement partitioning by `(underlying_ticker, date)` for multi-day runs
4. Verify output matches POC format for backward compatibility (POC columns are a subset of v1.0 columns)

**Depends on:** Tasks 5.1-5.3 (analytics data to write)

### Task 5.9: Report Generator

**Files:** `bt_engine/output/report.py`

1. Implement summary report generator (Section 8.1 template)
2. Implement comparison report generator (Section 8.2 template)
3. Implement red flag detection (auto-flag metrics crossing alert thresholds)
4. Implement status classification (OK / WARNING / ALERT) for each metric
5. Output as Markdown file in the run output directory

**Depends on:** Tasks 5.1-5.3 (all metrics computed), Task 5.8 (output directory structure)

### Task 5.10: Integration and End-to-End Test

**Files:** `tests/test_e2e.py`

1. Run full engine pipeline on NVDA March 30 data: data load -> event loop -> fills -> analytics -> outputs
2. Verify all output files are produced and non-empty
3. Verify report contains all required sections
4. Verify determinism (two runs produce identical hashes)
5. Verify no-lookahead audit passes
6. Verify accounting identity holds (decomposition sums to total)
7. Verify all per-strike P&L sums to portfolio P&L

**Depends on:** All tasks 5.1-5.9

---

## 10. Acceptance Criteria

Phase 5 is complete when:

1. **P&L Decomposition**: Five-component decomposition (spread capture, adverse selection, inventory, resolution, fees) sums to total P&L within 1 tick tolerance for every backtest run
2. **Adverse Selection**: Multi-horizon AS measurement (1s through 60min) is computed for every fill and output to `adverse_selection.csv`
3. **Metrics**: All metrics in Section 1 are computed and included in the summary report, with alert thresholds flagged
4. **Determinism**: Two identical runs produce bitwise-identical outputs (verified by SHA-256 hash comparison)
5. **Audit Trail**: Every order, fill, fair value update, and book state change is logged to the journal with full context
6. **No-Lookahead**: Post-hoc audit confirms zero timestamp violations
7. **NVDA Replay**: v1.0 engine reproduces POC results within tolerance (Section 6.1 criteria)
8. **Validation Tests**: All 7 test cases pass
9. **Output Files**: All CSV/Parquet files are produced with correct schemas
10. **Report**: Summary report is generated with all sections populated, red flags identified

---

## References

- [[Performance-Metrics-and-Pitfalls]] -- P&L decomposition methodology, risk metrics, pitfall taxonomy, statistical rigor
- [[NVDA-POC-Results]] -- POC metrics, per-strike breakdown, L2 vs midpoint comparison, lessons learned
- [[Engine-Architecture-Plan]] -- Sections 10 (Analytics), 13 (Determinism), 14 (Audit Trail)
- [[Capital-Efficiency-and-Edge-Cases]] -- Return metrics, capital lockup, opportunity cost, Sharpe estimation
- [[Order-Flow-Analysis-Strategies]] -- VPIN, adverse selection measurement, Glosten-Harris model
- [[Inventory-and-Risk-Management]] -- Risk monitoring dashboard, inventory decay targets, portfolio delta aggregation
