---
title: Inventory and Risk Management for Binary Market Making
created: 2026-03-31
updated: 2026-03-31
tags:
  - strategy
  - market-making
  - polymarket
  - inventory
  - risk-management
  - hedging
  - adverse-selection
  - VPIN
  - binary-options
sources:
  - https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf
  - https://arxiv.org/abs/1105.3115
  - https://www.research.hangukquant.com/p/digital-option-market-making-on-prediction
  - https://www.quantresearch.org/From%20PIN%20to%20VPIN.pdf
  - https://www.stern.nyu.edu/sites/default/files/assets/documents/con_035928.pdf
  - https://link.springer.com/article/10.1007/s11009-023-10013-6
  - http://www.deltaquants.com/managing-risks-of-digital-payoffs-overhedging
  - https://apps.olin.wustl.edu/faculty/liuh/Papers/Bid_Ask_JET.pdf
  - https://newyorkcityservers.com/blog/prediction-market-making-guide
---

# Inventory and Risk Management for Binary Market Making

This note covers inventory management, hedging strategies, adverse selection detection, and risk budgeting for market making on Polymarket's stock/index binary event markets.

See [[Core-Market-Making-Strategies]] for quoting frameworks, [[Capital-Efficiency-and-Edge-Cases]] for return metrics and edge cases, and [[Polymarket-CLOB-Mechanics]] for platform mechanics.

---

## 1. The Unique Challenge of Binary Inventory

### Why Binary Inventory Is Different

In traditional equity market making, inventory risk is **continuous**: holding 100 shares of a \$50 stock exposes you to gradual price movements. In binary market making, inventory risk is **discontinuous**: every position resolves to exactly \$1.00 or \$0.00. There is no middle ground.

| Property | Equity Market Making | Binary Market Making |
|----------|---------------------|---------------------|
| Position value range | Continuous | $\{0, 1\}$ only |
| Maximum loss per unit | Theoretically unlimited | Bounded: \$1.00 per token |
| Holding period | Flexible (can exit anytime) | Often locked until resolution |
| Inventory risk character | Gradual P&L drift | All-or-nothing resolution |
| Hedging | Delta hedging with underlying | Limited (see below) |
| Liquidation | Exit via market at any time | Can sell tokens, but thin liquidity |

### The YES/NO Duality

A critical structural property: buying YES is economically equivalent to selling NO, and vice versa. This means:

$$
\text{Long } q \text{ YES} \equiv \text{Short } q \text{ NO}
$$

The implications for inventory management:

1. **Net exposure calculation**: Inventory should be tracked as net directional exposure. If you hold +10 YES and +5 NO at the same strike, your net exposure is +5 YES (or equivalently -5 NO).
2. **Merge to exit**: Holding 1 YES + 1 NO can be merged back to \$1.00 USDC via the CTF contract, bypassing the order book entirely. This is a zero-slippage exit mechanism unique to the platform.
3. **Split to create**: \$1.00 USDC can be split into 1 YES + 1 NO, creating inventory for quoting both sides without taking directional risk.

### Inventory Lifecycle

```
Split USDC --> YES + NO inventory
  |
  v
Quote both sides of the book
  |
  v
Fills create directional exposure (net YES or net NO)
  |
  v
Manage via: (a) counter-fills, (b) quote skewing, (c) merge, (d) hedging
  |
  v
Resolution: winning tokens --> $1.00, losing tokens --> $0.00
```

---

## 2. Position Limits and Risk Budgeting

### Per-Market Position Limits

Every market should have a hard position limit $Q_{\max}$ that caps the maximum net directional exposure. This limit should reflect:

$$
Q_{\max} = \frac{\text{Max Acceptable Loss per Market}}{\text{Max Loss per Token}}
$$

Since max loss per token is \$1.00 (buying at \$1.00, resolving to \$0.00) or more realistically the purchase price $p$:

$$
Q_{\max} = \frac{L_{\max}}{p_{\text{avg}}}
$$

