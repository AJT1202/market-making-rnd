---
title: Orderbook Microstructure Strategies
created: 2026-04-02
tags: [orderbook, microstructure, market-making, polymarket, L2-data, signals]
sources:
  - https://academic.oup.com/jfec/article-abstract/12/1/47/816163
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694
  - https://www.tandfonline.com/doi/full/10.1080/01621459.2014.982278
  - https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf
  - https://arxiv.org/abs/1312.0563
  - https://arxiv.org/abs/1011.6402
---

# Orderbook Microstructure Strategies

This note catalogues orderbook microstructure strategies applicable to market-making on **Polymarket binary event markets** (e.g., "Will NVDA close above $120 on April 2?"). The focus is on extracting short-horizon signals from L2 orderbook data that go beyond simple midpoint pricing, with concrete formulations, data mappings to our available feeds, and testable hypotheses for the [[Engine-Architecture-Plan|backtesting engine]].

## Data Environment

### Polymarket CLOB Data (via Telonex)

Our primary orderbook feed comes from Telonex snapshots of the Polymarket CLOB at ~1-second intervals. Each snapshot contains the full L2 book:

| Field | Description | Signal Relevance |
|-------|-------------|------------------|
| `bids[]` | Array of `{price, size}` sorted price-descending | Demand depth profile |
| `asks[]` | Array of `{price, size}` sorted price-ascending | Supply depth profile |
| `timestamp` | Snapshot epoch milliseconds | Temporal alignment |
| `hash` | Book state hash | Change detection between snapshots |
| `tick_size` | `0.01` (standard) or `0.001` (near 0/1) | Price grid granularity |

The Polymarket WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) also emits:

- **`book`** -- Full snapshot on subscribe and after trades
- **`price_change`** -- Individual level updates (new orders, cancellations); `size=0` means level removed
- **`last_trade_price`** -- Executed trade with `price`, `side`, `size`, `fee_rate_bps`
- **`best_bid_ask`** -- Top-of-book changes with `best_bid`, `best_ask`, `spread`

For more detail see [[Polymarket-CLOB-Mechanics]] and [[Polymarket-Data-API]].

### ThetaData Options Chain Data

We use ThetaData historical options chains to derive options-implied probabilities as our exogenous fair value signal. Key endpoints:

- **Option History Trade/Quote** (`/v3/option/history/trade_quote`) -- Tick-level trades paired with NBBO quotes at execution time. Fields: `price`, `size`, `bid`, `ask`, `bid_size`, `ask_size`, timestamps.
- **All Greeks Snapshot** (`/v3/option/snapshot/greeks/all`) -- Real-time greeks including `implied_vol`, `delta`, `d1`, `d2`, `underlying_price` for full chains.

The Breeden-Litzenberger pipeline (see [[Breeden-Litzenberger-Pipeline]]) converts these into risk-neutral probability distributions that serve as our exogenous fair value anchor. Orderbook microstructure signals then provide short-horizon *adjustments* around that anchor.

---

## 1. Order Book Imbalance (OBI)

### Academic Foundation

**Cont, Kukanov, Stoikov (2014)** -- "The Price Impact of Order Book Events," *Journal of Financial Econometrics* 12(1): 47--88.

The paper establishes that over short time intervals, price changes are mainly driven by **order flow imbalance** -- the imbalance between supply and demand at the best bid and ask. They demonstrate a linear relation between order flow imbalance and price changes, with slope inversely proportional to market depth.

### Core Formula

**Level-1 OBI (top of book only):**

$$
\text{OBI}_1 = \frac{V_{\text{bid}}^{(1)} - V_{\text{ask}}^{(1)}}{V_{\text{bid}}^{(1)} + V_{\text{ask}}^{(1)}}
$$

where $V_{\text{bid}}^{(1)}$ is the size at the best bid and $V_{\text{ask}}^{(1)}$ is the size at the best ask.

**Multi-level OBI (depth-weighted):**

$$
\text{OBI}_N = \frac{\sum_{i=1}^{N} V_{\text{bid}}^{(i)} - \sum_{i=1}^{N} V_{\text{ask}}^{(i)}}{\sum_{i=1}^{N} V_{\text{bid}}^{(i)} + \sum_{i=1}^{N} V_{\text{ask}}^{(i)}}
$$

**Exponentially decay-weighted OBI:**

