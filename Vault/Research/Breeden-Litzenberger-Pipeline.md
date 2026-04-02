---
title: Breeden-Litzenberger Pipeline
tags:
  - quantitative-finance
  - options-pricing
  - risk-neutral-density
  - breeden-litzenberger
  - binary-options
  - polymarket
  - market-making
created: 2026-03-31
status: research
related:
  - "[[Vol-Surface-Fitting]]"
  - "[[Risk-Neutral-vs-Physical-Probabilities]]"
---

# Breeden-Litzenberger Pipeline

> **Goal**: Extract risk-neutral probabilities $P^{\mathbb{Q}}(S_T > K)$ from listed options chains to price binary contracts on Polymarket (e.g., "Will NVDA close above \$120 on April 2?").

---

## 1. Mathematical Foundation

### 1.1 The Pricing Identity

Under the risk-neutral measure $\mathbb{Q}$, the price of a European call is the discounted expected payoff:

$$C(K, T) = e^{-rT} \int_0^{\infty} \max(S_T - K, 0) \, q(S_T) \, dS_T$$

where $q(S_T)$ is the **risk-neutral probability density function** (RND) of the terminal stock price. This is **model-free** --- it holds regardless of the stochastic process driving $S_T$.

Since $\max(S_T - K, 0) = 0$ for $S_T < K$, the integral simplifies:

$$C(K, T) = e^{-rT} \int_K^{\infty} (S_T - K) \, q(S_T) \, dS_T$$

### 1.2 First Derivative: CDF Extraction

Differentiate both sides with respect to $K$ using the Leibniz integral rule:

$$\frac{\partial}{\partial K} \left[ e^{rT} C(K,T) \right] = \frac{\partial}{\partial K} \int_K^{\infty} (S_T - K) \, q(S_T) \, dS_T$$

Applying Leibniz:

$$e^{rT} \frac{\partial C}{\partial K} = -(K - K) \cdot q(K) \cdot (-1) + \int_K^{\infty} (-1) \cdot q(S_T) \, dS_T = -\int_K^{\infty} q(S_T) \, dS_T$$

Therefore:

$$\boxed{\frac{\partial C}{\partial K} = -e^{-rT} \, P^{\mathbb{Q}}(S_T > K)}$$

> **This is our key result for binary option pricing.** The fair value of a cash-or-nothing digital call paying \$1 if $S_T > K$ is:
> $$V_{\text{binary}} = e^{-rT} P^{\mathbb{Q}}(S_T > K) = -\frac{\partial C}{\partial K}$$

### 1.3 Second Derivative: The Breeden-Litzenberger Formula

Differentiate once more:

$$e^{rT} \frac{\partial^2 C}{\partial K^2} = -\frac{\partial}{\partial K} \int_K^{\infty} q(S_T) \, dS_T = q(K)$$

By the Fundamental Theorem of Calculus:

$$\boxed{q(K) = e^{rT} \frac{\partial^2 C}{\partial K^2}}$$

This is the **Breeden-Litzenberger (1978) result**: the risk-neutral density at strike $K$ equals the discounted second derivative of call prices with respect to strike. It is completely model-agnostic.

### 1.4 Connection to Butterfly Spreads

The second derivative has a natural financial interpretation. A **butterfly spread** centered at $K$ with wing width $\Delta K$ has payoff:

$$\text{Butterfly}(K, \Delta K) = C(K - \Delta K) - 2C(K) + C(K + \Delta K)$$

From the Taylor expansion:

$$\frac{\partial^2 C}{\partial K^2} \approx \frac{C(K + \Delta K) - 2C(K) + C(K - \Delta K)}{(\Delta K)^2}$$

So the butterfly spread price, normalized by $(\Delta K)^2$, approximates the risk-neutral density (up to discounting). The narrower the butterfly, the better the approximation --- but we can never make infinitely narrow butterflies with discrete strikes.

---

## 2. Numerical Implementation

### 2.1 The Naive Approach (and Why It Fails)

**Direct finite differences on market prices:**