where $L_{\max}$ is the maximum acceptable loss and $p_{\text{avg}}$ is the expected average entry price.

### Example: Position Sizing

| Total Capital | Max Loss per Market (2%) | Avg Entry Price | Max Position |
|--------------|------------------------|----------------|--------------|
| \$10,000 | \$200 | \$0.50 | 400 tokens |
| \$10,000 | \$200 | \$0.80 | 250 tokens |
| \$50,000 | \$1,000 | \$0.50 | 2,000 tokens |
| \$50,000 | \$1,000 | \$0.20 | 5,000 tokens |

### Risk Budget Hierarchy

A multi-level risk budget prevents concentration:

| Level | Limit | Rationale |
|-------|-------|-----------|
| **Per-market** | 2% of capital | Single binary outcome should not be catastrophic |
| **Per-underlying** | 5% of capital | Multiple strikes on NVDA are correlated |
| **Per-sector** | 10% of capital | NVDA, AAPL, MSFT are correlated tech names |
| **Total portfolio** | 25% of capital at risk | Maximum aggregate directional exposure |

### The GLFT Inventory Constraint

The GLFT model from [[Core-Market-Making-Strategies]] enforces inventory limits directly. When $|q| = Q_{\max}$:

- **At long limit**: Stop quoting bids (no more buying). Only quote asks to reduce position.
- **At short limit**: Stop quoting asks (no more selling). Only quote bids to reduce position.
- **Near limits**: GLFT naturally widens the spread on the side that would increase exposure and narrows on the reduction side.

---

## 3. Quote Skewing for Inventory Management

### Linear Skewing

The simplest approach: shift the mid-price linearly based on inventory.

$$
\text{Skewed Mid} = V - \alpha \cdot q
$$

where:
- $V$ = fair value (options-implied probability)
- $q$ = net inventory (positive = long YES)
- $\alpha$ = skew intensity parameter (cents per unit of inventory)

| Bid | $= \text{Skewed Mid} - \delta$ |
|-----|------|
| Ask | $= \text{Skewed Mid} + \delta$ |

### Nonlinear (Urgency) Skewing

As inventory approaches limits, skewing should become more aggressive. A common approach:

$$
\text{Skewed Mid} = V - \alpha \cdot q \cdot \left(\frac{|q|}{Q_{\max}}\right)^\beta
$$

where $\beta > 0$ controls the convexity of the urgency function:
- $\beta = 0$: linear skewing (constant urgency)
- $\beta = 1$: quadratic skewing (increasing urgency)
- $\beta = 2$: cubic skewing (aggressive urgency near limits)

### Skewing Calibration Guidelines

| Inventory Level ($|q| / Q_{\max}$) | Skew Action |
|-------------------------------------|-------------|
| 0% - 25% | Minimal skew ($\alpha \leq 0.002$) |
| 25% - 50% | Moderate skew ($\alpha \approx 0.005$) |
| 50% - 75% | Aggressive skew ($\alpha \approx 0.01$) |
| 75% - 90% | Very aggressive skew ($\alpha \approx 0.02$) + spread widening |
| 90% - 100% | One-sided quoting only (reduce exposure side) |

### Example

Fair value: $V = 0.60$. $Q_{\max} = 100$. Current inventory: $q = +40$ YES tokens (40% of limit). $\alpha = 0.005$, $\beta = 1$, $\delta = 0.02$.

$$
\text{Skewed Mid} = 0.60 - 0.005 \times 40 \times \left(\frac{40}{100}\right)^1 = 0.60 - 0.08 = 0.52
$$

Wait -- this produces an 8-cent skew, which is extremely aggressive. In practice, $\alpha$ must be calibrated to produce sensible skews. A more practical formulation uses the AS reservation price directly:

$$
r = V - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)
$$

With $\gamma = 0.1$, $\sigma = 0.05$, $T - t = 1$ day:

$$
r = 0.60 - 40 \times 0.1 \times 0.0025 \times 1 = 0.60 - 0.01 = 0.59
$$