$$
\text{OBI}_{\lambda} = \frac{\sum_{i=1}^{N} e^{-\lambda \cdot \delta_i^b} V_{\text{bid}}^{(i)} - \sum_{i=1}^{N} e^{-\lambda \cdot \delta_i^a} V_{\text{ask}}^{(i)}}{\sum_{i=1}^{N} e^{-\lambda \cdot \delta_i^b} V_{\text{bid}}^{(i)} + \sum_{i=1}^{N} e^{-\lambda \cdot \delta_i^a} V_{\text{ask}}^{(i)}}
$$

where $\delta_i^b = P_{\text{mid}} - P_{\text{bid}}^{(i)}$ and $\delta_i^a = P_{\text{ask}}^{(i)} - P_{\text{mid}}$ are distances from midpoint, and $\lambda$ controls the decay rate (levels closer to mid count more).

### Interpretation

| OBI Value | Meaning | Predicted Move |
|-----------|---------|----------------|
| +1.0 | All volume on bid, no asks | Strong upward pressure |
| +0.3 to +0.7 | Moderate bid-heavy imbalance | Slight upward drift |
| -0.3 to +0.3 | Balanced book | No directional signal |
| -0.3 to -0.7 | Moderate ask-heavy imbalance | Slight downward drift |
| -1.0 | All volume on ask, no bids | Strong downward pressure |

### Binary Market Adaptations

In Polymarket binary markets, the book is structurally bounded by $[0, 1]$. This creates asymmetric dynamics:

- **Near boundaries (p < 0.10 or p > 0.90):** The OBI signal is compressed because one side of the book is structurally thin. A market at 0.95 will naturally have thin ask-side depth (few sellers at 0.96--0.99). OBI must be normalized against a boundary-aware baseline.
- **Complementary token constraint:** Every Yes bid is economically equivalent to a No ask at the complement price. OBI should be computed on the *combined* Yes+No book for robustness.
- **Tick size transitions:** When price crosses 0.96 or drops below 0.04, tick size shifts from 0.01 to 0.001, dramatically changing the depth profile. OBI must be recomputed on the new grid.

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | `bids[].size`, `asks[].size` | Volume at each level |
| Telonex L2 snapshots | `bids[].price`, `asks[].price` | Distance weighting |
| Polymarket WS `price_change` | `size`, `side`, `price` | Real-time OBI updates between snapshots |

### Implementation Notes

```
For each snapshot at time t:
  1. Parse bids[] and asks[] arrays
  2. Compute OBI_1, OBI_3, OBI_5 (top 1, 3, 5 levels)
  3. Compute decay-weighted OBI with lambda = {0.5, 1.0, 2.0}
  4. Record midpoint at t, midpoint at t+k (k = 1, 5, 10 snapshots)
  5. Regress: delta_mid(t, t+k) = alpha + beta * OBI(t) + epsilon
```

---

## 2. Micro-Price Models

### Academic Foundation

**Stoikov (2018)** -- "The micro-price: a high-frequency estimator of future prices," *Quantitative Finance* 18(12): 1959--1966.

The micro-price is a **martingale by construction** and represents a better estimate of the "fair" price than the midpoint, conditional on orderbook information. It is computed by weighting bid and ask prices by the *opposite side's* volume -- the intuition being that a large bid queue signals the price is more likely to move toward the ask.

### Core Formula

**Basic micro-price:**

$$
P_{\mu} = P_{\text{ask}} \cdot \frac{V_{\text{bid}}}{V_{\text{bid}} + V_{\text{ask}}} + P_{\text{bid}} \cdot \frac{V_{\text{ask}}}{V_{\text{bid}} + V_{\text{ask}}}
$$

This can be rewritten as an adjustment to the midpoint:

$$
P_{\mu} = P_{\text{mid}} + \frac{S}{2} \cdot \text{OBI}_1
$$

where $S = P_{\text{ask}} - P_{\text{bid}}$ is the spread and $\text{OBI}_1$ is the level-1 order book imbalance.

**Multi-level micro-price (Stoikov extension):**

The full Stoikov micro-price models the book state as a discrete Markov chain over $(I, S)$ pairs where $I$ is the imbalance bucket and $S$ is the spread. The micro-price adjustment $G(I, S)$ is learned from data:

$$
P_{\mu}^{*} = P_{\text{mid}} + G(I, S)
$$

where $G$ satisfies a fixed-point equation ensuring the martingale property.

### Why Micro-Price Matters for Binary Markets

The standard midpoint is a poor fair-value estimate in Polymarket for several reasons:

1. **Wide spreads are common.** Many stock/index binary markets have 2--4 cent spreads. A midpoint at 0.50 when the book is 0.48/0.52 hides significant information about where the next trade will occur.
2. **Asymmetric queue depths.** If the bid side has 5000 shares at 0.48 and the ask has 500 at 0.52, the "fair" price is much closer to 0.52 than the midpoint suggests. The micro-price captures this.
3. **Inventory cost.** When quoting around a midpoint that systematically overestimates or underestimates fair value, the market maker accumulates adverse inventory. The micro-price reduces this adverse selection.

### Micro-Price vs. Midpoint: Expected Improvement

In our backtesting engine, replacing midpoint with micro-price as the fair value estimate should:

- **Reduce fill asymmetry:** Fewer fills on the "wrong" side of fair value
- **Improve quote centering:** Bid/ask quotes centered on micro-price should have more balanced fill rates
- **Reduce P&L variance:** Less adverse inventory accumulation

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | `bids[0].price`, `asks[0].price` | Best bid/ask prices |
| Telonex L2 snapshots | `bids[0].size`, `asks[0].size` | Top-of-book volumes |
| Telonex L2 snapshots | Full depth | Multi-level imbalance buckets |
| Polymarket WS `last_trade_price` | `price`, `side` | Validation: does micro-price better predict next trade price? |

### Implementation Notes

```
For each snapshot:
  1. Compute basic micro-price from top-of-book
  2. Compute spread S and imbalance I = V_bid / (V_bid + V_ask)
  3. Bucket (I, S) into discrete states -- e.g., I in 10 buckets [0,0.1)...[0.9,1.0], S in {1,2,3,4,...} ticks
  4. From historical data, for each state (I,S), compute:
     E[P(t+1) - P_mid(t) | state = (I,S)]
  5. This learned adjustment G(I,S) is the micro-price correction
  6. Validate: compare prediction error |P(t+k) - P_mu(t)| vs |P(t+k) - P_mid(t)|
```

---

## 3. Queue Position Analysis

### Academic Foundation

**Huang, Lehalle, Rosenbaum (2015)** -- "Simulating and Analyzing Order Book Data: The Queue-Reactive Model," *Journal of the American Statistical Association* 110(509): 107--122.

The queue-reactive model views the limit order book as a **Markov queuing system** where the intensities of order flows (arrivals, cancellations, market orders) depend on the current state of the book. Within periods where the midprice is constant, the queue dynamics at each level follow state-dependent Poisson processes.

### Why Queue Position Matters

For a market maker, the **position in the queue** at a given price level determines:

1. **Fill probability:** Orders earlier in the queue fill first (FIFO). Being 10th in a queue of 100 has ~10% fill probability for a single incoming market order of size 10.
2. **Fill timing:** Earlier queue position means faster fills, which matters for inventory turnover.
3. **Adverse selection exposure:** Late fills are more likely to occur when the price is about to move *away* from you (the informed traders already took the early fills).

### Queue Position Fill Probability Model

For a market maker at position $q$ in a queue of total depth $Q$ at a price level:

**Simple pro-rata approximation:**

$$
P(\text{fill} \mid \text{market order of size } m) = \min\left(\frac{m}{Q}, 1\right) \quad \text{(if pro-rata)}
$$

**FIFO fill probability (Polymarket uses FIFO):**

$$
P(\text{fill} \mid \text{market order of size } m) = \begin{cases} 1 & \text{if } q \leq m \\ 0 & \text{if } q > m \end{cases}
$$

In practice, the fill probability for a FIFO queue integrates over the distribution of incoming market order sizes:

$$
P(\text{fill}) = \int_q^{\infty} f_M(m) \, dm = 1 - F_M(q)
$$

where $F_M$ is the CDF of market order sizes arriving at that price level.

### Queue Dynamics in Polymarket

Polymarket's CLOB uses **price-time priority** (FIFO within each price level). Key observations from the data:

- **Queue building:** After a price level is established, subsequent limit orders stack behind existing ones. The `price_change` WebSocket event with increasing `size` signals queue growth.
- **Queue draining:** Market orders (trades) drain the front of the queue. The `last_trade_price` event with `size` tells us how much was consumed.
- **Cancellations:** The `price_change` event with decreasing `size` (but size > 0) indicates cancellations. We cannot directly observe *where* in the queue cancellations occur, but we can estimate.

### Cancellation Dynamics

A critical insight from Huang et al.: **cancellation rates depend on queue depth.** Empirically:

- Cancellation intensity *increases* when the queue is deep (crowded level, lower fill probability per order)
- Cancellation intensity *increases* when the opposite side thins (signal of impending adverse move)
- Cancellation intensity is highest at the best bid/ask (most sensitive to information)

### Implications for Fill Simulation in Backtesting