$$\hat{q}(K_i) = e^{rT} \frac{C(K_{i+1}) - 2C(K_i) + C(K_{i-1})}{(\Delta K)^2}$$

where $K_i$ are the discrete listed strikes with spacing $\Delta K$.

**Problems:**
- Raw option prices contain bid-ask noise, microstructure effects, and stale quotes
- Second derivatives **amplify noise** --- small price errors become large density oscillations
- Empirically, ~48% of density estimates come out **negative** when applied naively
- Results are erratic and non-unimodal

### 2.2 The Correct Approach: Smooth in IV Space, Differentiate in Price Space

The standard practical methodology:

```
1. Extract implied volatilities from option prices
2. Smooth/fit the IV smile (see [[Vol-Surface-Fitting]])
3. Reprice on a fine strike grid using Black-Scholes with fitted IVs
4. Compute second derivatives on the smooth price curve
5. Scale by e^{rT} to get the density
```

**Why IV space?** Implied volatility is a much smoother function of strike than price. Fitting a smooth curve in IV space and then converting back to prices via Black-Scholes automatically enforces no-arbitrage constraints within a single expiry slice.

### 2.3 Step-by-Step Pipeline

#### Step 1: Data Ingestion & Cleaning

```python
# Pseudocode for data pipeline
raw_chain = fetch_options_chain(ticker, expiry)

# Filter criteria:
# - Remove strikes with zero open interest
# - Remove strikes with bid = 0 (no real market)
# - Remove strikes where bid-ask spread > X% of mid
# - Use OTM options only: puts for K < F, calls for K > F
# - Convert to implied volatility using Black-Scholes inversion
```

**Why OTM options?** They are more liquid than deep ITM options. Use **put-call parity** to convert everything to a common framework:

$$C(K) - P(K) = e^{-rT}(F - K)$$

where $F = S_0 e^{(r-d)T}$ is the forward price. This means we can compute call prices from put prices (and vice versa) once we know the forward.

**Bid-ask handling:**
- Use **mid-price** as the default: $\text{mid} = (\text{bid} + \text{ask}) / 2$
- For more sophistication, use **micro-prices** weighted by quote sizes
- Flag any strike where the spread exceeds 20% of mid as potentially unreliable

#### Step 2: Implied Volatility Fitting

Choose a parametric or semi-parametric model (see [[Vol-Surface-Fitting]] for full comparison):

| Method | Pros | Cons |
|--------|------|------|
| **SVI** (5 params) | Parsimonious, arbitrage-free constraints available | Breaks down at very short maturities |
| **SABR** (3-4 params) | Theoretically motivated, good for short-dated | Can produce negative densities at low strikes |
| **Smoothing spline** | Flexible, good empirical fit | Requires regularization; can oscillate |
| **Cubic spline** (exact interpolation) | Passes through all points | **Dangerous**: oscillations, negative densities |

> [!warning] Cubic Splines Are Dangerous
> Exact cubic spline interpolation through market IV points can introduce butterfly arbitrage even when the input data is arbitrage-free. The resulting oscillations produce negative probability densities and incorrect digital option prices. Always use **smoothing** splines (with regularization) or parametric models instead.

#### Step 3: Reprice on Fine Grid

Evaluate the fitted IV function $\hat{\sigma}(K)$ on a fine grid of $N$ equally-spaced strikes (e.g., $N = 400$):

$$K_j = K_{\min} + j \cdot \frac{K_{\max} - K_{\min}}{N}, \quad j = 0, 1, \ldots, N$$

Then compute Black-Scholes call prices:

$$\hat{C}(K_j) = \text{BS}_{\text{call}}(S_0, K_j, T, r, d, \hat{\sigma}(K_j))$$

#### Step 4: Finite Differences

Apply the central difference formula on the smooth prices:

$$\hat{q}(K_j) = e^{rT} \frac{\hat{C}(K_{j+1}) - 2\hat{C}(K_j) + \hat{C}(K_{j-1})}{(\Delta K)^2}$$