This produces a 1-cent skew -- more reasonable. The bid/ask become 0.57/0.61 instead of symmetric 0.58/0.62.

---

## 4. Hedging Binary Positions

### The Hedging Challenge

Binary (digital) options are notoriously difficult to hedge, especially near expiry. The fundamental problem: the payoff is a **step function** at the strike, and the delta of a binary option diverges as expiry approaches for ATM strikes.

$$
\Delta_{\text{binary}} = e^{-r\tau} \cdot \frac{\phi(d_2)}{S \cdot \sigma \sqrt{\tau}}
$$

As $\tau \to 0$ and $S \approx K$: $\Delta_{\text{binary}} \to \infty$.

### Hedging Methods

#### Method 1: Delta Hedging with the Underlying Stock

**Concept**: Hold $\Delta_{\text{binary}} \times$ shares of the underlying stock per binary position to neutralize directional risk.

| Advantage | Disadvantage |
|-----------|-------------|
| Theoretically complete hedge | Delta diverges near expiry for ATM |
| Liquid underlying market | Requires continuous rebalancing |
| Standard infrastructure | Transaction costs erode the hedge |

**Practical viability**: Only feasible for positions with $\tau > 1$ day and strikes not too close to the current price. For short-dated ATM positions, the rebalancing frequency and cost make delta hedging impractical.

#### Method 2: Call Spread (Overhedge) Replication

**Concept**: Replicate the binary payoff as a tight bull call spread in the options market.

A binary call with strike $K$ paying \$1 is approximately:

$$
\text{Binary}(K) \approx \frac{1}{\epsilon}\left[C(K - \epsilon/2) - C(K + \epsilon/2)\right]
$$

where $C(K)$ is a vanilla call with strike $K$ and $\epsilon$ is the "overhedge width."

The overhedge width is typically set at 3-8% of the binary payoff level. For a \$1 binary: $\epsilon \approx$ \$0.03 - \$0.08 in probability terms, translating to a strike spread in the underlying of:

$$
\Delta K = \epsilon \times S \times \sigma \sqrt{\tau}
$$

| Advantage | Disadvantage |
|-----------|-------------|
| Smooth Greeks (no delta explosion) | Options bid-ask spread cost |
| Standard options infrastructure | Need liquid options near the strike |
| Works near expiry | Imperfect replication (residual risk proportional to $\epsilon$) |

#### Method 3: Cross-Position Hedging on Polymarket

**Concept**: Use offsetting positions across strikes or underlyings on Polymarket itself.

Examples:
- Long YES at \$120, Short YES at \$125 creates a "range" bet paying if stock closes between 120-125
- Long YES on NVDA above \$120, Long NO on NDX above some level -- captures correlation

| Advantage | Disadvantage |
|-----------|-------------|
| No external platform needed | Thin Polymarket liquidity |
| Collateral efficiency (merge pairs) | Imperfect correlation between strikes |
| Captures Polymarket-specific mispricings | Multiple positions amplify inventory tracking complexity |

#### Method 4: No Hedging (Position Limits Only)

**Concept**: Accept the binary risk, manage it through strict position limits and diversification across many markets.

This is the most practical approach for a Polymarket-focused market maker, given:
1. Hedging costs in options markets may exceed the edge from Polymarket spread capture
2. Binary resolution is bounded (\$0 or \$1) -- maximum loss is known
3. Diversification across 10+ underlyings and multiple strikes provides natural risk reduction

| Advantage | Disadvantage |
|-----------|-------------|
| Zero hedging cost | Full exposure to resolution risk |
| Simplest implementation | Requires many uncorrelated markets for diversification |
| Maximum capital efficiency on Polymarket | Tail risk from correlated events (market crash) |

### Hedging Decision Framework