Our backtesting engine (see [[Fill-Simulation-Research]]) currently uses midpoint-based fill simulation. Queue-aware simulation would:

1. **Model queue position explicitly:** When our simulated order is placed, assign a queue position based on current depth at that level.
2. **Simulate queue evolution:** Between snapshots, model arrivals and cancellations as state-dependent Poisson processes (queue-reactive model).
3. **Determine fills realistically:** Only fill our order when cumulative incoming market order flow exceeds our queue position.

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | `bids[].size`, `asks[].size` at each level | Queue depth over time |
| Polymarket WS `price_change` | `size` delta at a price level | Queue growth / cancellation detection |
| Polymarket WS `last_trade_price` | `size`, `side` | Market order sizes (queue drain) |
| Derived | Size changes between consecutive snapshots | Arrival / cancellation rate estimation |

---

## 4. Book Pressure / Liquidity Signals

### Weighted Depth Metrics

Beyond simple OBI, we can construct richer liquidity signals from the full depth profile.

**Cumulative depth ratio at distance $\delta$:**

$$
\text{CDR}(\delta) = \frac{\sum_{i: \delta_i^b \leq \delta} V_{\text{bid}}^{(i)}}{\sum_{i: \delta_i^a \leq \delta} V_{\text{ask}}^{(i)}}
$$

Values > 1 indicate bid-heavy depth within $\delta$ of mid; values < 1 indicate ask-heavy.

**Book slope (bid side):**

$$
\text{Slope}_{\text{bid}} = \frac{d}{d\delta} \left( \sum_{i: \delta_i^b \leq \delta} V_{\text{bid}}^{(i)} \right)
$$

Approximated discretely as the regression slope of cumulative bid depth against distance from mid. A steep slope means liquidity concentrates near the top of book; a flat slope means it is distributed evenly.

**Depth-weighted pressure (DWP):**

$$
\text{DWP} = \sum_{i=1}^{N} \frac{V_{\text{bid}}^{(i)}}{\delta_i^b} - \sum_{i=1}^{N} \frac{V_{\text{ask}}^{(i)}}{\delta_i^a}
$$

This weights volume inversely by distance -- volume close to mid exerts more "pressure" than volume far away.

### Kyle's Lambda Adapted for Binary Markets

Kyle (1985) defines market impact parameter $\lambda$ as:

$$
\Delta P = \lambda \cdot \text{SignedVolume}
$$

For Polymarket, we estimate $\lambda$ empirically from trade data:

$$
\hat{\lambda} = \frac{\text{Cov}(\Delta P_t, \, Q_t)}{\text{Var}(Q_t)}
$$

where $Q_t$ is signed trade volume (positive for buys, negative for sells) and $\Delta P_t$ is the midpoint change following the trade.

**Binary market adaptations:**

- $\lambda$ is **price-dependent** in binary markets. Near 0.50, a given volume moves price less (deep books, balanced interest). Near 0.05 or 0.95, the same volume moves price more (thin books, concentrated interest).
- $\lambda$ should be estimated in **rolling windows** because it changes as the event approaches (expiration effect: books thin as resolution nears).
- $\lambda$ is **asymmetric** near boundaries: it is easier to push price from 0.90 to 0.92 than from 0.92 to 0.90 when the event is likely to resolve Yes.

### Predicting Short-Term Spread Changes

Book pressure metrics predict spread widening/tightening:

- **Depth withdrawal on one side** (CDR moving away from 1.0) predicts spread widening
- **Symmetric depth increase** predicts spread tightening
- **Rapid cancellation bursts** (detected from snapshot-to-snapshot size drops) predict spread widening and potential price movement

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | Full bid/ask arrays | All depth metrics |
| Polymarket WS `last_trade_price` | `price`, `size`, `side` | Kyle's lambda estimation |
| Derived | Midpoint changes post-trade | Price impact regression |
| Derived | Snapshot-to-snapshot depth changes | Cancellation detection |

---

## 5. Depth-Aware Spread Calibration

### Academic Foundation

**Avellaneda & Stoikov (2008)** -- "High-frequency trading in a limit order book," *Quantitative Finance* 8(3): 217--224.

The Avellaneda-Stoikov (A-S) model derives optimal bid and ask quotes for a market maker. The key parameter $\kappa$ (kappa) represents the **order arrival intensity** -- how quickly limit orders get filled as a function of their distance from the midpoint.

### The A-S Optimal Spread

The reservation price (inventory-adjusted fair value):

$$
r(s, q, t) = s - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)
$$

The optimal spread around the reservation price:

$$
\delta^* = \gamma \sigma^2 (T - t) + \frac{2}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right)
$$

