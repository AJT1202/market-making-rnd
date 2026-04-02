---
title: "Fill Simulation Research: Trade-Driven Backtesting for Market Making"
created: 2026-03-31
updated: 2026-03-31
tags:
  - fill-simulation
  - backtesting
  - market-microstructure
  - queue-position
  - adverse-selection
  - market-making
  - polymarket
  - research
  - hftbacktest
  - telonex
  - dual-orderbook
  - trade-driven
sources:
  - "Lalor & Swishchuk (2024) — Market Simulation under Adverse Selection"
  - "Cont, Stoikov & Talreja (2010) — A Stochastic Model for Order Book Dynamics"
  - "Avellaneda & Stoikov (2008) — High-Frequency Trading in a Limit Order Book"
  - "Moallemi & Yuan (2016) — A Model for Queue Position Valuation"
  - "Taranto et al. (2024) — The Negative Drift of a Limit Order Fill"
  - "Kolm et al. (2022) — A Deep Learning Approach to Estimating Fill Probabilities"
  - "HftBacktest — nkaz001/hftbacktest"
  - "NautilusTrader — Polymarket Adapter"
---

# Fill Simulation Research: Trade-Driven Backtesting for Market Making

How to realistically simulate order fills in a market making backtesting engine using Telonex's `book_snapshot_full` and `trades` channels. Covers trade-driven fill triggers, queue position modeling with real depth, adverse selection, latency handling, dual YES/NO orderbook management, fill probability calibration, and multi-channel data fusion.

**Context:** Our backtesting engine targets Polymarket binary event markets. We will use TWO Telonex data channels:

1. **`book_snapshot_full`** -- Complete orderbook snapshots (all depth levels) captured at every change (~0.1-3 second intervals, 20K-39K snapshots/day per market)
2. **`trades`** -- Every executed trade with timestamp, price, size, and taker side

Each Polymarket binary market has **two independent orderbooks** (YES token and NO token), each with its own bid/ask ladder. The engine must maintain dual books per market. See [[Backtesting-Architecture]] for the engine design, [[Orderbook-Backtesting-with-Telonex]] for data integration, and [[Performance-Metrics-and-Pitfalls]] for evaluation methodology.

**Motivation:** Our POC demonstrated that fill simulation is the single most important component. The midpoint simulator overstated P&L by $638.80 and fills by 16.5x compared to the L2 simulator (see [[NVDA-POC-Results]]).

> [!important] Primary Recommendation
> With both `book_snapshot_full` and `trades` available, the **hybrid trade-driven approach** (Section 7, Approach 5) is the primary recommendation. Trade events serve as fill triggers (solving the phantom fill problem), while snapshots provide real depth for queue position modeling. This closely mirrors the BTC engine's proven 7-condition fill architecture. Snapshot-only approaches remain as fallback/baseline.

---

## 1. Snapshot-Based Fill Detection

### 1.1 The Fundamental Problem

With snapshot data, we observe the orderbook state at discrete points $t_0, t_1, t_2, \ldots$ separated by $\Delta t \approx 0.1\text{--}3$ seconds. Between any two consecutive snapshots, multiple events may have occurred:

- Limit orders placed, modified, or cancelled
- Market orders consuming resting depth
- Multiple trades at different price levels

We see the **result** of these events (the new book state) but not the individual events themselves. This creates three interrelated problems:

| Problem                     | Description                                       | Impact on Fill Simulation                           |
| --------------------------- | ------------------------------------------------- | --------------------------------------------------- |
| **Missing trades**          | Trades between snapshots are invisible            | Cannot use trade-tick matching for fills            |
| **Ambiguous depth changes** | Depth decrease could be cancellation OR execution | Overstates fills if all decreases treated as trades |
| **Temporal aliasing**       | Multiple events collapsed into one state change   | Cannot determine order of events within interval    |

### 1.2 Inferring Trades from Depth Changes

When depth at a price level decreases between snapshots $t_i$ and $t_{i+1}$, we can attempt to classify the cause:

```python
def classify_depth_change(
    prev_snapshot: dict,
    curr_snapshot: dict,
    price_level: float,
    side: str,
) -> str:
    """
    Heuristic classification of depth decreases.

    Returns: 'likely_trade', 'likely_cancel', or 'ambiguous'
    """
    prev_depth = get_depth_at(prev_snapshot, side, price_level)
    curr_depth = get_depth_at(curr_snapshot, side, price_level)

    if curr_depth >= prev_depth:
        return 'no_decrease'

    decrease = prev_depth - curr_depth
    prev_bbo = get_bbo(prev_snapshot)
    curr_bbo = get_bbo(curr_snapshot)

    # Signal 1: BBO moved through this level
    # If best ask was at 0.52 and is now at 0.53, depth at 0.52 was likely
    # consumed by a buy market order
    if side == 'ask' and curr_bbo['best_ask'] > prev_bbo['best_ask']:
        if price_level <= prev_bbo['best_ask']:
            return 'likely_trade'
    if side == 'bid' and curr_bbo['best_bid'] < prev_bbo['best_bid']:
        if price_level >= prev_bbo['best_bid']:
            return 'likely_trade'

    # Signal 2: Level at BBO disappeared entirely
    if curr_depth == 0 and price_level == prev_bbo[f'best_{side}']:
        # Could be trade consuming all depth, or mass cancellation
        # Check if opposite side also changed (suggests trade)
        opp_side = 'bid' if side == 'ask' else 'ask'
        opp_changed = get_depth_at(curr_snapshot, opp_side,
                                    prev_bbo[f'best_{opp_side}']) != \
                      get_depth_at(prev_snapshot, opp_side,
                                    prev_bbo[f'best_{opp_side}'])
        return 'likely_trade' if not opp_changed else 'ambiguous'

    # Signal 3: Depth decreased at a level behind the BBO
    # Cancellations are more common behind the BBO
    if side == 'bid' and price_level < prev_bbo['best_bid']:
        return 'likely_cancel'
    if side == 'ask' and price_level > prev_bbo['best_ask']:
        return 'likely_cancel'

    return 'ambiguous'
```

**Accuracy of this heuristic:** Research on matching trades to orderbook changes reports approximately 85% accuracy for liquid equities (CAC-40 stocks). For thin prediction markets, accuracy may be lower due to larger relative tick sizes and sparser depth.

### 1.3 BBO-Based Fill Detection

A simpler and more conservative approach: detect fills only when the BBO changes in a way that implies a marketable order crossed the spread.

```python
def detect_bbo_fill_signal(
    prev_snapshot: dict,
    curr_snapshot: dict,
) -> dict:
    """
    Detect fill signals from BBO changes between consecutive snapshots.

    Returns signals for bid-side and ask-side fills.
    """
    prev_bid = float(prev_snapshot['bid_price_0'])
    prev_ask = float(prev_snapshot['ask_price_0'])
    curr_bid = float(curr_snapshot['bid_price_0'])
    curr_ask = float(curr_snapshot['ask_price_0'])

    prev_bid_size = float(prev_snapshot['bid_size_0'])
    prev_ask_size = float(prev_snapshot['ask_size_0'])
    curr_bid_size = float(curr_snapshot['bid_size_0'])
    curr_ask_size = float(curr_snapshot['ask_size_0'])

    signals = {'bid_fill': False, 'ask_fill': False,
               'bid_volume_estimate': 0, 'ask_volume_estimate': 0}

    # Ask-side fill: someone bought aggressively
    # Evidence: best ask price increased OR best ask depth decreased at same price
    if curr_ask > prev_ask:
        # Ask level was fully consumed — trade ate through it
        signals['ask_fill'] = True
        signals['ask_volume_estimate'] = prev_ask_size  # At minimum, this much traded
    elif curr_ask == prev_ask and curr_ask_size < prev_ask_size:
        # Partial consumption at same price — trade or cancellation
        signals['ask_fill'] = True  # Conservative: treat as trade
        signals['ask_volume_estimate'] = prev_ask_size - curr_ask_size

    # Bid-side fill: someone sold aggressively
    if curr_bid < prev_bid:
        signals['bid_fill'] = True
        signals['bid_volume_estimate'] = prev_bid_size
    elif curr_bid == prev_bid and curr_bid_size < prev_bid_size:
        signals['bid_fill'] = True
        signals['bid_volume_estimate'] = prev_bid_size - curr_bid_size

    return signals
```

### 1.4 The Phantom Fill Problem

> [!warning] Critical Issue (Snapshot-Only Mode)
> Distinguishing cancellations from trades is the central challenge of snapshot-based fill simulation. Both reduce depth at a price level, but only trades should trigger fills.

**Cancellation characteristics** (heuristics for classification):

| Signal | Suggests Cancellation | Suggests Trade |
|--------|----------------------|----------------|
| BBO unchanged | Yes | No |
| Depth decrease behind BBO | Yes (common) | Rare |
| Depth decrease at BBO with spread widening | Possible | More likely |
| Complete level removal at BBO + price improvement on opposite side | Unlikely | Highly likely |
| Symmetric depth decrease (both sides) | Likely (volatility pullback) | Unlikely |
| Large depth decrease + BBO shift | Possible (spoofing pullback) | Likely (sweep) |