```
Is the position size > 5% of portfolio?
  |
  YES --> Hedge with call spread replication (Method 2)
  |
  NO --> Is time to expiry < 4 hours AND strike is ATM?
           |
           YES --> Reduce position or withdraw quotes (Method 4 with limits)
           |
           NO --> Accept the risk with position limits (Method 4)
```

---

## 5. Adverse Selection and Information Risk

### The Adverse Selection Problem

Adverse selection occurs when a market maker trades with a counterparty who has superior information. In the Polymarket context, adverse selection manifests primarily through:

1. **Underlying stock movements**: The stock price moves, shifting the true probability, before the market maker updates quotes
2. **Informed Polymarket traders**: Participants with faster pricing models or information pick off stale quotes
3. **Cross-platform information**: Traders monitoring real-time options market movements exploit Polymarket latency

### The Latency Window

The critical adverse selection window for stock/index binary markets:

```
Stock moves at time t=0
  |
  t=0.1s: Options market reprices
  |
  t=1-5s: Our model recalculates fair probability
  |
  t=1-3s: We update quotes on Polymarket
  |
  t=10-60s: Other Polymarket participants adjust
  |
  DANGER ZONE: t=0 to t=3s (our quotes are stale)
```

During the danger zone, our resting orders are at the wrong price. Informed traders (or faster bots) can pick them off.

### Quote Staleness Detection

Implement a staleness monitor that tracks the age of the current quote relative to the last fair value update:

$$
\text{Staleness} = t_{\text{now}} - t_{\text{last\_update}}
$$

| Staleness | Action |
|-----------|--------|
| < 2 seconds | Normal quoting |
| 2-5 seconds | Widen spread by 50% |
| 5-15 seconds | Widen spread by 100% or pull quotes |
| > 15 seconds | Pull all quotes immediately |

### Underlying Price Movement Detection

Monitor the underlying stock price for rapid moves that invalidate current quotes:

$$
\text{Move Score} = \frac{|S(t) - S(t - \Delta t)|}{\sigma_{\Delta t}}
$$

where $\sigma_{\Delta t}$ is the expected move over interval $\Delta t$.

| Move Score | Interpretation | Action |
|-----------|---------------|--------|
| < 1.5 | Normal fluctuation | Continue quoting |
| 1.5 - 3.0 | Moderate move | Widen spreads, update fair value |
| 3.0 - 5.0 | Large move | Pull quotes, recalculate, re-quote |
| > 5.0 | Extreme move (possible halt/news) | Pull all quotes, investigate |

### VPIN-Based Toxic Flow Detection

The Volume-Synchronized Probability of Informed Trading (VPIN) metric from Easley, Lopez de Prado, and O'Hara (2012) can be adapted for Polymarket order flow analysis.

#### Standard VPIN Framework

1. **Volume bucketing**: Divide trades into equal-volume buckets of size $V_B$
2. **Buy/sell classification**: Classify each trade as buy-initiated or sell-initiated (using trade direction relative to mid-price)
3. **Compute order imbalance**: For each bucket $n$:

$$
\text{OI}_n = |V_n^{\text{buy}} - V_n^{\text{sell}}|
$$

4. **VPIN**: Rolling average of order imbalance over $N$ buckets:

$$
\text{VPIN} = \frac{1}{N} \sum_{n=1}^{N} \frac{\text{OI}_n}{V_B}
$$

#### Adaptation for Polymarket

| Standard VPIN | Polymarket Adaptation |
|--------------|----------------------|
| Single asset volume | YES + NO volume combined (they are the same market) |
| Buy/sell classification by tick rule | Classify by token: buying YES = bullish, buying NO = bearish |
| Volume buckets of $V_B$ trades | Use $V_B = $ 50-200 USDC equivalent |
| Lookback $N = 50$ buckets | Shorter lookback $N = 20$-$30$ (thin market, faster signal needed) |

#### VPIN Response Protocol