where $\Delta K = (K_{\max} - K_{\min}) / N$.

#### Step 5: Validation Checks

| Check | Expected | Action if Failed |
|-------|----------|------------------|
| $\hat{q}(K) \geq 0$ for all $K$ | All non-negative | Re-fit with more smoothing |
| $\int \hat{q}(K) \, dK \approx 1$ | Within 1% of 1.0 | Normalize or extend tails |
| $E^{\mathbb{Q}}[S_T] \approx F$ | Within 0.5% of forward | Check data / forward calc |
| Unimodal | Single peak | Investigate data quality |

#### Step 6: Extract the Binary Option Price

$$P^{\mathbb{Q}}(S_T > K^*) = \int_{K^*}^{\infty} \hat{q}(S) \, dS$$

Or equivalently, using the first-derivative result directly:

$$P^{\mathbb{Q}}(S_T > K^*) = -e^{rT} \frac{\partial C}{\partial K}\bigg|_{K=K^*}$$

which can be computed via central differences on the smooth price curve:

$$P^{\mathbb{Q}}(S_T > K^*) \approx -e^{rT} \frac{\hat{C}(K^* + \Delta K) - \hat{C}(K^* - \Delta K)}{2 \Delta K}$$

---

## 3. Tail Handling

### 3.1 The Problem

Options chains have finite strike ranges. The density beyond the last observable strike is unknown but non-zero. For our binary option pricing, if $K^*$ is near the edge of available strikes, tail handling becomes critical.

### 3.2 Generalized Pareto Distribution (GPD) Tails

The standard approach (from the NY Fed methodology):

1. **Extract the central RND** from the Breeden-Litzenberger formula across the range of liquid strikes
2. **Fit GPD tails** to the left and right edges:

$$\text{Left tail: } q_L(x) = \frac{1}{\beta_L}\left(1 + \xi_L \frac{x_L - x}{\beta_L}\right)^{-1/\xi_L - 1}$$

$$\text{Right tail: } q_R(x) = \frac{1}{\beta_R}\left(1 + \xi_R \frac{x - x_R}{\beta_R}\right)^{-1/\xi_R - 1}$$

where $x_L, x_R$ are the left and right attachment points, $\xi$ controls tail heaviness, and $\beta$ is the scale parameter.

3. **Match** the GPD to the empirical density at the attachment points (value and first derivative continuity)
4. **Normalize** the composite density to integrate to 1

### 3.3 Alternative: Log-Normal Tail Extrapolation

A simpler approach: assume the tails follow a log-normal distribution calibrated to match the density and its derivative at the boundary strikes. This is less flexible than GPD but avoids overfitting with sparse tail data.

### 3.4 Practical Guidance for Our Use Case

For binary options on liquid stocks/indices (NVDA, SPY, QQQ):
- Strikes typically cover a wide range (e.g., $\pm 20\%$ from ATM for weekly expiries)
- Our binary strikes are usually within this range (Polymarket contracts are typically within a few percent of current price)
- **Tail handling is rarely the binding constraint** for near-ATM binary pricing
- It matters more for deep OTM contracts or for computing higher moments (skewness, kurtosis)

---

## 4. Put-Call Parity and Data Unification

### 4.1 The Identity

For European options with the same strike and expiry:

$$C(K) - P(K) = S_0 e^{-dT} - K e^{-rT}$$

or equivalently in terms of the forward $F = S_0 e^{(r-d)T}$:

$$C(K) - P(K) = e^{-rT}(F - K)$$

### 4.2 Using Both Calls and Puts

**Standard practice:** Use OTM options for each strike region:
- $K < F$: use **puts** (more liquid, tighter spreads)
- $K > F$: use **calls** (more liquid, tighter spreads)
- $K \approx F$: use the average or the more liquid side

**Conversion:** Any OTM put price can be converted to the equivalent OTM call price via put-call parity, giving a continuous call price function across all strikes.

### 4.3 Implied Forward Extraction