**Quantitative estimate:** In our Telonex data, the $165 strike had 39,346 snapshots with ~163 actual fills per the L2 simulator. If we naively treated every BBO depth decrease as a trade, we would generate vastly more fill signals than actual trades — the same overcounting that caused the midpoint simulator's 16.5x fill inflation.

> [!success] Resolution: Trade Data Solves the Phantom Fill Problem
> With the `trades` channel available, the phantom fill problem is **completely solvable**. We use actual trade events as fill triggers rather than inferring trades from depth changes. This is identical to the BTC engine's approach where `last_trade_price` events drive fills and depth changes (`price_change`) never generate fills. See Section 7, Approach 5 (Hybrid) for the primary recommended implementation.

### 1.5 Dual Orderbook Considerations

Each Polymarket binary market has **two independent orderbooks** -- one for the YES token and one for the NO token. Fill simulation must account for this structure:

```python
@dataclass
class DualOrderbook:
    """
    Two independent orderbooks per binary market.

    YES token: bid = want to buy YES, ask = want to sell YES
    NO token:  bid = want to buy NO,  ask = want to sell NO

    Cross-leg parity:
      YES_ask + NO_ask >= 1.00  (always, or arbitrage exists)
      YES_bid + NO_bid <= 1.00  (always, or arbitrage exists)
    """
    yes_book: 'TokenBook'  # Orderbook for YES outcome token
    no_book: 'TokenBook'   # Orderbook for NO outcome token

    def validate_parity(self) -> dict:
        """
        Cross-leg parity check.
        Violations indicate data quality issues or arbitrage.
        """
        yes_ask = self.yes_book.best_ask
        no_ask = self.no_book.best_ask
        yes_bid = self.yes_book.best_bid
        no_bid = self.no_book.best_bid

        return {
            'ask_sum': yes_ask + no_ask,      # Should be >= 1.00
            'bid_sum': yes_bid + no_bid,        # Should be <= 1.00
            'ask_parity_ok': yes_ask + no_ask >= 1.00 - 0.005,
            'bid_parity_ok': yes_bid + no_bid <= 1.00 + 0.005,
            'synthetic_spread': (yes_ask + no_ask) - (yes_bid + no_bid),
        }
```

**Fill simulation implications of dual orderbooks:**

| Concern | Impact |
|---------|--------|
| Independent queues | Queue position tracked separately per token book |
| Independent trades | A YES trade does NOT drain queue on the NO book |
| Cross-leg fills | Buying YES at 0.45 AND NO at 0.45 = boxed position worth $1.00 (risk-free $0.10 profit) |
| Arbitrage fills | If YES_ask + NO_ask < 1.00, buying both is a guaranteed profit |
| Settlement | YES resolves to $1 or $0; NO resolves to $1 - YES |
| Position netting | Holding 50 YES + 50 NO = $50 locked capital, can be redeemed |

### 1.6 Recommendation: Fill Detection Strategy

**Primary (with trades data):** Use trade events as fill triggers. Snapshots provide book state for queue position modeling. This is the hybrid approach (Section 7, Approach 5).

**Fallback (snapshot-only):** Use a two-tier detection system:

1. **Definite fill signal:** Price crossed through our level (best ask moved above our bid price, or best bid moved below our ask price). This is equivalent to our POC's L2 simulator.

2. **Probable fill signal:** Depth at our price level decreased AND the BBO moved in the direction consistent with a trade consuming that depth. Apply a discount factor $\rho \in (0.3, 0.7)$ to these signals.

3. **Ignore:** All other depth changes (likely cancellations or re-quoting).

---

## 2. Queue Position Models for Snapshot Data

### 2.1 The Queue Position Problem

When our order rests at price level $P$, other orders ahead of us must be consumed before we can be filled. With both `trades` and `book_snapshot_full` available, we can use **trade-driven queue drain** (the gold standard, matching the BTC engine) enhanced by **real depth data** for queue initialization and validation.

For snapshot-only fallback, we must infer queue drain from depth changes between snapshots -- but depth changes conflate trades and cancellations. The trade-driven approach avoids this problem entirely.

### 2.2 The BTC Engine's Approach (Reference)

The BTC engine uses an explicit queue model with three modes:

| Mode | Initial Queue Position | Queue Drain |
|------|----------------------|-------------|
| `CONSERVATIVE` | `queue_ahead = displayed_size` (back of queue) | Only through trades |
| `PROBABILISTIC` | `queue_ahead = randint(0, displayed_size)` | Only through trades |
| `OPTIMISTIC` | `queue_ahead = 0` (front of queue) | Only through trades |

Key design decision: **cancellations do NOT improve queue position.** Only actual trades consuming depth ahead drain the queue. This is conservative and prevents the phantom fill problem.

### 2.3 Primary Recommendation: Trade-Driven Queue with Real Depth

With both `trades` and `book_snapshot_full` available, we combine the BTC engine's trade-driven queue drain with real depth from snapshots. This is the recommended approach for our Polymarket engine.

```python
class TradeAndSnapshotQueueModel:
    """
    Queue position model using trade events for drain
    and full book snapshots for depth initialization.

    Maintains independent queue state per token (YES/NO).
    Mirrors the BTC engine's 7-condition fill model.
    """

    def __init__(self, position_model: str = 'CONSERVATIVE'):
        self.position_model = position_model
        # Separate queue tracking per token book
        self.yes_orders = {}  # order_id -> QueueState
        self.no_orders = {}   # order_id -> QueueState

    def on_order_active(
        self,
        order_id: str,
        token_side: str,   # 'YES' or 'NO'
        book_side: str,     # 'buy' or 'sell'
        price: float,
        size: float,
        current_book: dict,  # Latest book_snapshot_full for this token
    ):
        """
        Assign initial queue position using real depth from snapshot.

        Called when order transitions PENDING_SUBMIT -> ACTIVE at visible_ts.
        """
        depth_at_level = self._get_depth_at_price(current_book, book_side, price)

        if self.position_model == 'CONSERVATIVE':
            queue_ahead = depth_at_level  # Back of queue (worst case)
        elif self.position_model == 'PROBABILISTIC':
            queue_ahead = depth_at_level * np.random.uniform(0.5, 1.0)
        elif self.position_model == 'OPTIMISTIC':
            queue_ahead = 0  # Front of queue (sanity check only)
        else:
            queue_ahead = depth_at_level

        state = QueueState(
            order_id=order_id,
            token_side=token_side,
            book_side=book_side,
            price=price,
            remaining_size=size,
            queue_ahead=queue_ahead,
        )

        orders = self.yes_orders if token_side == 'YES' else self.no_orders
        orders[order_id] = state

    def on_trade(
        self,
        trade: dict,       # {price, size, side, asset_id, timestamp}
        token_side: str,    # 'YES' or 'NO' — resolved from asset_id
    ) -> list:
        """
        Process a trade event from the Telonex trades channel.

        Trades on the YES book only affect YES orders.
        Trades on the NO book only affect NO orders.

        Returns list of fill events.
        """
        orders = self.yes_orders if token_side == 'YES' else self.no_orders
        fills = []

        for order_id, state in list(orders.items()):
            # BTC engine conditions 1-5:
            if not state.is_fillable():                        # Condition 1
                continue
            if trade['timestamp'] < state.visible_ts:          # Condition 2
                continue
            if state.cancel_effective_reached(trade['timestamp']):  # Condition 3
                continue
            if trade['price'] != state.price:                  # Condition 4
                continue
            if not self._trade_direction_compatible(trade, state):  # Condition 5
                continue

            # Condition 6: Queue drain
            original_queue = state.queue_ahead
            consumed = min(state.queue_ahead, trade['size'])
            state.queue_ahead -= consumed

            if state.queue_ahead > 0:
                continue  # Still waiting in queue

            # Condition 7: Sufficient remaining trade size
            passthrough = max(0, trade['size'] - original_queue)
            fill_size = min(state.remaining_size, passthrough)

            if fill_size <= 0:
                continue

            fills.append({
                'order_id': order_id,
                'token_side': token_side,
                'side': state.book_side,
                'price': state.price,
                'size': fill_size,
                'timestamp': trade['timestamp'],
                'aggressor_side': trade['side'],
                'source_trade': trade,
            })

            state.remaining_size -= fill_size
            if state.remaining_size <= 0:
                del orders[order_id]

        return fills

    def _trade_direction_compatible(self, trade: dict, state) -> bool:
        """
        Taker BUY fills resting SELL orders.
        Taker SELL fills resting BUY orders.
        """
        if state.book_side == 'buy':
            return trade['side'] == 'sell'
        else:
            return trade['side'] == 'buy'

    def _get_depth_at_price(
        self, book: dict, side: str, price: float
    ) -> float:
        """Get total resting depth at a price level from snapshot."""
        book_side = 'bids' if side == 'buy' else 'asks'
        for level in book.get(book_side, []):
            if abs(float(level['price']) - price) < 1e-6:
                return float(level['size'])
        return 0.0


@dataclass
class QueueState:
    order_id: str
    token_side: str   # 'YES' or 'NO'
    book_side: str    # 'buy' or 'sell'
    price: float
    remaining_size: float
    queue_ahead: float
    status: str = 'ACTIVE'
    visible_ts: int = 0
    cancel_effective_ts: int = None

    def is_fillable(self) -> bool:
        return self.status in ('ACTIVE', 'PARTIALLY_FILLED', 'PENDING_CANCEL')

    def cancel_effective_reached(self, ts: int) -> bool:
        return (self.cancel_effective_ts is not None and
                ts >= self.cancel_effective_ts)
```

