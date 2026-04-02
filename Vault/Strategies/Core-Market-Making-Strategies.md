---
title: Core Market Making Strategies for Binary Event Markets
created: 2026-03-31
updated: 2026-03-31
tags:
  - strategy
  - market-making
  - polymarket
  - binary-options
  - quoting
  - avellaneda-stoikov
  - GLFT
  - arbitrage
  - theta
sources:
  - https://people.orie.cornell.edu/sfs33/LimitOrderBook.pdf
  - https://arxiv.org/abs/1105.3115
  - https://arxiv.org/abs/1605.01862
  - https://www.research.hangukquant.com/p/digital-option-market-making-on-prediction
  - https://medium.com/hummingbot/a-comprehensive-guide-to-avellaneda-stoikovs-market-making-strategy-102d64bf5df6
  - https://docs.polymarket.com/developers/market-makers/liquidity-rewards
  - https://newyorkcityservers.com/blog/prediction-market-making-guide
---

# Core Market Making Strategies for Binary Event Markets

This note covers the primary quoting strategies for market making on Polymarket's stock/index binary event markets. Each strategy is grounded in established market microstructure theory, adapted for the specific properties of binary (digital) options that resolve to $1.00 or $0.00.

See [[polymarket-stock-index-mm-research-brief]] for the full research context, [[Polymarket-CLOB-Mechanics]] for platform mechanics, and [[Breeden-Litzenberger-Pipeline]] for the fair value extraction methodology.

---

## 1. Probability-Based Quoting

### Concept

The simplest and most fundamental strategy: use the options-implied probability as the fair value (mid), and quote symmetrically around it. The edge comes from the informational superiority of the options market over Polymarket's thin, retail-dominated order book.

### Fair Value Derivation

The fair value of a YES token for "Will $S$ close above $K$ on date $T$?" is the risk-neutral probability:

$$
V_{\text{YES}} = P_{\text{RN}}(S_T > K) = \int_K^{\infty} q(x) \, dx
$$

where $q(x)$ is the risk-neutral density extracted via the [[Breeden-Litzenberger-Pipeline]]:

$$
q(K) = e^{rT} \frac{\partial^2 C}{\partial K^2}
$$

For short-dated contracts ($\leq 2$ days), we drop $r$ and use:

$$
V_{\text{YES}} \approx \int_K^{\infty} \frac{\partial^2 C}{\partial K^2} dK
$$

The complementary NO token fair value is simply $V_{\text{NO}} = 1 - V_{\text{YES}}$.

### Quoting Mechanics

Given fair value $V$ and desired half-spread $\delta$:

| Side | Quote |
|------|-------|
| Bid (YES) | $V - \delta$ |
| Ask (YES) | $V + \delta$ |
| Bid (NO) | $(1 - V) - \delta$ |
| Ask (NO) | $(1 - V) + \delta$ |

### Example

- NVDA spot: \$122.50
- Strike: \$120
- Expiry: tomorrow's close
- Options-implied $P(S_T > 120) = 0.72$
- Polymarket midpoint: 0.68
- Chosen half-spread: $\delta = 0.02$

| Token | Bid | Ask | Polymarket Mid |
|-------|-----|-----|----------------|
| YES | 0.70 | 0.74 | 0.68 |
| NO | 0.26 | 0.30 | 0.32 |

The market maker quotes YES at 0.70/0.74 versus Polymarket's 0.68 mid. A taker buying YES at 0.74 pays fair value plus 2 cents of spread. If the options-implied probability is accurate, the market maker earns the spread on round-trip trades and has a structural edge because 0.68 is cheap relative to the 0.72 fair value.

### Risk-Neutral vs. Physical Probability Adjustment

Options-implied probabilities are risk-neutral, not physical. They systematically overstate left-tail probabilities due to:
- **Variance risk premium**: investors pay for downside protection
- **Skew risk premium**: put skew inflates the implied probability of large drops

For a YES token on "close above $K$":
- Risk-neutral $P(S_T > K)$ **understates** the physical probability for $K < S$ (ITM strikes)
- Risk-neutral $P(S_T > K)$ **overstates** the physical probability for deep OTM calls

See [[Risk-Neutral-vs-Physical-Probabilities]] for adjustment methods. In practice, the mispricing on Polymarket is typically large enough (5-15 cents) that the risk premium adjustment (1-3 cents) is secondary.

---

## 2. Inventory-Aware Quoting (Avellaneda-Stoikov Framework)

### Concept

The Avellaneda-Stoikov (AS) model (2008) is the canonical framework for optimal market making in limit order books. It derives quotes that maximize the expected utility of terminal wealth, explicitly penalizing inventory accumulation. We adapt it here for binary markets.