where:
- $s$ = current midprice
- $q$ = inventory (positive = long)
- $\gamma$ = risk aversion parameter
- $\sigma$ = price volatility
- $T - t$ = time to horizon (market close / resolution)
- $\kappa$ = order arrival intensity parameter

### Calibrating Kappa from L2 Data

The parameter $\kappa$ governs how order fill probability decays with distance from mid. Avellaneda-Stoikov assume:

$$
\Lambda(\delta) = A \cdot e^{-\kappa \cdot \delta}
$$

where $\Lambda(\delta)$ is the intensity of market orders hitting a limit order posted at distance $\delta$ from mid.

**Empirical estimation from Polymarket L2 data:**

1. **Trade-based method:** For each observed trade, record the distance $\delta$ between the trade price and the pre-trade midpoint. Fit the exponential model to the empirical distribution of $\delta$ values.

2. **Book-based method:** Observe how quickly depth at each level gets consumed over time. Levels closer to mid turn over faster. The ratio of turnover rates at different distances gives $\kappa$:

$$
\hat{\kappa} = \frac{\ln(\text{turnover}(\delta_1) / \text{turnover}(\delta_2))}{\delta_2 - \delta_1}
$$

3. **Snapshot-based regression:** Across many snapshots, for each price level $\delta$ ticks from mid, compute the probability that the level sees a fill in the next $k$ seconds. Fit:

$$
\ln P(\text{fill at } \delta) = \ln A - \kappa \cdot \delta
$$

### Binary Market Considerations

- **Price-dependent kappa:** $\kappa$ varies with the probability level. At $p = 0.50$, books tend to be deepest and $\kappa$ highest (orders fill quickly because both sides are active). At $p = 0.10$, the ask side is thin and $\kappa_{\text{ask}}$ may be much lower than $\kappa_{\text{bid}}$.
- **Asymmetric kappa:** The A-S model assumes symmetric $\kappa$ for bid and ask fills. In binary markets, $\kappa_{\text{bid}} \neq \kappa_{\text{ask}}$ almost always. Calibrate separately.
- **Time-dependent kappa:** As resolution approaches, $\kappa$ generally increases (more trading activity) but book depth also thins. Net effect needs empirical measurement.
- **GLFT extension:** The Gueant-Lehalle-Fernandez-Tapia (2012) model generalizes A-S with closed-form solutions for inventory limits. The same $\kappa$ calibration applies.

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | Full depth at each level | Level turnover rates |
| Polymarket WS `last_trade_price` | `price`, `size` | Trade distance from mid |
| Derived | Fill events per level per time window | Kappa regression |
| ThetaData options | Implied volatility | Sigma calibration for A-S |

---

## 6. Orderbook-Based Adverse Selection Detection

### Concept

**Adverse selection** occurs when a market maker's quotes are filled by traders with superior information. In binary event markets, informed flow often appears just before price-moving news (earnings releases, economic data). Detecting adverse selection in real-time allows the market maker to widen spreads or pull quotes defensively.

### Detection Signals from Book Changes

**Signal 1: Rapid depth withdrawal (one-sided)**

When informed traders anticipate a price move, they cancel their resting orders on the side that will become stale. Observable as:

$$
\text{DepthDrop}_{\text{bid}}(t) = \frac{\sum V_{\text{bid}}(t) - \sum V_{\text{bid}}(t - \Delta t)}{\sum V_{\text{bid}}(t - \Delta t)}
$$

A sudden drop > 30--50% on one side without a corresponding trade is a strong adverse selection signal.

**Signal 2: Aggressive order placement**

Large limit orders placed at or near the best ask (bid) that immediately improve the book signal aggressive informed buying (selling). Detected from `price_change` events where a new level appears at a better price than the previous best.

**Signal 3: Trade size anomalies**

Informed traders often trade in sizes larger than typical flow. Compute a z-score of trade size relative to recent history:

$$
z_{\text{size}} = \frac{m_t - \bar{m}}{\sigma_m}
$$

where $m_t$ is the current trade size and $\bar{m}, \sigma_m$ are the rolling mean and standard deviation. A $z > 2$ combined with a directional move is a strong signal.

**Signal 4: OBI regime shifts**

Sudden OBI transitions (e.g., from 0.0 to +0.6 within 3--5 snapshots) without corresponding trades indicate informed limit order activity -- someone is building a position via limit orders before a catalyst.

### Composite Adverse Selection Score

$$
\text{AS}_t = w_1 \cdot |\text{DepthDrop}| + w_2 \cdot \mathbb{1}[\text{price improvement}] + w_3 \cdot z_{\text{size}} + w_4 \cdot |\Delta \text{OBI}|
$$