> [!important] Key Advantage Over Snapshot-Only
> With actual trade events, queue drain is **deterministic and accurate** -- no probability models needed, no cancel_discount tuning, no phantom fills. The queue position model from the BTC engine (Section 2.2) applies directly. Snapshots provide the real depth for initial queue assignment, replacing the BTC engine's `displayed_size` from reconstructed books.

### 2.4 HftBacktest's Probability Queue Model (Snapshot-Only Fallback)

HftBacktest (the leading open-source HFT backtesting framework) uses a sophisticated probabilistic approach that handles L2 snapshot data natively. The core insight: when depth at a level decreases, a probability model determines how much of that decrease occurred **ahead** of vs **behind** our order.

#### The Probability Function

Given an order with `front_q_qty` units ahead and `back_q_qty` units behind:

$$\text{prob}(\text{front}, \text{back}) = \frac{f(\text{back})}{f(\text{back}) + f(\text{front})}$$

where $f$ is a shaping function:

| Model | $f(x)$ | Behavior |
|-------|--------|----------|
| `PowerProbQueueFunc` ($n=1$) | $x$ | Linear — uniform probability |
| `PowerProbQueueFunc` ($n=2$) | $x^2$ | Quadratic — more depth decreases happen behind our order |
| `LogProbQueueFunc` | $\ln(1 + x)$ | Logarithmic — gentler than power models |
| `PowerProbQueueFunc3` ($n$) | $1 - (front/(front+back))^n$ | Variant formulation |

**Properties:**
- $\text{prob}(0, \text{back}) = 1$: If we are at the front, all decreases happen behind us (no queue advancement)
- $\text{prob}(\text{front}, 0) = 0$: If we are at the back, all decreases happen ahead of us (maximum queue advancement)

#### Queue Advancement Algorithm

```python
def update_queue_position(
    order: 'QueuePos',
    prev_qty: float,
    new_qty: float,
    prob_model: callable,
) -> float:
    """
    HftBacktest's queue position update algorithm.

    Returns the filled quantity (> 0 if order was filled).
    """
    # Step 1: Subtract cumulative trade quantity to avoid double-counting
    chg = prev_qty - new_qty
    chg -= order.cum_trade_qty
    order.cum_trade_qty = 0  # Reset for next cycle

    # Step 2: If depth INCREASED, just clamp front_q_qty
    if chg < 0:
        order.front_q_qty = min(order.front_q_qty, new_qty)
        return 0

    # Step 3: Probabilistic split of depth decrease
    front = order.front_q_qty
    back = prev_qty - front
    prob = prob_model(front, back)

    # prob = probability that decrease came from BEHIND our order
    # (1 - prob) = probability that decrease came from AHEAD
    est_front = front - (1 - prob) * chg

    # Correction: if back goes negative, extra comes from front
    back_remaining = back - prob * chg
    if back_remaining < 0:
        est_front += back_remaining  # back_remaining is negative, so this subtracts

    order.front_q_qty = min(est_front, new_qty)

    # Step 4: Fill detection — negative front_q_qty means we were reached
    if order.front_q_qty < 0:
        filled_qty = round(-order.front_q_qty / lot_size) * lot_size
        order.front_q_qty = 0
        return filled_qty

    return 0
```

#### Trade Handling (Separate from Depth Changes)

```python
def on_trade_at_level(order: 'QueuePos', trade_qty: float):
    """
    When a trade occurs at our price level, directly advance queue.
    Trade quantity is tracked separately to avoid double-counting
    with depth changes.
    """
    order.front_q_qty -= trade_qty
    order.cum_trade_qty += trade_qty
```

> [!important] Key Design: Avoiding Double-Counting
> HftBacktest carefully separates trade-driven queue drain from depth-change-driven queue drain. Trade quantities are subtracted from depth changes before the probabilistic model runs. This prevents counting the same liquidity removal twice (once as a trade, once as a depth decrease).

### 2.5 Adapting HftBacktest Model for Snapshot-Only Fallback

With only snapshots (no separate trade feed), we cannot apply the trade-handling path. The entire queue drain must go through the probabilistic model. This makes the choice of probability function critical:

**Recommendation for snapshot-only mode:**

```python
class SnapshotQueueModel:
    """
    Queue position model adapted for snapshot-only data.
    Uses conservative probability function since we cannot
    distinguish trades from cancellations.
    """

    def __init__(self, power: float = 2.0, cancel_discount: float = 0.5):
        self.power = power
        self.cancel_discount = cancel_discount  # Fraction of depth decrease
                                                 # assumed to be cancellations

    def prob(self, front: float, back: float) -> float:
        """PowerProbQueueFunc with n=2 (conservative)."""
        if front + back <= 0:
            return 0.5
        f_back = back ** self.power
        f_front = front ** self.power
        return f_back / (f_back + f_front)

    def update(self, order, prev_qty: float, new_qty: float) -> float:
        chg = prev_qty - new_qty
        if chg <= 0:
            order.front_q_qty = min(order.front_q_qty, new_qty)
            return 0

        # Apply cancel discount: assume only (1 - cancel_discount)
        # of the decrease was from actual trades
        effective_chg = chg * (1 - self.cancel_discount)

        front = order.front_q_qty
        back = max(0, prev_qty - front)
        prob = self.prob(front, back)

        est_front = front - (1 - prob) * effective_chg
        back_remaining = back - prob * effective_chg
        if back_remaining < 0:
            est_front += back_remaining

        order.front_q_qty = min(est_front, new_qty)

        if order.front_q_qty < 0:
            filled_qty = abs(order.front_q_qty)
            order.front_q_qty = 0
            return filled_qty
        return 0
```

The `cancel_discount` parameter (default 0.5) is the key tuning knob. Setting it too low (treating most decreases as trades) generates phantom fills. Setting it too high (treating most as cancellations) understates fill rates. **Calibrate from trade data if available.**

### 2.6 Queue Position on Order Placement

When our simulated order is placed at a price level, we need an initial queue position:

| Strategy | Queue Ahead | When to Use |
|----------|-------------|-------------|
| Back of queue | Total depth at level | **Default — most conservative** |
| Middle of queue | 50% of depth | Aggressive estimate |
| Proportional | Depth × $U(0.5, 1.0)$ | Probabilistic with conservative bias |

For Polymarket with 200-800ms order latency, back-of-queue is the appropriate default. By the time our order becomes visible, we are genuinely behind all existing resting orders.

---

## 3. Adverse Selection Modeling

### 3.1 Empirical Evidence: The Scale of the Problem

Recent empirical research establishes that adverse selection is not a minor correction — it is the **dominant** factor in limit order fill economics.

#### Lalor & Swishchuk (2024): "Market Simulation under Adverse Selection"

Adverse fill rates across liquid CME futures:

| Contract | Total Fills | Adverse Fills | Adverse Rate |
|----------|-------------|---------------|--------------|
| ES (E-mini S&P) | 941 | 767 | **81.5%** |
| NQ (E-mini Nasdaq) | 1,929 | 1,269 | **65.8%** |
| CL (Crude Oil) | 625 | 518 | **82.9%** |
| ZN (10Y Treasury) | 224 | 199 | **88.8%** |

Key finding: *"Randomly generated executions are unlikely to capture adverse fills, systematically underestimating their frequency. This issue worsens as the time step decreases."*

#### Taranto et al. (2024): "The Negative Drift of a Limit Order Fill"

Using 10Y Treasury futures data:
- Fill probability during adverse price moves: $P(f|D) = 0.99$ (approximately certain)
- Fill probability during non-adverse moves: $R_f = 0.018$ (very low)
- Measured negative drift: **-0.45 ticks** per fill
- Only 66.7% of posted orders were filled; the rest cancelled before adverse execution

**The conditional fill expectation:**

$$\mathbb{E}[d_t | \text{fill}] = \frac{R_f \cdot P(U) - P(D)}{P(f)}$$

This is always negative — on average, the mid-price moves against you after every fill.

### 3.2 Two Categories of Fills

The Lalor & Swishchuk framework distinguishes fills into two categories with different modeling requirements:

#### Adverse Fills (Deterministic)

An adverse fill occurs when the price moves through our resting order. These are **guaranteed** by LOB mechanics — if price crosses our level, our order must fill (assuming queue position is reached).

$$\text{AFA}_t = \sum \delta_{t_i}^+ \cdot \mathbb{1}\{AS(t_i) < AS(t_{i+1})\}$$

where $AS(t)$ is the ask price at time $t$. If the ask moved up through our bid, we were adversely filled.

In snapshot data, this corresponds to: **between snapshots $t_i$ and $t_{i+1}$, the best ask rose above our bid price** (for buy orders) or **the best bid fell below our ask price** (for sell orders).

#### Non-Adverse Fills (Probabilistic)

A non-adverse fill occurs when a trade consumes our depth without the price moving against us — we captured the spread successfully. These are probabilistic:

$$\text{NFA}_t = \sum \delta_{t_i}^+ \cdot \mathbb{1}\{M_{t_i}^+ = 1\} \cdot \rho$$

where $\rho \in (0, 1)$ is the non-adverse fill probability. This is the parameter to calibrate.