### The Reservation Price

The core insight: a market maker with inventory should not use the market mid-price as their reference. Instead, they compute a **reservation price** that reflects their inventory risk:

$$
r(t) = S(t) - q \cdot \gamma \cdot \sigma^2 \cdot (T - t)
$$

where:
- $S(t)$ = current fair value (options-implied probability for our case)
- $q$ = current inventory position (positive = long YES, negative = short YES)
- $\gamma$ = risk aversion parameter (higher = more aggressive inventory reduction)
- $\sigma^2$ = variance of the fair value process
- $T - t$ = time remaining to horizon

**Adaptation for binary markets:** In the standard AS model, $S$ is a stock price following geometric Brownian motion. For binary markets, $S$ is the fair probability $V_{\text{YES}} \in [0, 1]$. The volatility $\sigma$ is the volatility of the probability process, not the underlying stock. This is related to but distinct from the stock's realized volatility.

### The Optimal Spread

The optimal half-spread around the reservation price:

$$
\delta^*(t) = \frac{1}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right) + \frac{1}{2} \gamma \sigma^2 (T - t)
$$

where:
- $\kappa$ = order arrival intensity parameter (higher $\kappa$ = denser order book = tighter spreads)
- The first term captures the market microstructure component (order book density)
- The second term captures the inventory risk premium (wider spreads for more volatile assets or longer horizons)

### Optimal Bid and Ask

$$
\text{Bid} = r(t) - \delta^*(t) = S(t) - q \gamma \sigma^2 (T-t) - \delta^*(t)
$$

$$
\text{Ask} = r(t) + \delta^*(t) = S(t) - q \gamma \sigma^2 (T-t) + \delta^*(t)
$$

### Inventory Feedback Mechanism

The model creates a self-correcting feedback loop:

| Inventory State | Reservation Price Shift | Effect |
|----------------|------------------------|--------|
| Long YES ($q > 0$) | Reservation price **decreases** | Bid drops, ask drops -- encourages selling |
| Short YES ($q < 0$) | Reservation price **increases** | Bid rises, ask rises -- encourages buying |
| Flat ($q = 0$) | No shift | Quotes symmetric around fair value |

### Parameter Calibration for Binary Markets

| Parameter | Traditional Market | Binary Market Adaptation |
|-----------|-------------------|--------------------------|
| $\sigma$ | Stock return volatility | Probability process volatility ($\approx 0.01$-$0.10$ per hour depending on moneyness and time to expiry) |
| $\gamma$ | Risk aversion ($10^{-4}$ to $10^{-1}$) | Higher values recommended due to binary resolution risk ($10^{-2}$ to $1$) |
| $\kappa$ | Calibrated from order book | Much lower on Polymarket than traditional markets (thin books) |
| $T - t$ | End of trading day | Contract expiry (hours to days) |

### Example: Inventory Skewing

Starting position: flat. Fair value $V = 0.55$, $\gamma = 0.5$, $\sigma = 0.05$, $T - t = 0.5$ days, $\kappa = 2$.

**Step 1: Flat inventory ($q = 0$)**
- $r = 0.55 - 0 = 0.55$
- $\delta^* = \frac{1}{0.5}\ln(1 + \frac{0.5}{2}) + \frac{1}{2}(0.5)(0.0025)(0.5) = 0.446 + 0.0003 \approx 0.45$ -- but this is too wide; in practice $\kappa$ is calibrated to produce reasonable spreads
- With calibrated parameters yielding $\delta^* = 0.02$: Bid = 0.53, Ask = 0.57

**Step 2: After accumulating $q = +5$ YES tokens**
- $r = 0.55 - 5 \times 0.5 \times 0.0025 \times 0.5 = 0.55 - 0.003 = 0.547$
- Bid = 0.527, Ask = 0.567
- Quotes shift down by 0.3 cents, encouraging sales to reduce long inventory

> [!important] Binary-Specific Constraint
> Unlike stocks where inventory risk is continuous, binary positions resolve to exactly $0 or $1. A large long YES position that resolves to $0 is a total loss. The AS framework captures the "during-life" inventory risk through $\sigma$, but does not fully capture the discontinuous resolution risk. See [[Inventory-and-Risk-Management]] for supplementary position limits and hedging.

---

## 3. Gueant-Lehalle-Fernandez-Tapia (GLFT) Model

### Concept

The GLFT model (2013) extends Avellaneda-Stoikov by imposing explicit **inventory constraints** and deriving closed-form approximations. This is critical for binary markets where position sizes must be bounded.

### Framework