| VPIN Level | Interpretation | Spread Adjustment |
|-----------|---------------|-------------------|
| < 0.3 | Normal flow | Base spread |
| 0.3 - 0.5 | Moderately informed | Widen by 25% |
| 0.5 - 0.7 | Likely informed flow | Widen by 50%, reduce position limits |
| > 0.7 | Highly toxic flow | Widen by 100% or pull quotes entirely |

### Quote Shading Algorithm

Combine all adverse selection signals into a unified quote adjustment:

$$
\delta_{\text{adjusted}} = \delta_{\text{base}} \times (1 + \lambda_1 \cdot \text{VPIN} + \lambda_2 \cdot \text{Staleness Factor} + \lambda_3 \cdot \text{Move Score})
$$

where $\lambda_1, \lambda_2, \lambda_3$ are sensitivity parameters calibrated from historical data.

Additionally, apply **asymmetric shading** when flow direction is detected:

$$
\delta_{\text{bid}} = \delta_{\text{adjusted}} \times (1 + \mu \cdot \text{NetBuyPressure})
$$

$$
\delta_{\text{ask}} = \delta_{\text{adjusted}} \times (1 - \mu \cdot \text{NetBuyPressure})
$$

where $\text{NetBuyPressure} \in [-1, 1]$ measures the directional bias of recent flow. When buy pressure is high (potential adverse selection on the ask side), widen the ask and tighten the bid.

---

## 6. Portfolio-Level Inventory Management

### Cross-Market Correlation

When market making across multiple strikes and underlyings, positions are correlated:

#### Same Underlying, Different Strikes

For "NVDA > \$120" and "NVDA > \$125" on the same expiry:

$$
\rho(V_{120}, V_{125}) \approx 1.0
$$

Both probabilities move together when NVDA moves. A large long YES position at \$120 combined with a large long YES at \$125 creates concentrated directional risk.

#### Same Sector, Different Underlyings

For NVDA and AAPL (both tech/NASDAQ):

$$
\rho(\text{NVDA}, \text{AAPL}) \approx 0.5 - 0.7
$$

Market-wide moves (macro news, Fed decisions) cause correlated losses across positions.

### Portfolio Delta Aggregation

Compute the aggregate portfolio sensitivity to each underlying:

$$
\Delta_{\text{portfolio}}^{(j)} = \sum_{i \in \text{markets for underlying } j} q_i \cdot \Delta_i^{\text{binary}}
$$

where $q_i$ is the net position in market $i$ and $\Delta_i^{\text{binary}}$ is the binary option delta.

**Risk limit**: Set maximum portfolio delta per underlying:

$$
|\Delta_{\text{portfolio}}^{(j)}| \leq \Delta_{\max}^{(j)}
$$

### Correlation-Adjusted Risk

The portfolio variance under a multivariate model:

$$
\text{Var}(\text{Portfolio P\&L}) = \sum_i \sum_j q_i \cdot q_j \cdot \sigma_i \cdot \sigma_j \cdot \rho_{ij}
$$

For $n$ positions with average correlation $\bar{\rho}$:

$$
\text{Var} \approx n \cdot \bar{q}^2 \cdot \bar{\sigma}^2 \cdot (1 + (n-1)\bar{\rho})
$$

This means that $n$ positions with average correlation $\bar{\rho} = 0.5$ have portfolio risk roughly $\sqrt{n \cdot (1 + (n-1) \cdot 0.5) / n} = \sqrt{0.5 + 0.5} = 1.0$ times the single-position risk multiplied by $\sqrt{n}$ -- essentially no diversification benefit!

> [!important] Concentration Risk
> For correlated tech stocks (NVDA, AAPL, MSFT, GOOGL, META, AMZN), treating each position as independent dramatically understates portfolio risk. During a broad market selloff, all "close above $K$" YES tokens lose value simultaneously.

### Inventory Decay Targets

Set inventory half-life targets to prevent stale position accumulation:

| Time to Expiry | Target Inventory Half-Life | Rationale |
|---------------|---------------------------|-----------|
| > 5 days | 24 hours | Gradual reduction; ample time |
| 2-5 days | 12 hours | More urgency |
| 1-2 days | 4 hours | Approaching resolution |
| < 1 day | 1 hour | Aggressive flattening |
| < 2 hours | 15 minutes | Near-expiry sprint to flat |