Rather than assuming $r$ and $d$, extract the market-implied forward from put-call parity:

$$\hat{F} = K^* + e^{rT}[C(K^*) - P(K^*)]$$

where $K^*$ is the strike with the smallest $|C - P|$ (closest to ATM). This is more robust than computing $F$ from dividends and rates.

### 4.4 Data Cleaning Checklist

- [ ] Remove strikes with zero bid on both call and put
- [ ] Remove strikes with zero open interest
- [ ] Flag strikes where IV is more than 2 standard deviations from neighbors
- [ ] Check put-call parity: if $|C - P - e^{-rT}(F-K)| > \text{threshold}$, investigate
- [ ] Remove strikes where the bid-ask spread exceeds 50% of theoretical value
- [ ] For 0DTE: be more aggressive with filtering since microstructure noise dominates
- [ ] Detect stale quotes: compare timestamps; discard quotes older than a threshold

---

## 5. Known Pitfalls and Solutions

### 5.1 Noise Amplification

| Source | Impact | Mitigation |
|--------|--------|------------|
| Bid-ask bounce | Creates jagged price function | Use mid-prices or micro-prices |
| Stale quotes | Inconsistent information across strikes | Timestamp filtering; discard old quotes |
| Low open interest | Unreliable prices | Filter by minimum OI threshold |
| Discrete strikes | Cannot resolve fine density features | Interpolate in IV space before differentiating |

### 5.2 Short-Dated Options (0-5 DTE)

These are our primary use case and present unique challenges:

- **Wider bid-ask spreads** relative to option value (theta decay eats premium)
- **Fewer liquid strikes** for very short-dated expirations
- **Gamma/charm effects**: rapid delta decay means dealer hedging activity distorts prices intraday
- **Overnight vs intraday vol**: the market prices overnight gap risk separately from intraday realized vol; for 0DTE, only intraday vol matters, but for 1DTE, overnight risk is embedded