The market maker maximizes expected CARA utility:

$$
\max_{\delta^a, \delta^b} \mathbb{E}\left[-\exp\left(-\gamma \cdot X_T\right)\right]
$$

subject to inventory constraints $|q| \leq Q_{\max}$, where $X_T$ is the terminal P&L.

### Closed-Form Approximation

The GLFT optimal quotes are:

$$
\delta^{a*}(q) = \frac{1}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right) + \frac{(2q + 1)}{2} \sqrt{\frac{\gamma \sigma^2 (T-t)}{2\kappa} \left(1 + \frac{\gamma}{\kappa}\right)^{1+\frac{\kappa}{\gamma}}}
$$

$$
\delta^{b*}(q) = \frac{1}{\gamma} \ln\left(1 + \frac{\gamma}{\kappa}\right) - \frac{(2q - 1)}{2} \sqrt{\frac{\gamma \sigma^2 (T-t)}{2\kappa} \left(1 + \frac{\gamma}{\kappa}\right)^{1+\frac{\kappa}{\gamma}}}
$$

where $\delta^a$ is the ask distance from mid and $\delta^b$ is the bid distance from mid.

### Key Properties

1. **Inventory-dependent asymmetry**: When $q > 0$ (long), ask spread narrows and bid spread widens -- encourages selling
2. **Time decay**: As $T - t \to 0$, the inventory-dependent component shrinks, quotes converge to the base spread
3. **Boundary behavior at $Q_{\max}$**: When inventory hits the limit, the model stops quoting on the side that would increase exposure

### Advantages Over AS for Binary Markets

| Feature                   | Avellaneda-Stoikov       | GLFT                                   |     |                 |
| ------------------------- | ------------------------ | -------------------------------------- | --- | --------------- |
| Inventory constraints     | Implicit (soft penalty)  | Explicit ($                            | q   | \leq Q_{\max}$) |
| Closed-form solution      | Asymptotic approximation | Exact via spectral decomposition       |     |                 |
| Position limits           | Not enforced             | Built into the model                   |     |                 |
| Binary market suitability | Requires adaptation      | Better suited due to bounded positions |     |                 |
|                           |                          |                                        |     |                 |

---

## 4. Multi-Market Quoting

### Concept

Polymarket often has multiple strike levels for the same underlying and expiry. For example, for NVDA expiring Friday:
- "Will NVDA close above \$115?"
- "Will NVDA close above \$120?"
- "Will NVDA close above \$125?"
- "Will NVDA close above \$130?"

A multi-market strategy quotes across all strikes simultaneously, exploiting the **structural relationship** between them.

### Probability Monotonicity Constraint

For a given underlying and expiry, fair values must satisfy:

$$
V(K_1) > V(K_2) > V(K_3) \quad \text{for} \quad K_1 < K_2 < K_3
$$

The probability of closing above a lower strike is always higher. If Polymarket prices violate this ordering, there is a pure arbitrage opportunity.

### No-Arbitrage Spread Constraint

The difference between adjacent strike probabilities must be non-negative and bounded:

$$
0 \leq V(K_i) - V(K_{i+1}) \leq 1
$$

This difference is related to the probability of the stock closing **between** $K_i$ and $K_{i+1}$:

$$
P(K_i < S_T \leq K_{i+1}) = V(K_i) - V(K_{i+1})
$$

### Cross-Strike Inventory Management

When quoting multiple strikes on the same underlying, inventory correlations matter:

| Position | Correlation | Risk Implication |
|----------|-------------|------------------|
| Long YES at $K=120$, Long YES at $K=125$ | High positive | Both lose if stock drops sharply below 120 |
| Long YES at $K=120$, Short YES at $K=125$ | Creates a "binary spread" | Capped risk: wins if stock between 120-125 |
| Long YES at $K=120$, Long NO at $K=115$ | Partially hedging | YES@120 needs >120; NO@115 needs <115 -- only both win if S < 115 is impossible given the trade |

> [!tip] Portfolio-Level Greeks
> Treat the portfolio of binary positions as a collection of digital options. The aggregate delta of the portfolio to the underlying stock is the sum of individual deltas, which can be computed from the options-implied density:
>
> $$\Delta_{\text{digital}} = e^{-rT} \cdot \phi(d_2) \cdot \frac{1}{\sigma \sqrt{T-t}}$$
>
> where $\phi$ is the standard normal PDF. This allows portfolio-level hedging with the underlying stock or vanilla options.

### Implementation: Joint Quoting Algorithm