When $\text{AS}_t$ exceeds a threshold, the market maker should:
1. Widen spreads by 1--2 ticks
2. Reduce quote sizes
3. Skew quotes away from the predicted direction of the informed flow

### Integration with Options-Implied Fair Value

Our exogenous fair value from the [[Breeden-Litzenberger-Pipeline]] provides a baseline. When the Polymarket orderbook signals diverge sharply from the options-implied probability (e.g., book OBI says strong buying pressure but options-implied probability is flat), this is itself an adverse selection signal -- someone on Polymarket may know something the options market has not yet priced.

Conversely, when options IV spikes but the Polymarket book is quiet, the opportunity is to update quotes preemptively before the informed flow arrives on Polymarket.

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | Full depth time series | Depth withdrawal detection |
| Polymarket WS `price_change` | New best bid/ask | Price improvement detection |
| Polymarket WS `last_trade_price` | `size` | Trade size anomaly |
| ThetaData options | IV changes, delta shifts | Cross-market divergence |

---

## 7. Optimal Quote Placement

### The Placement Decision

Given a fair value estimate (micro-price) and a desired spread (from A-S/GLFT), the market maker must decide *where exactly* to place quotes relative to the existing book.

### Option A: Join the Queue at Best Bid/Ask

- **Pro:** Maximum fill probability -- you are at the tightest spread the market offers.
- **Pro:** Earns full spread when filled.
- **Con:** Queue position is last (behind existing depth at that level). Fill rate depends on how much market order flow arrives.
- **Con:** Higher adverse selection risk -- best bid/ask gets hit first by informed traders.

**Queue value estimation:** The expected value of being at position $q$ in a queue of depth $Q$ at the best bid:

$$
\text{QueueValue}(q, Q) = P(\text{fill}) \cdot \left(\frac{S}{2} - \text{AS cost}\right)
$$

where $P(\text{fill}) = 1 - F_M(q)$ from the market order size distribution and AS cost is the expected adverse selection cost per fill.

### Option B: Penny Jumping (Price Improvement)

- **Pro:** Immediate queue priority -- you are first in line at a new best price.
- **Pro:** Captures flow that would otherwise go to the existing best.
- **Con:** Earns a narrower spread (you gave up 1 tick on your side).
- **Con:** Invites penny-jumping wars -- others may improve on your price.

**Penny jumping payoff:**

$$
\text{PJ Payoff} = P(\text{fill at } P_{\text{best}} + \text{tick}) \cdot \left(\frac{S - \text{tick}}{2}\right) - P(\text{fill at } P_{\text{best}}) \cdot \left(\frac{S}{2}\right) \cdot \frac{q}{Q}
$$

Penny jumping is profitable when the existing queue is deep (low fill probability at best) and the spread is wide enough that giving up one tick is worthwhile.

### Option C: Post Behind Best (Passive)

- **Pro:** Lower adverse selection -- you only fill after the best level is fully consumed.
- **Pro:** In fast markets, the book may move to you (your level becomes the new best).
- **Con:** Lower fill probability.
- **Con:** Wider effective spread when filled.

### Decision Framework for Polymarket

| Condition | Recommended Placement |
|-----------|----------------------|
| Spread >= 4 ticks, thin best depth (< 200) | Penny jump -- plenty of spread to sacrifice 1 tick |
| Spread >= 4 ticks, deep best depth (> 1000) | Penny jump -- you will never fill joining the deep queue |
| Spread = 2 ticks, thin best depth | Join the queue -- 1-tick spread after penny jump is too thin |
| Spread = 2 ticks, deep best depth | Join the queue with reduced size, or quote 1 level behind |
| Spread = 1 tick | Join the queue -- no room to improve |
| Adverse selection score high | Post 1--2 levels behind best or pull quotes entirely |

### Data Requirements

| Source | Field | Usage |
|--------|-------|-------|
| Telonex L2 snapshots | Depth at best bid/ask | Queue depth assessment |
| Telonex L2 snapshots | Spread | Penny jumping viability |
| Derived | Market order size distribution $F_M$ | Fill probability at queue position |
| Derived | Adverse selection score | Placement aggressiveness |

---

## What This Enables Beyond Midpoint Backtesting

Our current backtesting engine (see [[NVDA-POC-Results]]) uses a simplified model:

1. Fair value = options-implied probability (from Breeden-Litzenberger)
2. Quotes are placed symmetrically around fair value at a fixed spread
3. Fills are simulated when the midpoint crosses our quote price