### 3.3 Detecting Adverse Selection from Snapshots

```python
class SnapshotAdverseSelectionDetector:
    """
    Classify fill opportunities as adverse or non-adverse
    using only orderbook snapshot data.
    """

    def __init__(self, lookback_snapshots: int = 5):
        self.recent_snapshots = []
        self.lookback = lookback_snapshots

    def classify_fill(
        self,
        order_side: str,
        order_price: float,
        fill_snapshot_idx: int,
        snapshots: list,
    ) -> dict:
        """
        Classify a fill as adverse or non-adverse based on
        subsequent price movement.

        For CALIBRATION ONLY (uses forward-looking data).
        At backtest time, use the calibrated fill rates.
        """
        fill_mid = get_midpoint(snapshots[fill_snapshot_idx])

        # Look at midpoint 1, 5, 10, 30 snapshots ahead
        horizons = [1, 5, 10, 30]
        results = {'adverse': False, 'adverse_severity': 0.0}

        for h in horizons:
            if fill_snapshot_idx + h >= len(snapshots):
                break
            future_mid = get_midpoint(snapshots[fill_snapshot_idx + h])

            if order_side == 'buy':
                drift = future_mid - fill_mid  # Negative = adverse for buyer
                if drift < -0.005:  # Half a cent threshold
                    results['adverse'] = True
                    results['adverse_severity'] = max(
                        results['adverse_severity'], abs(drift)
                    )
            else:  # sell
                drift = fill_mid - future_mid  # Negative = adverse for seller
                if drift < -0.005:
                    results['adverse'] = True
                    results['adverse_severity'] = max(
                        results['adverse_severity'], abs(drift)
                    )

        return results

    def compute_depth_imbalance(self, snapshot: dict, levels: int = 5) -> float:
        """
        Depth imbalance is a real-time (non-forward-looking) predictor
        of adverse selection.

        OIR = (bid_depth - ask_depth) / (bid_depth + ask_depth)

        Positive OIR → buying pressure → adverse for ask-side fills
        Negative OIR → selling pressure → adverse for bid-side fills
        """
        bid_depth = sum(
            float(snapshot[f'bid_size_{i}'])
            for i in range(levels)
            if snapshot.get(f'bid_size_{i}')
        )
        ask_depth = sum(
            float(snapshot[f'ask_size_{i}'])
            for i in range(levels)
            if snapshot.get(f'ask_size_{i}')
        )

        total = bid_depth + ask_depth
        if total == 0:
            return 0.0
        return (bid_depth - ask_depth) / total

    def compute_depth_change_imbalance(
        self, prev_snapshot: dict, curr_snapshot: dict, levels: int = 5
    ) -> float:
        """
        Rate of change in depth imbalance — a stronger signal.
        Rapid thinning on one side predicts adverse fills on the other.
        """
        prev_oir = self.compute_depth_imbalance(prev_snapshot, levels)
        curr_oir = self.compute_depth_imbalance(curr_snapshot, levels)
        return curr_oir - prev_oir
```

### 3.4 VPIN Adaptation for Prediction Markets

VPIN (Volume-Synchronized Probability of Informed Trading) measures order flow toxicity by comparing buy-initiated vs sell-initiated volume across fixed-volume buckets. For snapshot data without explicit trade direction:

```python
class SnapshotVPIN:
    """
    VPIN adapted for orderbook snapshot data.

    Without explicit trade data, we use BBO changes to classify
    order flow direction:
    - Ask depth decreasing → buy-initiated flow
    - Bid depth decreasing → sell-initiated flow

    Original: Easley, Lopez de Prado & O'Hara (2010)
    """

    def __init__(self, bucket_size: float = 100.0, n_buckets: int = 50):
        self.bucket_size = bucket_size
        self.n_buckets = n_buckets
        self.buckets = []
        self.current_bucket_buy = 0.0
        self.current_bucket_sell = 0.0
        self.current_bucket_volume = 0.0

    def on_snapshot_pair(
        self, prev_snapshot: dict, curr_snapshot: dict
    ):
        """
        Infer trade direction and volume from snapshot pair.
        """
        prev_ask_depth = float(prev_snapshot['ask_size_0'])
        curr_ask_depth = float(curr_snapshot['ask_size_0'])
        prev_bid_depth = float(prev_snapshot['bid_size_0'])
        curr_bid_depth = float(curr_snapshot['bid_size_0'])

        # Estimate buy volume (ask side consumed)
        buy_volume = max(0, prev_ask_depth - curr_ask_depth)
        # Estimate sell volume (bid side consumed)
        sell_volume = max(0, prev_bid_depth - curr_bid_depth)

        total = buy_volume + sell_volume
        if total == 0:
            return

        self.current_bucket_buy += buy_volume
        self.current_bucket_sell += sell_volume
        self.current_bucket_volume += total

        # Close bucket when volume threshold reached
        while self.current_bucket_volume >= self.bucket_size:
            self.buckets.append({
                'buy': self.current_bucket_buy,
                'sell': self.current_bucket_sell,
            })
            self.current_bucket_buy = 0
            self.current_bucket_sell = 0
            self.current_bucket_volume -= self.bucket_size

            if len(self.buckets) > self.n_buckets:
                self.buckets.pop(0)

    def get_vpin(self) -> float:
        """
        VPIN = (1/n) * Σ |V_buy - V_sell| / (V_buy + V_sell)

        Range: [0, 1]. Higher = more informed flow = higher toxicity.
        Above 0.7 → very toxic, expect adverse selection.
        """
        if len(self.buckets) < self.n_buckets:
            return 0.5  # Insufficient data

        vpin_sum = 0.0
        for bucket in self.buckets[-self.n_buckets:]:
            total = bucket['buy'] + bucket['sell']
            if total > 0:
                vpin_sum += abs(bucket['buy'] - bucket['sell']) / total

        return vpin_sum / self.n_buckets
```

### 3.5 Onchain Wallet Analysis for Informed Flow

Telonex's `onchain_fills` channel provides maker/taker wallet addresses. This enables a unique form of adverse selection analysis:

```python
def analyze_wallet_toxicity(
    onchain_fills: pd.DataFrame,
    price_data: pd.DataFrame,
    forward_horizon_minutes: int = 5,
) -> pd.DataFrame:
    """
    Classify wallets by their historical fill profitability.

    Wallets whose fills consistently predict price direction
    are 'informed' — fills against them are highly adverse.

    Parameters:
        onchain_fills: Telonex onchain_fills with maker/taker addresses
        price_data: Midpoint prices for PnL calculation
    """
    taker_stats = []

    for wallet in onchain_fills['taker_address'].unique():
        wallet_fills = onchain_fills[
            onchain_fills['taker_address'] == wallet
        ]

        profits = []
        for _, fill in wallet_fills.iterrows():
            future_mid = get_midpoint_at_time(
                price_data,
                fill['timestamp'] + pd.Timedelta(minutes=forward_horizon_minutes)
            )
            if future_mid is None:
                continue

            if fill['side'] == 'buy':
                profit = future_mid - fill['price']
            else:
                profit = fill['price'] - future_mid
            profits.append(profit)

        if len(profits) >= 10:  # Minimum sample
            taker_stats.append({
                'wallet': wallet,
                'n_fills': len(profits),
                'avg_profit': np.mean(profits),
                'win_rate': sum(1 for p in profits if p > 0) / len(profits),
                'is_informed': np.mean(profits) > 0.01,  # Consistently profitable
            })

    return pd.DataFrame(taker_stats)
```

> [!note] Data Requirement
> This analysis requires downloading the `onchain_fills` channel from Telonex, which is available from November 2022. For initial engine development, this is a **nice-to-have** enhancement, not a prerequisite.

---

## 4. Latency Modeling with Discrete Timestamps

### 4.1 The Discrete-Time Latency Problem

The BTC engine models latency precisely because events have microsecond-resolution timestamps:

```
decision_ts → +decision_to_send_ms → send_ts → +exchange_network_ms → visible_ts
```

With snapshots at ~0.1-3 second intervals, the time grid is coarser than the latency itself (Polymarket order latency: 200-800ms). This creates ambiguity about when orders become active.

### 4.2 Three Approaches to Snapshot-Granularity Latency

#### Approach A: Snap to Next Snapshot (Recommended)

The simplest and most conservative approach. When the strategy makes a decision at snapshot $t_k$, the order becomes visible at the **first snapshot after** $t_k + \text{latency}$.

```python
def compute_visible_snapshot(
    decision_snapshot_idx: int,
    snapshot_timestamps: np.ndarray,
    latency_ms: float = 500.0,
) -> int:
    """
    Find the first snapshot at which the order becomes visible.

    This naturally rounds UP — the order is never visible sooner
    than the latency allows.
    """
    decision_ts = snapshot_timestamps[decision_snapshot_idx]
    visible_ts = decision_ts + latency_ms * 1000  # Convert to microseconds

    # Binary search for first snapshot >= visible_ts
    visible_idx = np.searchsorted(
        snapshot_timestamps[decision_snapshot_idx:],
        visible_ts,
        side='left'
    ) + decision_snapshot_idx

    return min(visible_idx, len(snapshot_timestamps) - 1)
```

**Properties:**
- Always conservative (order visible later, not sooner)
- Typical delay: 1-3 snapshots beyond the decision point
- No interpolation needed
- Deterministic