```
For each update cycle:
  1. Fetch options chain for underlying/expiry
  2. Extract P_RN(S_T > K_i) for all active strikes K_i
  3. Enforce monotonicity constraint across strikes
  4. For each strike K_i:
     a. Compute reservation price r_i(t) using current inventory q_i
     b. Compute optimal spread delta_i using GLFT/AS model
     c. Apply cross-strike correlation adjustment to gamma parameter
  5. Submit batch orders across all strikes (up to 15 per batch via CLOB)
  6. Monitor aggregate portfolio exposure
```

---

## 5. Cross-Market Arbitrage

### Concept

Exploit mispricings between Polymarket's YES/NO token prices and the options-implied probability. This is the directional component of our edge, distinct from pure spread capture.

### Signal Construction

The core signal:

$$
\alpha(t) = P_{\text{Polymarket}}^{\text{YES}}(t) - V_{\text{YES}}(t)
$$

where $V_{\text{YES}}(t)$ is the options-implied fair value.

| Signal | Interpretation | Action |
|--------|---------------|--------|
| $\alpha > +\theta$ | YES overpriced on Polymarket | Sell YES / Buy NO |
| $\alpha < -\theta$ | YES underpriced on Polymarket | Buy YES / Sell NO |
| $|\alpha| \leq \theta$ | Within fair value band | Quote symmetrically, no directional bias |

The threshold $\theta$ should account for:
- Polymarket bid-ask spread and execution slippage
- Taker fees if crossing the spread (typically 0 for stock/index markets, but verify via [[Polymarket-CLOB-Mechanics]])
- Model uncertainty in the Breeden-Litzenberger extraction
- Capital opportunity cost until resolution

### Practical Threshold Calibration

For stock/index binary markets (no taker fees for most):

$$
\theta = \underbrace{\frac{s_{\text{PM}}}{2}}_{\text{half-spread}} + \underbrace{\epsilon_{\text{model}}}_{\text{model uncertainty}} + \underbrace{c_{\text{capital}}}_{\text{capital cost}}
$$

Typical values:
- $s_{\text{PM}} / 2 \approx 0.02$ (Polymarket half-spread for liquid stock markets)
- $\epsilon_{\text{model}} \approx 0.01$-$0.03$ (depends on options chain density near strike)
- $c_{\text{capital}} \approx 0.001$-$0.005$ (daily opportunity cost at ~5% APY)
- **Total $\theta \approx 0.03$-$0.05$** (3-5 cents)

### Integration with Market Making

Cross-market arbitrage integrates naturally with the quoting strategy:

1. **Within threshold**: Use standard symmetric quoting (AS/GLFT framework)
2. **Outside threshold**: Shift quotes aggressively in the alpha direction
   - If $\alpha > \theta$: quote tight on the sell-YES side, wide or absent on the buy-YES side
   - If $\alpha < -\theta$: quote tight on the buy-YES side, wide or absent on the sell-YES side
3. **Update frequency**: Re-extract options-implied probability on every options chain update (typically every few seconds during market hours)

### Latency Considerations

The key latency risk: the underlying stock moves, the options market reprices instantly, but Polymarket lags. During this lag, a market maker quoting stale prices will be adversely selected.

| Component | Typical Latency |
|-----------|----------------|
| Stock price movement | Instantaneous |
| Options chain repricing | < 1 second |
| Options-implied probability recalculation | 1-5 seconds (computation) |
| Polymarket price adjustment by other participants | 10-60 seconds |
| Your quote update on Polymarket | 1-3 seconds (API latency) |