This midpoint-based approach misses significant alpha and risk management opportunities. The microstructure strategies above enable:

### Improvement 1: Micro-Price Fair Value

Replace midpoint with micro-price as the centerpoint for quoting. Expected benefit: **reduced adverse fill rate by 10--30%** based on the Stoikov (2018) finding that micro-price is a significantly better predictor of next trade direction.

### Improvement 2: Dynamic Spread from Book State

Replace fixed spread with A-S/GLFT spread calibrated from actual book dynamics ($\kappa$, $\sigma$). Expected benefit: **tighter spreads in calm markets** (more fills, more revenue), **wider spreads in volatile/informed-flow regimes** (less adverse selection cost).

### Improvement 3: Realistic Fill Simulation

Replace midpoint-cross fill model with queue-position-aware simulation. Expected benefit: **more accurate P&L estimates** -- current model likely overestimates fill rates (it assumes infinite queue priority) and underestimates adverse selection.

### Improvement 4: Adverse Selection Avoidance

Real-time detection of informed flow from book changes allows defensive quoting. Expected benefit: **reduced max drawdown** during adverse events, particularly around news catalysts that move the underlying stock price.

### Improvement 5: Optimal Quote Placement

Rather than always quoting at best bid/ask, dynamically choosing placement based on queue depth, spread, and adverse selection signals. Expected benefit: **improved fill quality** -- better balance between fill rate and adverse selection.

### Implementation Priority

| Strategy | Implementation Effort | Expected Impact | Priority |
|----------|----------------------|-----------------|----------|
| Micro-price (2) | Low -- formula-based | High | **P0** |
| OBI signal (1) | Low -- formula-based | Medium-High | **P0** |
| Kappa calibration (5) | Medium -- needs regression | High | **P1** |
| Book pressure (4) | Medium -- multiple signals | Medium | **P1** |
| Adverse selection (6) | Medium -- composite score | High | **P1** |
| Queue position (3) | High -- needs fill simulation rewrite | Very High | **P2** |
| Quote placement (7) | High -- needs decision framework | Medium | **P2** |

---

## Backtesting Test Specifications

### Test 1: OBI Predictive Power

**Objective:** Validate that order book imbalance predicts short-term midpoint direction in Polymarket stock/index binary markets.

**Procedure:**
1. For each L2 snapshot, compute $\text{OBI}_1$, $\text{OBI}_3$, $\text{OBI}_5$, and $\text{OBI}_{\lambda}$ (with $\lambda \in \{0.5, 1.0, 2.0\}$).
2. Compute forward midpoint change at horizons $k \in \{1, 5, 10, 30, 60\}$ seconds.
3. Compute Spearman rank correlation between each OBI variant and forward midpoint change.
4. Compute directional accuracy: $P(\text{sign}(\Delta P_{t+k}) = \text{sign}(\text{OBI}_t))$.

**Pass criteria:** Directional accuracy > 52% at $k = 5s$ for at least one OBI variant. Spearman $|\rho| > 0.05$ with $p < 0.01$.

**Data:** Telonex NVDA binary market L2 snapshots, minimum 5 trading days.

### Test 2: Micro-Price vs. Midpoint Accuracy

**Objective:** Confirm micro-price is a better predictor of the next trade price than midpoint.

**Procedure:**
1. For each snapshot, compute midpoint and micro-price.
2. Identify the next trade price from `last_trade_price` events.
3. Compute $\text{MAE}_{\text{mid}} = |P_{\text{trade}} - P_{\text{mid}}|$ and $\text{MAE}_{\mu} = |P_{\text{trade}} - P_{\mu}|$.
4. Paired comparison across all snapshots.

**Pass criteria:** $\text{MAE}_{\mu} < \text{MAE}_{\text{mid}}$ with paired t-test $p < 0.01$.

**Data:** Same as Test 1, requiring trade-level data aligned to snapshots.

### Test 3: Kappa Stability and Spread Improvement

**Objective:** Estimate $\kappa$ from historical data and confirm it produces better spread calibration than a fixed spread.

**Procedure:**
1. Estimate $\kappa$ using the snapshot-based regression method (Section 5) on a training window (first 3 days).
2. Compute A-S optimal spread using estimated $\kappa$ and rolling $\sigma$ from ThetaData options IV.
3. Backtest MM strategy with:
   - **Baseline:** Fixed 2-cent spread centered on midpoint.
   - **Treatment A:** Fixed 2-cent spread centered on micro-price.
   - **Treatment B:** A-S dynamic spread centered on micro-price.