#### Approach B: Probabilistic Visibility

Model the order as becoming active with increasing probability across the snapshots spanning the latency window.

```python
def probabilistic_visibility(
    decision_snapshot_idx: int,
    snapshot_timestamps: np.ndarray,
    latency_mean_ms: float = 500.0,
    latency_std_ms: float = 100.0,
) -> list:
    """
    Return list of (snapshot_idx, probability_active) pairs.

    Uses a normal CDF centered at decision_ts + latency_mean.
    """
    from scipy.stats import norm

    decision_ts = snapshot_timestamps[decision_snapshot_idx]
    target_ts = decision_ts + latency_mean_ms * 1000
    std_us = latency_std_ms * 1000

    results = []
    for idx in range(decision_snapshot_idx + 1, len(snapshot_timestamps)):
        ts = snapshot_timestamps[idx]
        prob = norm.cdf(ts, loc=target_ts, scale=std_us)
        results.append((idx, prob))
        if prob > 0.99:
            break

    return results
```

**Properties:**
- More realistic distribution of order activation times
- Adds complexity and stochasticity
- Better suited for Monte Carlo analysis

#### Approach C: Interpolated State (Not Recommended)

Interpolate the orderbook state at the exact `visible_ts` between two snapshots. This is problematic because orderbook states are discontinuous — interpolating between snapshots produces synthetic states that never existed.

### 4.3 Cancel Race Conditions

Cancel race conditions are critical for market making: an order in `PENDING_CANCEL` can still fill during the cancellation latency window (as modeled in the BTC engine, Section 11).

With discrete snapshots:

```python
def handle_cancel_with_snapshots(
    order: dict,
    cancel_request_snapshot_idx: int,
    snapshot_timestamps: np.ndarray,
    cancel_latency_ms: float = 500.0,
) -> dict:
    """
    Model cancel race condition in discrete time.

    The order remains fillable between the cancel request snapshot
    and the cancel-effective snapshot.
    """
    cancel_effective_idx = compute_visible_snapshot(
        cancel_request_snapshot_idx,
        snapshot_timestamps,
        cancel_latency_ms,
    )

    return {
        'order_id': order['order_id'],
        'status': 'PENDING_CANCEL',
        'cancel_request_snapshot': cancel_request_snapshot_idx,
        'cancel_effective_snapshot': cancel_effective_idx,
        'fillable_until_snapshot': cancel_effective_idx,
        # The order CAN fill at any snapshot between request and effective
    }
```

### 4.4 Recommended Latency Configuration for Polymarket

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `decision_to_send_ms` | 0-50ms | Our infrastructure latency |
| `exchange_network_ms` | 200-800ms | Polygon L2 CLOB latency |
| `cancel_latency_ms` | 200-800ms | Independent of submit latency |
| Total visible delay | 1-3 snapshots | At ~0.5s avg snapshot interval |
| Latency model | Snap-to-next-snapshot | Conservative, deterministic |

---

## 5. Fill Probability Models

### 5.1 Cont-Stoikov-Talreja Model

The foundational model for fill probabilities in limit order books. Models the LOB as a continuous-time Markov chain where each price level is a birth-death process.

**Key parameters:**

| Parameter | Meaning | How to Estimate |
|-----------|---------|-----------------|
| $\lambda$ | Limit order arrival rate | Count new depth additions per unit time |
| $\mu$ | Cancellation rate | Count depth removals (non-trade) per unit time |
| $\theta$ | Market order arrival rate | Count BBO crossings per unit time |

**Fill probability before mid-price moves:**

For a bid order at the best bid with $q$ units ahead in queue and total depth $Q$ at the level:

$$P(\text{fill before mid moves}) = \frac{\theta^q}{\prod_{k=1}^{q}(\lambda + \mu + \theta \cdot \mathbb{1}_{k \leq Q})}$$

This can be computed semi-analytically using Laplace transforms, or approximated as:

$$P(\text{fill}) \approx \left(\frac{\theta}{\lambda + \mu + \theta}\right)^q$$

**Adaptation for snapshot data:** Estimate $\lambda$, $\mu$, $\theta$ from snapshot-to-snapshot changes:
- $\hat{\theta}$: Rate of BBO crossings per snapshot interval
- $\hat{\lambda}$: Rate of depth increases at BBO per snapshot interval
- $\hat{\mu}$: Rate of depth decreases at BBO that don't coincide with BBO movement

### 5.2 Avellaneda-Stoikov Fill Intensity

The Avellaneda-Stoikov (2008) optimal market making model uses an exponential fill intensity:

$$\lambda(\delta) = A \cdot e^{-\kappa \delta}$$

where:
- $\delta$ is the distance from midpoint to our quote (half-spread)
- $A$ is the baseline arrival rate
- $\kappa$ is the order book density parameter

**Interpretation of $\kappa$:**
- High $\kappa$ → dense books, fills very sensitive to spread
- Low $\kappa$ → thin books, fills less sensitive (more tolerant of wider spreads)

**Estimation from snapshot data:**

```python
def estimate_kappa(snapshots: list, window_minutes: int = 60) -> float:
    """
    Estimate the Avellaneda-Stoikov κ parameter from snapshot data.

    Method: For each snapshot, compute the total depth at each
    price level distance from mid. Fit an exponential decay.
    """
    import numpy as np
    from scipy.optimize import curve_fit

    distances = []  # δ values
    depths = []     # depth at that δ

    for snap in snapshots:
        mid = (float(snap['bid_price_0']) + float(snap['ask_price_0'])) / 2

        for i in range(25):  # 25 levels available
            if snap.get(f'ask_price_{i}') and snap.get(f'ask_size_{i}'):
                ask_p = float(snap[f'ask_price_{i}'])
                ask_s = float(snap[f'ask_size_{i}'])
                distances.append(ask_p - mid)
                depths.append(ask_s)

            if snap.get(f'bid_price_{i}') and snap.get(f'bid_size_{i}'):
                bid_p = float(snap[f'bid_price_{i}'])
                bid_s = float(snap[f'bid_size_{i}'])
                distances.append(mid - bid_p)
                depths.append(bid_s)

    distances = np.array(distances)
    depths = np.array(depths)

    # Fit exponential: depth(δ) = A * exp(-κ * δ)
    def exp_decay(delta, A, kappa):
        return A * np.exp(-kappa * delta)

    try:
        popt, _ = curve_fit(exp_decay, distances, depths,
                             p0=[100, 5], maxfev=5000)
        return popt[1]  # kappa
    except RuntimeError:
        return 5.0  # Default fallback
```

### 5.3 Probabilistic Fill Model (Recommended for Our Engine)

Instead of binary fill decisions, model fill probability conditioned on observable state:

$$P(\text{fill} | \mathbf{x}) = \sigma(\beta_0 + \beta_1 x_1 + \beta_2 x_2 + \beta_3 x_3 + \beta_4 x_4 + \beta_5 x_5)$$

where $\sigma$ is the sigmoid function and features $\mathbf{x}$:

| Feature | Symbol | Observable from Snapshots? |
|---------|--------|---------------------------|
| Queue position (fraction of total depth) | $x_1 = q/Q$ | Yes (at placement time) |
| Time at level (snapshots since placement) | $x_2$ | Yes |
| Spread (current) | $x_3 = \text{ask} - \text{bid}$ | Yes |
| Depth imbalance (OIR) | $x_4 = (D_b - D_a)/(D_b + D_a)$ | Yes |
| Depth change rate at our level | $x_5 = \Delta D / \Delta t$ | Yes |

**Calibration procedure:**

```python
def calibrate_fill_model(
    snapshots: list,
    actual_trades: pd.DataFrame,  # From Telonex trades channel
    spread_width: float = 0.02,
) -> dict:
    """
    Calibrate fill probability model from historical data.

    For each snapshot:
    1. Place hypothetical quotes at mid +/- spread/2
    2. Compute features (x1..x5)
    3. Check if a trade in the trades data would have filled us
    4. Fit logistic regression

    Returns calibrated coefficients.
    """
    from sklearn.linear_model import LogisticRegression

    features = []
    labels = []

    for i in range(len(snapshots) - 1):
        snap = snapshots[i]
        mid = get_midpoint(snap)
        bid_quote = mid - spread_width / 2
        ask_quote = mid + spread_width / 2

        # Features
        bid_depth = float(snap['bid_size_0'])
        ask_depth = float(snap['ask_size_0'])
        spread = float(snap['ask_price_0']) - float(snap['bid_price_0'])
        oir = (bid_depth - ask_depth) / max(bid_depth + ask_depth, 1)

        # Check for fills in the trade data between snapshots
        ts_start = snap['timestamp_us']
        ts_end = snapshots[i + 1]['timestamp_us']
        interval_trades = actual_trades[
            (actual_trades['timestamp_us'] >= ts_start) &
            (actual_trades['timestamp_us'] < ts_end)
        ]

        bid_filled = any(
            t['price'] <= bid_quote and t['side'] == 'sell'
            for _, t in interval_trades.iterrows()
        )
        ask_filled = any(
            t['price'] >= ask_quote and t['side'] == 'buy'
            for _, t in interval_trades.iterrows()
        )

        # Record bid-side observation
        features.append([1.0, 1, spread, oir, 0])  # Back of queue
        labels.append(1 if bid_filled else 0)

        # Record ask-side observation
        features.append([1.0, 1, spread, -oir, 0])
        labels.append(1 if ask_filled else 0)

    X = np.array(features)
    y = np.array(labels)

    model = LogisticRegression(max_iter=1000)
    model.fit(X, y)

    return {
        'coefficients': model.coef_[0].tolist(),
        'intercept': model.intercept_[0],
        'fill_rate': y.mean(),
    }
```