The critical window is the 10-60 second gap where Polymarket prices are stale. During this window, informed traders may pick off stale quotes. See [[Inventory-and-Risk-Management#Adverse Selection]] for mitigation.

---

## 6. Time-Decay (Theta) Strategies

### Binary Option Time Decay

A binary (digital) option's time value behaves fundamentally differently from vanilla options:

#### Black-Scholes Binary Call Price

$$
V_{\text{YES}} = e^{-r\tau} \cdot \Phi(d_2)
$$

where:

$$
d_2 = \frac{\ln(S/K) + (r - \frac{1}{2}\sigma^2)\tau}{\sigma\sqrt{\tau}}
$$

and $\Phi$ is the cumulative standard normal distribution, $\tau = T - t$ is time to expiry.

#### Theta of a Binary Call

$$
\Theta_{\text{binary}} = \frac{\partial V}{\partial t} = e^{-r\tau} \cdot \phi(d_2) \cdot \frac{d_1}{2\tau}
$$

where $\phi$ is the standard normal PDF and $d_1 = d_2 + \sigma\sqrt{\tau}$.

### Key Behaviors

| Moneyness | As $\tau \to 0$ | Time Decay Direction |
|-----------|-----------------|---------------------|
| Deep ITM ($S \gg K$) | $V \to 1$ | Price rises toward 1 -- positive theta for longs |
| ATM ($S \approx K$) | $V$ oscillates wildly | Extreme theta in both directions |
| Deep OTM ($S \ll K$) | $V \to 0$ | Price falls toward 0 -- negative theta for longs |

### Theta Capture Strategy

**Concept**: On ATM binary contracts, the probability that the price stays above strike versus below strike becomes increasingly sensitive to small moves. A market maker can capture theta by:

1. **Selling time value on OTM contracts**: When a strike is clearly OTM with limited time remaining, the remaining probability is mostly "hope value." Selling into this (quoting tight asks on NO tokens) captures the natural decay toward $0.
2. **Buying time value on ITM contracts**: When a strike is clearly ITM, the remaining probability shortfall from $1.00 is mostly "fear value." Buying into this (quoting tight bids on YES tokens) captures the natural rise toward $1.00.

### Spread Dynamics as Expiry Approaches

Optimal spreads should evolve with time to expiry:

$$
\delta^*(\tau) \propto \sigma \sqrt{\tau} \cdot \phi(d_2)
$$

This formula captures three effects:
- $\sqrt{\tau}$: uncertainty decreases with time, narrowing spreads
- $\sigma$: higher volatility warrants wider spreads
- $\phi(d_2)$: the gamma-shaped density -- spreads are widest ATM and narrowest deep ITM/OTM

| Time to Expiry | Moneyness | Recommended Spread |
|---------------|-----------|-------------------|
| > 1 week | ATM | 4-8 cents |
| 1-5 days | ATM | 2-5 cents |
| < 1 day | ATM | 3-10 cents (widen due to gamma risk!) |
| < 1 day | Deep ITM/OTM | 1-2 cents |
| > 1 week | Deep ITM/OTM | 1-3 cents |

> [!warning] Near-Expiry Gamma Explosion
> As $\tau \to 0$ for ATM binary options, delta and gamma become unbounded:
>
> $$\Delta_{\text{binary}} = \frac{\phi(d_2)}{\sigma \sqrt{\tau}} \to \infty$$
>
> This means tiny moves in the underlying cause massive changes in fair value. Market makers must **widen spreads or withdraw** from ATM contracts in the final minutes before expiry. The standard guidance for vanilla 0DTE options (extreme gamma risk) applies even more forcefully to binaries, where the payoff discontinuity at the strike makes hedging near-impossible.

---

## 7. Strategy Comparison

| Strategy | Edge Source | Complexity | Capital Requirement | Risk Profile |
|----------|-----------|------------|-------------------|--------------|
| Probability-based quoting | Informational (options vs. Polymarket) | Low | Moderate | Medium -- model risk |
| Avellaneda-Stoikov | Spread capture + inventory management | Medium | Moderate | Medium -- inventory risk |
| GLFT | Spread capture with bounded positions | Medium-High | Moderate | Lower -- explicit limits |
| Multi-market quoting | Structural relationships + diversification | High | High (capital across strikes) | Lower -- natural hedges |
| Cross-market arbitrage | Pure mispricing exploitation | Medium | Low per trade | Low if model is accurate |
| Theta capture | Time value decay | Low-Medium | Moderate | Medium -- gap risk |

### Recommended Approach: Layered Strategy

The strategies are not mutually exclusive. The recommended production approach layers them:

1. **Foundation**: GLFT-based quoting with options-implied fair values (strategies 1 + 3)
2. **Alpha overlay**: Cross-market arbitrage signal to skew quotes (strategy 5)
3. **Portfolio management**: Multi-market quoting across strikes (strategy 4)
4. **Time-aware adjustments**: Spread dynamics based on time to expiry (strategy 6)
5. **Risk controls**: Inventory-aware skewing from AS framework (strategy 2)

This layered approach is detailed further in [[Inventory-and-Risk-Management]] and [[Capital-Efficiency-and-Edge-Cases]].

---

## Related Notes

- [[Breeden-Litzenberger-Pipeline]] -- Fair value extraction methodology
- [[Vol-Surface-Fitting]] -- SABR/SVI calibration for smooth probability surfaces
- [[Risk-Neutral-vs-Physical-Probabilities]] -- Adjusting for risk premiums
- [[Polymarket-CLOB-Mechanics]] -- Platform order types, fees, tick sizes
- [[Inventory-and-Risk-Management]] -- Hedging, adverse selection, position limits
- [[Capital-Efficiency-and-Edge-Cases]] -- Return metrics, edge cases, risk scenarios