4. Compare: total P&L, Sharpe ratio, max drawdown, fill rate.

**Pass criteria:** Treatment B achieves higher Sharpe ratio than Baseline. Treatment A achieves lower max drawdown than Baseline.

**Data:** Telonex L2 + trade data, ThetaData options IV for the underlying, minimum 5 trading days.

### Test 4: Adverse Selection Signal Validation

**Objective:** Confirm that the composite adverse selection score predicts large adverse price moves.

**Procedure:**
1. Compute $\text{AS}_t$ for each snapshot using signals from Section 6.
2. Define "adverse event" as midpoint moving > 3 ticks against a market maker's position within 30 seconds of a fill.
3. Compare $\text{AS}_t$ in the 10 seconds preceding adverse events vs. a matched sample of non-adverse fills.
4. ROC analysis: can $\text{AS}_t$ discriminate adverse from non-adverse fills?

**Pass criteria:** AUC > 0.60 for the ROC curve.

**Data:** Telonex L2 + trade data, minimum 5 trading days with sufficient trade count (> 200 trades/day).

### Test 5: Queue Position Fill Accuracy

**Objective:** Validate that queue-position-aware fill simulation is more realistic than midpoint-cross simulation.

**Procedure:**
1. Replay historical L2 snapshots and trades.
2. For each simulated order placement, assign queue position based on depth at placement time.
3. Simulate fills using:
   - **Model A:** Midpoint-cross (current model)
   - **Model B:** Queue-position with empirical market order size distribution
4. Compare simulated fill times and fill rates against actual historical fills for orders placed by real market makers (if identifiable from trade data).

**Pass criteria:** Model B fill rate is within 20% of observed fill rates; Model A overestimates fill rate by > 30%.

**Data:** Telonex L2 + trade data with sufficient depth to reconstruct queue dynamics.

### Test 6: End-to-End Strategy Comparison

**Objective:** Full strategy backtest comparing microstructure-enhanced strategy against baseline.

**Procedure:**
1. **Baseline strategy:** Quote at midpoint +/- 1 cent, fixed size, midpoint-cross fills.
2. **Enhanced strategy:** Quote at micro-price +/- A-S spread, dynamic sizing from book pressure, adverse selection pull-back, queue-aware fills.
3. Run both on identical historical data for 10+ market-days across multiple NVDA binary strike levels.
4. Compare: cumulative P&L, Sharpe, max drawdown, fill count, average spread earned, adverse selection cost per fill.

**Pass criteria:** Enhanced strategy improves Sharpe by > 0.3 and reduces max drawdown by > 15%.

**Data:** Full Telonex dataset + ThetaData options chains for the test period.

---

## Related Notes

- [[Polymarket-CLOB-Mechanics]] -- CLOB architecture, order types, settlement mechanics
- [[Polymarket-Data-API]] -- REST and WebSocket API endpoints for market data
- [[Core-Market-Making-Strategies]] -- Avellaneda-Stoikov, GLFT, and inventory management theory
- [[NVDA-POC-Results]] -- Current backtesting engine results and performance baseline
- [[Fill-Simulation-Research]] -- Fill model design and queue simulation approaches
- [[Engine-Architecture-Plan]] -- Backtesting engine design incorporating these signals
- [[Breeden-Litzenberger-Pipeline]] -- Options-implied probability extraction as exogenous fair value
- [[Vol-Surface-Fitting]] -- Volatility surface construction for sigma calibration

## References

1. Cont, R., Kukanov, A., & Stoikov, S. (2014). The price impact of order book events. *Journal of Financial Econometrics*, 12(1), 47--88. [Link](https://academic.oup.com/jfec/article-abstract/12/1/47/816163)
2. Stoikov, S. (2018). The micro-price: a high-frequency estimator of future prices. *Quantitative Finance*, 18(12), 1959--1966. [Link](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694)
3. Huang, W., Lehalle, C.-A., & Rosenbaum, M. (2015). Simulating and analyzing order book data: The queue-reactive model. *Journal of the American Statistical Association*, 110(509), 107--122. [Link](https://arxiv.org/abs/1312.0563)
4. Avellaneda, M. & Stoikov, S. (2008). High-frequency trading in a limit order book. *Quantitative Finance*, 8(3), 217--224. [Link](https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf)
5. Kyle, A. S. (1985). Continuous auctions and insider trading. *Econometrica*, 53(6), 1315--1335.
6. Gueant, O., Lehalle, C.-A., & Fernandez-Tapia, J. (2012). Dealing with the inventory risk: A solution to the market making problem. *Mathematics and Financial Economics*, 7(4), 477--507.