### 5.4 Deep Learning Fill Estimation

Kolm, Ritter & Westray (2022) propose using recurrent neural networks to estimate time-to-fill distributions conditioned on LOB state. While powerful, this requires substantial training data and introduces model risk. For our current scope, the logistic regression approach is more appropriate — we can upgrade to deep learning once we have sufficient calibration data from multiple markets.

---

## 6. Multi-Channel Data Fusion

### 6.1 Available Telonex Channels

| Channel | Content | Granularity | Status |
|---------|---------|-------------|--------|
| `book_snapshot_full` | **All-level** orderbook (per token: YES and NO) | Every change (~0.1-3s) | **Confirmed** -- will download |
| `trades` | Every executed trade with price, size, taker side (per token) | Per-trade | **Confirmed** -- will download |
| `book_snapshot_25` | 25-level orderbook | Every change | Downloaded (5 NVDA markets, POC) |
| `quotes` | BBO changes | Per-change | Available (not yet planned) |
| `onchain_fills` | Blockchain settlements with wallet IDs | Per-fill | Available (not yet planned) |

> [!note] Per-Token Data
> Both `book_snapshot_full` and `trades` are downloaded separately for each token (YES and NO). A single binary market requires 4 files: YES book snapshots, NO book snapshots, YES trades, NO trades.

### 6.2 Data Combination Strategies

#### Strategy 1: Snapshots Only (Fallback / Baseline)

```
book_snapshot_full → Infer fills from depth changes
                   → Probabilistic queue model (HftBacktest-style)
                   → Conservative fill rates
```

**Pros:** Simpler data pipeline. Works if trades data is unavailable.
**Cons:** Cannot distinguish trades from cancellations. Lower fill accuracy. Requires cancel_discount tuning.

#### Strategy 2: Snapshots + Trades (Primary Recommendation)

```
book_snapshot_full → Book state at each point (per YES/NO token)
(YES + NO)         → Real depth for queue position initialization
                   → Depth profiles for adverse selection signals

trades             → Actual fill triggers (per YES/NO token)
(YES + NO)         → Trade-tick matching (BTC engine's 7-condition model)
                   → Deterministic queue drain through actual trades
```

**Implementation with dual orderbook support:**

```python
class DualBookHybridFillSimulator:
    """
    Production fill simulator using book_snapshot_full + trades.

    Maintains independent fill simulation for YES and NO token books.
    Mirrors the BTC engine's dual-TokenBook architecture.
    """

    def __init__(self, queue_model: str = 'CONSERVATIVE',
                 latency_ms: float = 500.0):
        self.queue = TradeAndSnapshotQueueModel(position_model=queue_model)
        self.latency_ms = latency_ms

        # Current book state per token
        self.yes_book = None
        self.no_book = None

        # Parity tracking
        self.parity_violations = []

    def on_snapshot(self, snapshot: dict, token_side: str, timestamp: int):
        """Update book state from book_snapshot_full for one token."""
        if token_side == 'YES':
            self.yes_book = snapshot
        else:
            self.no_book = snapshot

        # Cross-leg parity check when both books available
        if self.yes_book and self.no_book:
            self._check_parity(timestamp)

    def on_trade(self, trade: dict, token_side: str,
                 timestamp: int) -> list:
        """
        Process trade event from Telonex trades channel.

        Trades on YES token only affect YES-side orders.
        Trades on NO token only affect NO-side orders.
        """
        return self.queue.on_trade(trade, token_side)

    def place_order(self, order_id: str, token_side: str,
                    book_side: str, price: float, size: float,
                    decision_ts: int):
        """
        Place an order on one of the two token books.

        Order becomes active at decision_ts + latency.
        Queue position assigned from real book depth at activation time.
        """
        visible_ts = decision_ts + int(self.latency_ms * 1000)
        book = self.yes_book if token_side == 'YES' else self.no_book

        if book is None:
            return  # No book state yet

        self.queue.on_order_active(
            order_id=order_id,
            token_side=token_side,
            book_side=book_side,
            price=price,
            size=size,
            current_book=book,
        )
        # Set visible_ts on the queue state
        orders = (self.queue.yes_orders if token_side == 'YES'
                  else self.queue.no_orders)
        if order_id in orders:
            orders[order_id].visible_ts = visible_ts

    def _check_parity(self, timestamp: int):
        """
        Cross-leg parity: YES_ask + NO_ask >= 1.00
                          YES_bid + NO_bid <= 1.00

        Violations may indicate data issues or arbitrage.
        """
        yes_ask = self._get_best_ask(self.yes_book)
        no_ask = self._get_best_ask(self.no_book)
        yes_bid = self._get_best_bid(self.yes_book)
        no_bid = self._get_best_bid(self.no_book)

        if yes_ask is not None and no_ask is not None:
            ask_sum = yes_ask + no_ask
            if ask_sum < 0.995:  # Tolerance for rounding
                self.parity_violations.append({
                    'timestamp': timestamp,
                    'type': 'ask_sum_below_1',
                    'yes_ask': yes_ask,
                    'no_ask': no_ask,
                    'sum': ask_sum,
                })

        if yes_bid is not None and no_bid is not None:
            bid_sum = yes_bid + no_bid
            if bid_sum > 1.005:
                self.parity_violations.append({
                    'timestamp': timestamp,
                    'type': 'bid_sum_above_1',
                    'yes_bid': yes_bid,
                    'no_bid': no_bid,
                    'sum': bid_sum,
                })

    @staticmethod
    def _get_best_ask(book: dict) -> float:
        asks = book.get('asks', [])
        return float(asks[0]['price']) if asks else None

    @staticmethod
    def _get_best_bid(book: dict) -> float:
        bids = book.get('bids', [])
        return float(bids[0]['price']) if bids else None
```

**Time alignment and event interleaving:**

Telonex timestamps use microsecond precision across all channels. The engine interleaves all four data streams (YES snapshots, NO snapshots, YES trades, NO trades) into a single timeline:

```python
def merge_dual_book_channels(
    yes_snapshots: pd.DataFrame,
    no_snapshots: pd.DataFrame,
    yes_trades: pd.DataFrame,
    no_trades: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge all four data streams into a single chronological timeline.

    Each event tagged with:
    - event_type: 'snapshot' or 'trade'
    - token_side: 'YES' or 'NO'
    """
    dfs = []

    for df, etype, tside in [
        (yes_snapshots, 'snapshot', 'YES'),
        (no_snapshots, 'snapshot', 'NO'),
        (yes_trades, 'trade', 'YES'),
        (no_trades, 'trade', 'NO'),
    ]:
        d = df.copy()
        d['event_type'] = etype
        d['token_side'] = tside
        dfs.append(d)

    merged = pd.concat(dfs).sort_values('timestamp_us').reset_index(drop=True)
    return merged
```