Implement via the AS/GLFT $\gamma$ parameter: increase $\gamma$ as expiry approaches to amplify the inventory penalty in the reservation price.

---

## 7. Emergency Procedures

### Market Halt Protocol

If the underlying stock is halted (trading suspension, circuit breaker):

1. **Immediately pull all quotes** on all markets for that underlying
2. **Do not re-quote** until the halt lifts and a stable price is established
3. **Assess position risk**: during halts, the probability may shift dramatically (earnings miss, regulatory news)
4. **Widen spreads 2-3x** for the first 5 minutes after trading resumes

### Rapid Move Protocol

If the underlying moves more than 2% in under 5 minutes:

1. **Pull all ATM quotes** (strikes within 2% of current price)
2. **Widen OTM/ITM quotes** by 50%
3. **Recalculate all fair values** from fresh options chain
4. **Re-quote gradually**: start with ITM/OTM strikes, wait for stability before ATM

### Platform Outage Protocol

If Polymarket API becomes unresponsive (see [[Polymarket-CLOB-Mechanics#Matching Engine]] for scheduled restarts):

1. **Orders remain live** on the book even if your connection drops
2. **Heartbeat timeout** (10 seconds without heartbeat) will cancel all orders -- this is your safety net
3. **After reconnection**: verify all order states before placing new ones
4. **Log all outage events** for post-mortem analysis

### Oracle/Resolution Dispute

If a market's resolution is disputed (see [[Polymarket-CLOB-Mechanics#Resolution Mechanics]]):

1. **Tokens remain tradeable** during the dispute period
2. **Risk**: resolution could be overturned -- the "winning" side may become the "losing" side
3. **Recommendation**: Exit all positions in disputed markets unless the dispute is clearly frivolous
4. **Timeline**: disputes can take 4-6 days to resolve; capital is locked

---

## 8. Risk Monitoring Dashboard

### Key Metrics to Track in Real-Time

| Metric | Calculation | Alert Threshold |
|--------|------------|-----------------|
| Net inventory per market | $\sum q_i$ (signed) | > 75% of $Q_{\max}$ |
| Portfolio delta per underlying | $\sum q_i \Delta_i$ | > $\Delta_{\max}$ |
| Aggregate capital at risk | $\sum |q_i| \times p_i$ | > 25% of total capital |
| Quote staleness | $t_{\text{now}} - t_{\text{last\_update}}$ | > 5 seconds |
| VPIN per market | Rolling order imbalance | > 0.5 |
| Realized spread | Avg sell price - avg buy price | < 0 (losing money on spread) |
| Fill rate asymmetry | Buys filled / sells filled | > 2:1 or < 1:2 (one-sided filling) |
| P&L per market | Realized + unrealized at fair value | Drawdown > 1% of capital |

### Daily Review Checklist

- [ ] Net inventory across all markets and underlyings
- [ ] Correlation-adjusted portfolio risk
- [ ] Fill analysis: adverse selection ratio (fills at worse-than-fair prices)
- [ ] Spread capture: average realized spread vs. quoted spread
- [ ] Liquidity rewards earned vs. inventory costs
- [ ] Position aging: any positions open > 2x expected half-life

---

## Related Notes

- [[Core-Market-Making-Strategies]] -- Quoting frameworks (AS, GLFT, multi-market)
- [[Capital-Efficiency-and-Edge-Cases]] -- Return metrics, capital allocation, edge cases
- [[Polymarket-CLOB-Mechanics]] -- Platform mechanics, fees, resolution
- [[Breeden-Litzenberger-Pipeline]] -- Fair value extraction
- [[Risk-Neutral-vs-Physical-Probabilities]] -- Risk premium adjustments
- [[Backtesting-Architecture]] -- System design for testing these strategies