> [!important] Reliability for 0-1 DTE
> Breeden-Litzenberger extraction for 0DTE options is **less reliable** than for longer-dated options because:
> 1. Higher moments are "much more difficult to estimate accurately" with short-maturity options
> 2. Bid-ask noise is proportionally larger relative to shrinking option values
> 3. Fewer strikes pass liquidity filters
> 4. The IV smile curvature is more pronounced (vol-of-vol effect dominates)
>
> **Recommendation**: For 0-1 DTE, use a robust parametric model ([[Vol-Surface-Fitting#SABR Model|SABR]] may outperform [[Vol-Surface-Fitting#SVI Model|SVI]] at very short maturities) and cross-validate with the direct butterfly spread approach.

### 5.3 The Discretization Bias

With a finite strike grid of spacing $\Delta K$, the finite difference approximation has an error of order $O((\Delta K)^2)$. For listed options with $\Delta K = \$1$ or $\$5$, this can be significant. Solutions:

1. Interpolate to a finer grid before differentiating (the IV-smoothing approach)
2. Use the **discrete Breeden-Litzenberger** formula directly:

$$P^{\mathbb{Q}}(K_{i-1} < S_T \leq K_i) \approx e^{rT} \frac{C(K_{i-1}) - 2C(K_i) + C(K_{i+1})}{K_{i+1} - K_{i-1}} \cdot \frac{2}{K_{i+1} - K_{i-1}}$$

---

## 6. Binary Option Pricing and Greeks

### 6.1 Fair Value

The Polymarket contract "S_T > K" has fair value:

$$V = e^{-rT} P^{\mathbb{Q}}(S_T > K)$$

Under Black-Scholes, this equals $e^{-rT} N(d_2)$ where:

$$d_2 = \frac{\ln(S/K) + (r - d - \sigma^2/2)T}{\sigma\sqrt{T}}$$

But we don't need to assume Black-Scholes --- the Breeden-Litzenberger pipeline gives us the model-free probability directly.

### 6.2 Greeks of the Binary Call

Under Black-Scholes for reference (see also [[Vol-Surface-Fitting]] for model-specific formulas):

| Greek | Formula | Behavior Near Expiry |
|-------|---------|---------------------|
| **Delta** | $\frac{e^{-rT} N'(d_2)}{\sigma S \sqrt{T}}$ | Tends to $\infty$ at ATM as $T \to 0$ |
| **Gamma** | Complex; involves $d_1, d_2$ derivatives | Oscillatory; extreme near ATM at expiry |
| **Vega** | $-\frac{e^{-rT} d_1 N'(d_2)}{\sigma}$ | Small for 0DTE (no time for vol to matter) |
| **Theta** | Large and negative near ATM | Dominates; "the clock is the trade" |

### 6.3 Pin Risk

When $S_T \approx K$ near expiry, the binary option's delta becomes extremely large --- theoretically infinite at ATM as $T \to 0$. This is **pin risk**: the option's value oscillates between 0 and 1 with tiny price movements.

**Practical implications for market making:**
- Do not hold unhedged binary positions near expiry when ATM
- Hedge with vanilla call/put spreads (see below)
- Widen bid-ask spreads on the binary as expiry approaches and the underlying is near the strike

### 6.4 Hedging with Vanilla Spreads

A binary call paying \$1 if $S_T > K$ can be approximated by a **call spread**:

$$\text{Binary} \approx \frac{1}{\Delta K} \left[ C(K - \Delta K/2) - C(K + \Delta K/2) \right]$$

This is the **overhedge** technique used by banks:
- Choose an overhedge width (typically 3-8% of the strike level)
- Long $\alpha$ calls at $K_1 = K - \epsilon$
- Short $\alpha$ calls at $K_2 = K$
- Where $\alpha = \text{payoff} / (K_2 - K_1) = 1 / \epsilon$

**Tradeoff:**
- Wider spread ($\epsilon$ larger): smoother Greeks, lower replication cost, but poorer replication of digital payoff
- Narrower spread ($\epsilon$ smaller): better replication, but delta $\approx 1/\epsilon$ becomes unmanageably large near expiry

### 6.5 Do We Need to Hedge?

For a Polymarket market maker, hedging is **optional but advisable** for large positions:

- **Small positions**: the bid-ask spread earned should compensate for directional risk
- **Large positions or concentrated strikes**: hedge with vanilla options on the underlying to bound downside
- **Near expiry**: the binary becomes highly sensitive to small moves; either exit the position or hedge tightly

---

## 7. Expiry Mismatch: When Polymarket Dates Don't Align With Options Expiries

### The Problem

Polymarket markets can resolve on **any calendar day** (e.g., "Will NVDA close above $170 on Monday March 30?"), but listed options typically expire on **Fridays** (weeklies) or the **third Friday** of the month (monthlies). When there is no options expiry matching the Polymarket resolution date, we cannot directly apply B-L extraction.

### Solution 1: Nearest Expiry + Black-Scholes Bridge (Simplest)

Use B-L extraction from the nearest options expiry and evolve the distribution forward/backward to the Polymarket date:

1. Extract the full risk-neutral density $q_{T_1}(x)$ from the nearest options expiry $T_1$
2. Compute the target probability by convolving with a B-S transition kernel for the remaining time:

$$P(S_T > K) = \int_0^{\infty} P(S_T > K \mid S_{T_1} = x) \cdot q_{T_1}(x) \, dx$$

where $P(S_T > K \mid S_{T_1} = x)$ uses Black-Scholes with ATM implied vol for the residual period $T - T_1$.

**When to use:** Polymarket date is within 1-2 trading days of an options expiry.

### Solution 2: Variance-Linear Interpolation Between Bracketing Expiries (Recommended)

Use options chains from the two expiries that bracket the Polymarket date:

$$T_1 < T_{\text{Polymarket}} < T_2$$

Interpolate the **total implied variance** (not volatility) at each strike:

$$w(K, T) = \frac{(T_2 - T) \cdot w(K, T_1) + (T - T_1) \cdot w(K, T_2)}{T_2 - T_1}$$

where $w(K, T) = \sigma^2(K, T) \cdot T$ is the total implied variance. Then compute:

$$P(S_T > K) = \Phi\!\left(\frac{\ln(S/K) + \frac{1}{2} w(K, T)}{{\sqrt{w(K, T)}}}\right)$$

This preserves the skew information from B-L at both bracketing expiries while producing a probability for the exact Polymarket date.

**When to use:** Default approach. Works whenever there are options expiries within ~5 trading days on each side (almost always true for liquid stocks).

### Solution 3: Full Vol Surface (SSVI) Interpolation (Most Rigorous)

Fit a full implied volatility surface across strikes AND expiries using SSVI ([[Vol-Surface-Fitting]]):

1. Extract B-L-calibrated IV smiles from 3+ options expiries
2. Fit SSVI surface parameters ensuring no-arbitrage constraints (butterfly and calendar spread)
3. Read off the IV smile at the exact Polymarket expiry date
4. Compute $P(S > K)$ from the interpolated smile using B-L on the synthetic chain

**When to use:** When high accuracy is needed, multiple expiries are available, and the Polymarket date is far from any options expiry.

### Comparison

| Approach | Accuracy | Complexity | Data Needed |
|----------|----------|------------|-------------|
| Nearest expiry + B-S bridge | Good | Low | 1 options chain |
| Variance-linear interpolation | Better | Medium | 2 options chains |
| Full SSVI surface | Best | High | 3+ options chains |

### Lucky Cases: No Interpolation Needed

- **Weekly Polymarket markets resolving on Friday** — often have an exact options expiry match
- **Monthly markets on third Friday** — align with monthly options expiry
- **SPX/NDX** — have options expiring on Mon, Wed, and Fri (0DTE), so almost any Polymarket date has a match

> [!important] Backtesting Implication
> The backtesting engine must record WHICH interpolation method was used for each fair value computation. When evaluating B-L accuracy ([[Backtesting-Plan]] Phase 1), results should be segmented by interpolation method and expiry distance to understand how much accuracy degrades with interpolation.

---

## 8. Implementation Architecture

```
┌──────────────┐     ┌──────────────┐     ┌───────────────┐
│ Options Chain │────▶│   Data       │────▶│  IV Fitting   │
│   (ThetaData) │     │   Cleaning   │     │  (SABR/SVI)   │
└──────────────┘     └──────────────┘     └───────┬───────┘
                                                   │
                                                   ▼
┌──────────────┐     ┌──────────────┐     ┌───────────────┐
│  Polymarket  │◀────│   Binary     │◀────│  B-L Density  │
│  Fair Value  │     │   P(S>K)     │     │  Extraction   │
└──────────────┘     └──────────────┘     └───────────────┘
```

**Latency considerations:**
- Full pipeline (fetch -> clean -> fit -> extract) should run in < 1 second
- For 0DTE, refresh every 1-5 minutes during market hours
- For 1DTE+, refresh every 5-15 minutes is sufficient

---

## 8. Key References

1. **Breeden, D.T. & Litzenberger, R.H.** (1978). "Prices of State-Contingent Claims Implicit in Option Prices." *Journal of Business*, 51(4), 621-651.
2. **NY Fed Staff Report 677**: "A Simple and Reliable Way to Compute Option-Based Risk-Neutral Distributions." Federal Reserve Bank of New York.
3. **Gatheral, J.** (2006). *The Volatility Surface: A Practitioner's Guide*. Wiley.
4. **Figlewski, S.** (2017). "Risk Neutral Densities: A Review." NYU Stern Working Paper.
5. **Whiteside, B.** (2022). "Butterflies & Probability --- From First Principles." [benjaminwhiteside.com](https://benjaminwhiteside.com/2022/05/04/butterflies/)
6. **Smolski, A.** "Options' Implied Probability: A Dive into Risk-Neutral Densities." [Medium](https://antonismolski.medium.com/options-implied-probability-a-dive-into-risk-neutral-densities-4bef5280842f)