**Processing loop (mirrors BTC engine's 5-phase timestamp processing):**

```python
def run_backtest(timeline: pd.DataFrame, simulator, strategy):
    """
    Main event loop processing merged timeline.

    Phase 1: Process all external events (snapshots, trades)
    Phase 2: Check for pending order activations / cancellations
    Phase 3: Deliver fills and book updates to strategy
    Phase 4: Process strategy actions (new orders, cancels)
    """
    for _, event in timeline.iterrows():
        ts = event['timestamp_us']
        token = event['token_side']

        if event['event_type'] == 'snapshot':
            simulator.on_snapshot(event, token, ts)
            strategy.on_book_update(event, token, ts)

        elif event['event_type'] == 'trade':
            fills = simulator.on_trade(event, token, ts)

            for fill in fills:
                strategy.on_fill(fill, ts)

            strategy.on_trade(event, token, ts)  # Inform strategy of market trades

        # Process strategy actions
        actions = strategy.get_pending_actions(ts)
        for action in actions:
            if action['type'] == 'PLACE_ORDER':
                simulator.place_order(
                    order_id=action['order_id'],
                    token_side=action['token_side'],
                    book_side=action['side'],
                    price=action['price'],
                    size=action['size'],
                    decision_ts=ts,
                )
            elif action['type'] == 'CANCEL_ORDER':
                simulator.cancel_order(action['order_id'], ts)
```

#### Strategy 3: Full Stack (Ultimate Realism)

```
book_snapshot_full → Book state, depth profiles (per YES/NO)
trades             → Fill triggers, queue drain (per YES/NO)
quotes             → High-resolution BBO tracking
onchain_fills      → Wallet-level informed flow analysis
```

This approaches the fidelity of the BTC engine's WebSocket-based data while adding the unique on-chain dimension for informed flow detection.

### 6.3 Calibration: Validating Fill Simulation Against Trade Data

With both channels available, validate the fill simulator's output against the known trade stream:

```python
def validate_snapshot_fills(
    snapshot_fills: list,   # Fills from snapshot-only simulator
    trade_fills: list,      # Fills from trade-data simulator (ground truth)
) -> dict:
    """
    Compare snapshot-inferred fills against trade-data fills.
    Measures accuracy of the snapshot-only approach.
    """
    # Match fills by approximate timestamp (within 5 seconds)
    matched = 0
    false_positives = 0  # Snapshot said fill, trade data says no
    false_negatives = 0  # Trade data says fill, snapshot missed it

    trade_times = {f['timestamp'] for f in trade_fills}

    for sf in snapshot_fills:
        # Find nearest trade fill within 5 seconds
        nearest = min(
            trade_times,
            key=lambda t: abs(t - sf['timestamp']),
            default=None,
        )
        if nearest and abs(nearest - sf['timestamp']) < 5_000_000:
            matched += 1
            trade_times.discard(nearest)
        else:
            false_positives += 1

    false_negatives = len(trade_times)

    precision = matched / max(matched + false_positives, 1)
    recall = matched / max(matched + false_negatives, 1)

    return {
        'matched': matched,
        'false_positives': false_positives,
        'false_negatives': false_negatives,
        'precision': precision,
        'recall': recall,
        'f1': 2 * precision * recall / max(precision + recall, 1e-10),
    }
```

---

## 7. Comparison of Fill Simulation Approaches

### 7.1 Approach Comparison Matrix

| Approach | Data Required | Fill Accuracy | Adverse Selection | Queue Modeling | Dual Book Support | Complexity | Our POC Result |
|----------|--------------|---------------|-------------------|----------------|-------------------|------------|----------------|
| **1. Snapshot-crossing** | `book_snapshot_*` | Low | Binary (crosses or not) | None | Trivial | Very Low | -$18 P&L, 188 fills |
| **2. Depth-drain** | `book_snapshot_full` | Medium | Via depth imbalance | Probabilistic (HftBacktest-style) | Per-token queues | Medium | Not tested |
| **3. Trade-event-driven** | `trades` only | High | Via trade direction + price movement | Trade-driven (BTC engine-style) | Per-token trades | Medium | N/A |
| **4. Probabilistic** | `book_snapshot_full` (calibrated) | Medium-High | Modeled via fill probability features | Implicit in probability model | Per-token models | Medium | Not tested |
| **5. Hybrid** | `book_snapshot_full` + `trades` | **Highest** | Full (depth + trade + price + OIR) | Explicit trade-driven + real depth | **Native** (Section 2.3) | Medium-High | **Recommended** |

### 7.2 Detailed Comparison

#### Approach 1: Snapshot-Crossing (Current POC L2 Simulator)

Our current implementation. Fill if order price crosses the BBO.

**Algorithm:**
```
IF order.side == 'buy' AND order.price >= best_ask: FILL
IF order.side == 'sell' AND order.price <= best_bid: FILL
```

| Strength | Weakness |
|----------|----------|
| Simple, fast, deterministic | No queue position modeling |
| Conservative (few false fills) | Misses fills where depth was consumed at our level |
| No phantom fill problem | Does not model partial fills |
| Works with snapshots only | Binary outcome (all or nothing) |

**Verdict:** Good baseline. Too conservative for accurate fill rate estimation but safe from overstatement.

#### Approach 2: Depth-Drain (Snapshot-Inferred Queue Drain)

Use depth changes between snapshots to model queue advancement. Apply HftBacktest's probabilistic queue model.

**Algorithm:**
```
For each snapshot pair:
  1. Compute depth change at order's price level
  2. Apply cancel_discount (0.5) to depth change
  3. Use probability model to split change into ahead/behind
  4. Advance queue position
  5. Fill if queue position reaches zero
```

| Strength | Weakness |
|----------|----------|
| Models queue position realistically | Cannot distinguish trades from cancellations |
| Uses actual depth data | Requires `cancel_discount` tuning parameter |
| Compatible with snapshot-only data | More complex than snapshot-crossing |
| Proven framework (HftBacktest) | Risk of phantom fills if poorly tuned |

**Verdict:** Best snapshot-only approach. Requires careful calibration of the `cancel_discount` parameter.

#### Approach 3: Trade-Event-Driven (BTC Engine Approach)

Use actual trade events as fill triggers. Queue drain only through trades.

**Algorithm:**
```
For each trade event:
  1. Check if trade price matches our order
  2. Check trade direction compatibility
  3. Drain queue by trade size
  4. Fill if queue_ahead == 0 and sufficient remaining size
```

| Strength | Weakness |
|----------|----------|
| Highest accuracy (real trade events) | Requires `trades` channel download |
| No phantom fill problem | More data to process and store |
| Proven in BTC engine | Doesn't capture fills between trade records |
| Deterministic queue drain | Additional data cost |

**Verdict:** The gold standard. Requires downloading the `trades` channel from Telonex.

#### Approach 4: Probabilistic (Feature-Based Fill Probability)

Model $P(\text{fill})$ as a function of observable features. Use logistic regression or similar.

**Algorithm:**
```
For each snapshot:
  1. Compute features: queue_position, time_at_level, spread, OIR, depth_change
  2. P(fill) = sigmoid(β · x)
  3. Sample: fill if U(0,1) < P(fill)
```

| Strength | Weakness |
|----------|----------|
| Captures rich market state | Requires calibration data (ideally trades) |
| Adapts to regime changes | Stochastic — different results each run |
| Models partial fills naturally | Risk of overfitting to training data |
| Extensible feature set | More complex to implement and validate |

**Verdict:** Excellent for production use after calibration. Requires `trades` data for initial calibration.

#### Approach 5: Hybrid (Snapshots for State + Trades for Triggers)

Combine snapshot book state with trade events for fill triggers. Native dual YES/NO orderbook support.

**Algorithm:**
```
Merge 4 streams: YES snapshots, NO snapshots, YES trades, NO trades
Interleave by timestamp:
  On snapshot: Update book state for correct token (YES or NO)
  On trade: Run 7-condition fill matching against orders on correct token book
            Queue depth from latest snapshot for that token
  Cross-leg: Validate YES_ask + NO_ask >= 1.00 at each snapshot pair
```

| Strength | Weakness |
|----------|----------|
| Best of both worlds | Requires both data channels |
| Real fills + real depth | 4 data streams per market (YES/NO x snapshot/trade) |
| Native dual orderbook support | More complex implementation |
| BTC engine's 7-condition fill model | Time alignment across 4 streams |
| Deterministic -- every fill traces to a source trade | Higher data processing cost |
| No phantom fills, no cancel_discount tuning | |
| Cross-leg parity validation built-in | |

**Verdict:** The recommended production approach. Both `book_snapshot_full` and `trades` are confirmed for download. Implementation provided in Section 6.2, Strategy 2 (`DualBookHybridFillSimulator`).

### 7.3 Ranking and Recommendation

| Rank | Approach | When to Use | Status |
|------|----------|-------------|--------|
| **1** | **Hybrid (5)** | **Primary engine** -- `book_snapshot_full` + `trades` | **Confirmed data available** |
| **2** | **Trade-event-driven (3)** | Simplified engine without full depth data | Trades confirmed |
| **3** | **Depth-drain (2)** | Fallback when trades data unavailable | Snapshot data available |
| **4** | **Probabilistic (4)** | Enhanced fills after calibration from Approach 5 | Post-calibration |
| **5** | **Snapshot-crossing (1)** | Quick sanity checks and lower-bound estimates | POC baseline |

> [!success] Recommended Architecture
> **Approach 5 (Hybrid) is our primary implementation target.** With both `book_snapshot_full` and `trades` confirmed for download, we can implement the BTC engine's proven 7-condition fill model with real depth data for queue initialization. The `DualBookHybridFillSimulator` (Section 6.2, Strategy 2) provides the complete implementation pattern, including dual YES/NO orderbook management and cross-leg parity checks.

---

## 8. Concrete Recommendations for Our Engine

### 8.1 Phased Implementation Plan

#### Phase 1: Data Download and Preparation

Download `book_snapshot_full` and `trades` for all target markets from Telonex:

1. Download per-token data: YES snapshots, NO snapshots, YES trades, NO trades
2. Validate data quality: cross-leg parity, timestamp consistency, gap detection
3. Build the `merge_dual_book_channels` pipeline (Section 6.2)
4. Compare `book_snapshot_full` vs `book_snapshot_25` (already downloaded for NVDA POC)

**Expected outcome:** Clean, merged timeline for each market. Data quality report extending [[Telonex-Data-Quality-Report]].

#### Phase 2: Hybrid Fill Simulator (Primary Engine)

Implement the `DualBookHybridFillSimulator` (Section 6.2, Strategy 2) with the BTC engine's 7-condition fill model:

1. Implement `TradeAndSnapshotQueueModel` (Section 2.3) with dual YES/NO book support
2. Trade-event-driven fill triggers -- trades as the SOLE source of fills
3. Queue position initialized from `book_snapshot_full` real depth (back-of-queue default)
4. Snap-to-next-snapshot latency model (Section 4.2, Approach A), 500ms default
5. Cancel race condition modeling (Section 4.3) -- orders fillable during PENDING_CANCEL
6. Cross-leg parity validation at every snapshot pair

**Expected outcome:** Fill accuracy matching the BTC engine. Realistic adverse selection rates (expect 65-85%). No phantom fills.

#### Phase 3: Adverse Selection Analysis and Calibration

Measure and calibrate adverse selection from Phase 2 results:

1. Classify all fills as adverse/non-adverse using post-fill price movement (Section 3.2)
2. Compute depth imbalance (OIR) signals and correlate with fill outcomes (Section 3.3)
3. Measure actual adverse fill rate per market -- validate against 65-85% benchmark
4. Implement VPIN from snapshot data (Section 3.4) for toxicity monitoring
5. Calibrate the probabilistic fill model (Section 5.3) using trade-validated fills

**Expected outcome:** Calibrated adverse selection parameters. Per-market fill quality metrics.

#### Phase 4: Advanced Enhancements

1. Feature-based fill probability model (Section 5.3) -- logistic regression trained on Phase 2 fills
2. Walk-forward validation of fill probability parameters
3. Onchain wallet toxicity analysis (Section 3.5) if `onchain_fills` downloaded
4. HftBacktest probabilistic queue model (Section 2.4) as comparison benchmark
5. Deep learning fill estimation (Section 5.4) if sufficient data accumulated

### 8.2 Key Parameters to Track

| Parameter | Initial Value | Calibrate From | Sensitivity |
|-----------|---------------|-----------------|-------------|
| `queue_position_model` | Back of queue (`CONSERVATIVE`) | Empirical fill latency | **Critical** -- front vs back changes fill rate ~2x |
| `latency_ms` | 500 | Polymarket order latency measurements | High -- determines visible_ts |
| `cancel_latency_ms` | 500 | Independent measurement | High -- affects PENDING_CANCEL race window |
| `adverse_fill_rate` | 0.75 (measured, not set) | Post-fill price trajectory | **Validation metric**, not tunable |
| `cancel_discount` | 0.5 | Snapshot-only fallback only | N/A for hybrid (trades solve this) |
| `prob_function_power` | 2.0 | Snapshot-only fallback only | N/A for hybrid |
| `parity_tolerance` | 0.005 | Cross-leg parity check | Low |

### 8.3 Validation Criteria

A fill simulation is considered realistic if:

| Criterion | Threshold | Rationale |
|-----------|-----------|-----------|
| Adverse selection rate | 50-85% | Empirical range from Lalor & Swishchuk |
| Fill rate (of quotes placed) | 2-15% | Typical for market making on thin books |
| Realized spread | Positive but small | Spread capture must exceed adverse selection |
| Fill count relative to trades | < 50% of total market trades | We cannot fill more than the market trades |
| Cross-leg parity violations | < 0.1% of snapshots | YES_ask + NO_ask >= 1.00 should hold |
| YES fills independent of NO fills | No correlation | Independent books must have independent fill streams |
| Queue drain deterministic | All fills trace to a source trade | No fill without a corresponding trade event |
| Fills during PENDING_CANCEL | Present but rare | Models real cancel race conditions |

> [!important] Red Flags
> If backtest shows adverse selection rate < 40%, fill rate > 30%, Sharpe > 5, or fills that cannot be traced to a source trade event, the fill simulation is almost certainly too optimistic. See [[Performance-Metrics-and-Pitfalls#3.2 Backtesting Red Flags]].
>
> **Dual-orderbook red flags:** If YES and NO fill rates are suspiciously correlated (fills always happen simultaneously on both sides), or if parity violations exceed 1% of snapshots, investigate data quality before trusting results.

---

## 9. Academic References and Further Reading

### 9.1 Foundational Papers

1. **Cont, Stoikov & Talreja (2010)** — *A Stochastic Model for Order Book Dynamics.* Operations Research, 58(3), 549-563. Models LOB as continuous-time Markov chain with Poisson arrivals. Provides semi-analytical fill probabilities via Laplace transforms. ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1273160))

2. **Avellaneda & Stoikov (2008)** — *High-Frequency Trading in a Limit Order Book.* Quantitative Finance, 8(3), 217-224. Derives optimal bid/ask quotes with exponential fill intensity $\lambda(\delta) = Ae^{-\kappa\delta}$. The $\kappa$ parameter captures order book density. ([PDF](https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf))

3. **Moallemi & Yuan (2016)** — *A Model for Queue Position Valuation in a Limit Order Book.* Derives the value of queue priority and shows it depends on the ratio of market order arrivals to cancellations. ([PDF](https://moallemi.com/ciamac/papers/queue-value-2016.pdf))

### 9.2 Adverse Selection and Fill Realism

4. **Lalor & Swishchuk (2024)** — *Market Simulation under Adverse Selection.* arXiv:2409.12721. Empirically measures 65-89% adverse fill rates across ES, NQ, CL, ZN. Proposes framework separating adverse (deterministic) from non-adverse (probabilistic) fills. ([arXiv](https://arxiv.org/abs/2409.12721))

5. **Taranto et al. (2024)** — *The Negative Drift of a Limit Order Fill.* arXiv:2407.16527. Shows $P(\text{fill}|\text{adverse move}) \approx 0.99$ while $P(\text{fill}|\text{favorable move}) \approx 0.018$. Measured negative drift of -0.45 ticks per fill in 10Y Treasury futures. ([arXiv](https://arxiv.org/abs/2407.16527))

6. **Law & Viens (2019)** — *Market Making under a Weakly Consistent Limit Order Book Model.* High Frequency, 2(2). Identifies the "phantom gains" problem from excluding adverse fills. ([Wiley](https://onlinelibrary.wiley.com/doi/full/10.1002/hf2.10050))

### 9.3 Fill Probability Estimation

7. **Kolm, Ritter & Westray (2022)** — *A Deep Learning Approach to Estimating Fill Probabilities in a Limit Order Book.* Quantitative Finance, 22(11). Uses RNNs to estimate time-to-fill distributions. ([tandfonline](https://www.tandfonline.com/doi/full/10.1080/14697688.2022.2124189))

8. **van Kervel (2024)** — *Fill Probabilities in a Limit Order Book with State-Dependent Stochastic Order Flows.* arXiv:2403.02572. Extends Cont-Stoikov-Talreja with spread-dependent arrival rates. ([arXiv](https://arxiv.org/pdf/2403.02572))

### 9.4 Software and Frameworks

9. **HftBacktest** — Open-source HFT backtesting framework (Rust + Python) with probability queue models, latency simulation, and L2/L3 replay. ([GitHub](https://github.com/nkaz001/hftbacktest)) ([Docs](https://hftbacktest.readthedocs.io/))

10. **NautilusTrader** — Production-grade event-driven trading framework with Polymarket adapter and L2 MBP matching. ([Docs](https://nautilustrader.io/docs/latest/integrations/polymarket/))

11. **prediction-market-backtesting** — NautilusTrader fork with Polymarket + Kalshi adapters and PMXT L2 replay. ([GitHub](https://github.com/evan-kolberg/prediction-market-backtesting))

### 9.5 Order Flow Toxicity

12. **Easley, Lopez de Prado & O'Hara (2012)** — *Flow Toxicity and Liquidity in a High-Frequency World.* Review of Financial Studies. Introduces VPIN for real-time toxicity measurement. ([PDF](https://www.quantresearch.org/VPIN.pdf))

---

## 10. Glossary

| Term | Definition |
|------|-----------|
| **Adverse fill** | A fill where the mid-price subsequently moves against our position |
| **BBO** | Best bid and offer (top of book) |
| **Boxed position** | Holding both YES and NO tokens -- risk-free position worth $1.00 |
| **Cancel discount** | Fraction of depth decrease attributed to cancellations (not trades) |
| **Cross-leg parity** | YES_ask + NO_ask >= 1.00; YES_bid + NO_bid <= 1.00 |
| **Dual orderbook** | Two independent books per binary market (YES token book + NO token book) |
| **Depth imbalance (OIR)** | $(D_{bid} - D_{ask}) / (D_{bid} + D_{ask})$ — predictor of short-term price direction |
| **Fill intensity** | Rate at which fills occur, $\lambda(\delta) = Ae^{-\kappa\delta}$ in A-S model |
| **Front queue / Back queue** | Depth ahead of / behind our order at the same price level |
| **L2 / MBP** | Level 2 / Market-By-Price data — aggregated depth at each price level |
| **L3 / MBO** | Level 3 / Market-By-Order data — individual orders visible |
| **Negative drift** | The expected adverse price movement conditional on a fill |
| **Phantom fill** | A simulated fill that would not have occurred in reality |
| **Queue position** | Our place in the FIFO queue at a price level |
| **Snapshot interval** | Time between consecutive `book_snapshot_25` records (~0.1-3s) |
| **VPIN** | Volume-Synchronized Probability of Informed Trading |
| **$\kappa$** | Avellaneda-Stoikov order book density parameter |
| **$\rho$** | Non-adverse fill probability in Lalor-Swishchuk framework |

---

## Related Notes

- [[Backtesting-Architecture]] — Engine design and event loop
- [[Orderbook-Backtesting-with-Telonex]] — Telonex data integration
- [[Performance-Metrics-and-Pitfalls]] — Evaluation methodology and red flags
- [[NVDA-POC-Results]] — POC results demonstrating fill simulation impact
- [[Core-Market-Making-Strategies]] — Strategy formulations (Avellaneda-Stoikov)
- [[Telonex-Data-Platform]] — Data source reference
- [[Telonex-Data-Quality-Report]] — Data quality analysis for NVDA markets
