---
title: Options-Implied Signal Strategies
created: 2026-04-02
status: research
tags:
  - options
  - signals
  - thetadata
  - breeden-litzenberger
  - volatility
  - greeks
  - probability
  - market-making
  - polymarket
  - intraday
sources:
  - https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr677.pdf
  - https://www.federalreserve.gov/econresdata/ifdp/2014/files/ifdp1122.pdf
  - https://www.bis.org/publ/bisp06b.pdf
  - https://public.econ.duke.edu/~boller/Published_Papers/rfs_09.pdf
  - https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1540-6261.2011.01695.x
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1418488
  - https://www.nber.org/system/files/working_papers/w17323/w17323.pdf
  - https://www.sciencedirect.com/science/article/pii/S0304405X20300763
  - https://reasonabledeviations.com/2020/10/10/option-implied-pdfs-2/
  - https://engineering.nyu.edu/sites/default/files/2019-01/CarrReviewofFinStudiesMarch2009-a.pdf
related:
  - "[[Breeden-Litzenberger-Pipeline]]"
  - "[[Vol-Surface-Fitting]]"
  - "[[Risk-Neutral-vs-Physical-Probabilities]]"
  - "[[ThetaData-Options-API]]"
  - "[[ThetaData-Stock-Index-Data]]"
  - "[[Core-Market-Making-Strategies]]"
---

# Options-Implied Signal Strategies for Polymarket Binary Market Making

> **Goal**: Go beyond the basic [[Breeden-Litzenberger-Pipeline]] and [[Vol-Surface-Fitting]] to develop a suite of tradable signals derived from options market microstructure, vol surface dynamics, and Greeks --- signals that predict when Polymarket binary prices are about to reprice and in which direction.

---

## Overview

The basic pipeline is: options chain $\to$ IV surface fit (SVI/SABR) $\to$ smooth $C(K)$ $\to$ Breeden-Litzenberger $\to$ $P^{\mathbb{Q}}(S_T > K)$. This note catalogs **seven signal families** that exploit the richness of the options data available via ThetaData's Standard tier ($80/mo) to generate alpha over naive EOD-only probability extraction.

Each signal includes: mathematical formulation, specific ThetaData endpoints and parameters, Polymarket data requirements, expected information content, and concrete backtesting specifications.

---

## 1. Intraday B-L Recalibration Frequency

### 1.1 The Core Question

How often should we refit the IV surface and recompute $P^{\mathbb{Q}}(S_T > K)$? More frequent recalibration captures information faster but incurs computation cost and noise. The optimal frequency depends on the **information arrival rate** of the options market relative to the **repricing latency** of Polymarket.

### 1.2 Information Content Framework

Define the **signal innovation** at recalibration interval $\Delta t$:

$$\Delta p_{\Delta t}(t) = P^{\mathbb{Q}}_{t}(S_T > K) - P^{\mathbb{Q}}_{t - \Delta t}(S_T > K)$$

The **information ratio** of recalibration at interval $\Delta t$ is:

$$IR(\Delta t) = \frac{E[|\Delta p_{\Delta t}|]}{\text{ComputeCost}(\Delta t)}$$

We want to find $\Delta t^*$ that maximizes $IR(\Delta t)$ subject to $\Delta p_{\Delta t}$ being statistically distinguishable from fitting noise.

### 1.3 Signal Quality Decomposition

Decompose the variance of the probability estimate at each recalibration:

$$\text{Var}[\hat{P}^{\mathbb{Q}}_t] = \underbrace{\text{Var}[\text{true } P^{\mathbb{Q}}_t]}_{\text{signal}} + \underbrace{\text{Var}[\epsilon_{\text{fit},t}]}_{\text{fitting noise}} + \underbrace{\text{Var}[\epsilon_{\text{bid-ask},t}]}_{\text{microstructure noise}}$$

At higher frequencies:
- **Signal variance increases** (more information captured per unit time)
- **Fitting noise stays constant** (same SVI/SABR methodology)
- **Microstructure noise increases** (wider effective spreads, stale quotes in illiquid strikes)

The **signal-to-noise ratio** (SNR) therefore has a non-monotonic relationship with $\Delta t$:

$$\text{SNR}(\Delta t) = \frac{\text{Var}[\Delta P^{\mathbb{Q}}_{\text{true}}(\Delta t)]}{\text{Var}[\epsilon_{\text{fit}}] + \text{Var}[\epsilon_{\mu\text{-structure}}(\Delta t)]}$$

### 1.4 ThetaData Implementation

**Primary endpoint**: `GET /v3/option/history/greeks/implied_volatility`

```
# 5-minute IV snapshots for all strikes of a given expiry
http://127.0.0.1:25503/v3/option/history/greeks/implied_volatility
  ?symbol=NVDA
  &expiration=20260403
  &strike=*
  &right=both
  &date=20260402
  &interval=5m
  &format=json
```

**Response fields used**: `bid_implied_vol`, `implied_vol`, `ask_implied_vol`, `underlying_price`, `timestamp`

**Tier requirement**: Options Standard ($80/mo) provides tick-level historical data back to 2016, with 2 concurrent server threads. The `interval` parameter supports: `tick`, `1s`, `5s`, `10s`, `15s`, `30s`, `1m`, `5m`, `10m`, `15m`, `30m`, `1h`.

**Comparison intervals to test**:

| Interval | Snapshots/Day | Data Points (20 strikes x 2 sides) | Compute Budget |
|----------|--------------|--------------------------------------|----------------|
| EOD only | 1 | 40 | Negligible |
| 30m | 13 | 520 | Low |
| 5m | 78 | 3,120 | Medium |
| 1m | 390 | 15,600 | High |
| tick | ~23,400 | ~936,000 | Extreme |

### 1.5 Polymarket Comparison Data

**Endpoint**: `GET https://clob.polymarket.com/prices-history`

```
GET /prices-history?market={asset_id}&interval=1h&fidelity=1
```

- `fidelity` parameter controls granularity in minutes (default: 1 minute)
- `interval` options: `1h`, `6h`, `1d`, `1w`, `1m`, `max`, `all`
- Response: array of `{t: unix_timestamp, p: float}` price snapshots

For backtesting: pull 1-minute fidelity Polymarket prices and compare to options-implied probabilities at each recalibration interval.

### 1.6 Expected Findings and Hypothesis

**Hypothesis**: 5-minute recalibration is optimal for our use case.

**Reasoning**:
- **EOD-only** misses all intraday information. Polymarket prices move intraday, so we arrive late to every repricing.
- **1-minute** captures noise from stale OTM quotes (many OTM options don't trade for minutes at a time; the NBBO can be stale or wide). The SVI fit oscillates.
- **5-minute** allows enough time for NBBO quotes to refresh across the chain while capturing the $\sim$15--30 minute lead time of options over Polymarket.
- **Tick-level** is computationally prohibitive (fitting SVI 23k times/day) and the marginal information over 1m is minimal for our purpose.

**Key metric**: Measure the **lead-lag correlation** $\rho(\Delta p^{\text{options}}_t, \Delta p^{\text{PM}}_{t+\tau})$ for each recalibration interval and optimize over $\tau$.

---

## 2. Vol Surface Dynamics as Signals

### 2.1 Intuition

The IV surface is not static. Its **shape changes** encode directional and uncertainty information that often leads Polymarket repricing. Three canonical shape metrics:

1. **25-delta risk reversal (skew)**: directional sentiment
2. **25-delta butterfly (curvature)**: tail risk pricing
3. **ATM implied vol level**: overall uncertainty

### 2.2 Mathematical Definitions

Let $\sigma(K, T)$ be the Black-Scholes implied volatility at strike $K$ and expiry $T$. Define:

**25-Delta Risk Reversal (Skew)**:

$$RR_{25} = \sigma_{\text{call}}^{25\Delta} - \sigma_{\text{put}}^{25\Delta}$$

where $\sigma_{\text{call}}^{25\Delta}$ is the IV of the call with $\Delta = 0.25$ and $\sigma_{\text{put}}^{25\Delta}$ is the IV of the put with $\Delta = -0.25$. More negative $RR_{25}$ means the market prices downside more expensively --- bearish skew.

**25-Delta Butterfly (Curvature)**:

$$BF_{25} = \frac{\sigma_{\text{call}}^{25\Delta} + \sigma_{\text{put}}^{25\Delta}}{2} - \sigma_{\text{ATM}}$$

Higher butterfly means fatter tails relative to ATM --- the market expects more extreme moves.

**ATM Implied Vol** ($\sigma_{\text{ATM}}$): The IV of the at-the-money option, typically the strike closest to the forward price $F = S \cdot e^{(r-q)T}$.

### 2.3 Signal Dynamics and Polymarket Repricing

**Skew steepening** ($\Delta RR_{25} < 0$, i.e., puts getting more expensive relative to calls):

$$\Delta RR_{25,t} \ll 0 \implies P^{\mathbb{Q}}(S_T < K_{\text{low}}) \uparrow \implies \text{Polymarket "above } K_{\text{low}}\text{" should trade down}$$

The mechanism: if 25-delta put IV rises from 35% to 40% while 25-delta call IV stays at 30%, the left tail of the risk-neutral distribution is getting fatter. The CDF shifts: $P^{\mathbb{Q}}(S_T > K)$ decreases for all $K$ below the forward, and especially for $K$ near the put strike.

**ATM vol shift** ($\Delta \sigma_{\text{ATM}} > 0$):

A uniform vol increase spreads the distribution. For a strike $K$ near ATM:

$$\frac{\partial P^{\mathbb{Q}}(S_T > K)}{\partial \sigma} \approx -\phi(d_2) \cdot \sqrt{T}$$

where $\phi$ is the standard normal PDF and $d_2 = \frac{\ln(F/K) - \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}$. For $K > F$ (OTM calls), a vol increase **raises** $P^{\mathbb{Q}}(S_T > K)$. For $K < F$, it **lowers** $P^{\mathbb{Q}}(S_T > K)$. For $K \approx F$, the effect is small (second order).

**Butterfly expansion** ($\Delta BF_{25} > 0$):

Fatter tails increase the probability of extreme outcomes in both directions. If NVDA butterfly widens by 2 vol points intraday, both "NVDA > \$130" and "NVDA < \$110" become more likely (assuming $F \approx \$120$).

### 2.4 ThetaData Implementation

Use the IV endpoint to construct intraday skew time series:

```
# Get full chain IV at 5-min intervals
GET /v3/option/history/greeks/implied_volatility
  ?symbol=NVDA&expiration=20260403&strike=*&right=both
  &date=20260402&interval=5m
```

**Processing pipeline**:

1. At each 5-min snapshot, collect `{strike, right, bid_implied_vol, ask_implied_vol, implied_vol}`
2. Compute midpoint IV: $\sigma_{\text{mid}}(K) = \frac{\sigma_{\text{bid}}(K) + \sigma_{\text{ask}}(K)}{2}$
3. Use delta from first-order Greeks (or compute from IV + underlying_price) to identify 25-delta strikes
4. Compute $RR_{25}$, $BF_{25}$, $\sigma_{\text{ATM}}$
5. Track $\Delta RR_{25}$, $\Delta BF_{25}$, $\Delta \sigma_{\text{ATM}}$ as rolling differences

**Alternative: use the All Greeks endpoint for delta**:

```
# First-order Greeks including delta at each strike --- requires VALUE+ tier
GET /v3/option/history/greeks/all
  ?symbol=NVDA&expiration=20260403&strike=*&right=both
  &date=20260402&interval=5m
```

Note: `/v3/option/history/greeks/all` requires **Pro** tier. For Standard tier, compute delta from IV using Black-Scholes: $\Delta_{\text{call}} = e^{-qT} \Phi(d_1)$.

### 2.5 Signal Construction: Z-Score Approach

Normalize shape changes against their own recent history:

$$z_{\text{skew},t} = \frac{RR_{25,t} - \bar{RR}_{25,\text{20d}}}{\hat{\sigma}_{RR,\text{20d}}}$$

A $z_{\text{skew}} < -2$ signals an unusually aggressive put-buying event. Compare to Polymarket mid-price movement over the next 30--120 minutes.

### 2.6 Testable Predictions

| Signal | Direction | Polymarket Impact | Expected Lead Time |
|--------|-----------|-------------------|--------------------|
| $\Delta RR_{25} < -2\sigma$ | Skew steepening | Below-strike contracts up | 15--60 min |
| $\Delta RR_{25} > +2\sigma$ | Skew flattening | Above-strike contracts up | 15--60 min |
| $\Delta \sigma_{\text{ATM}} > +1\sigma$ | Vol expansion | OTM contracts reprice | 5--30 min |
| $\Delta BF_{25} > +1\sigma$ | Tail fattening | Far OTM contracts up | 30--120 min |

---

## 3. Term Structure Signals

### 3.1 Forward Volatility Extraction

When Polymarket contracts have varying expiry dates, the **term structure of implied vol** contains information about how probability dynamics evolve over time.

Given implied variances at two expiries $T_1 < T_2$, the **forward variance** from $T_1$ to $T_2$ is:

$$\sigma^2_{T_1 \to T_2} = \frac{\sigma^2(T_2) \cdot T_2 - \sigma^2(T_1) \cdot T_1}{T_2 - T_1}$$

This must be non-negative (calendar spread arbitrage constraint). When forward vol is **unusually high** relative to spot vol, the market expects a volatility event between $T_1$ and $T_2$.

### 3.2 Variance-Linear Interpolation for Bracketing Expiries

Polymarket contracts often expire on dates between listed options expiries. If the contract expires at $T_c$ and the nearest listed expiries are $T_1 < T_c < T_2$:

**Step 1**: Extract the risk-neutral CDF at each expiry: $F_1(K) = P^{\mathbb{Q}}_{T_1}(S_{T_1} > K)$ and $F_2(K) = P^{\mathbb{Q}}_{T_2}(S_{T_2} > K)$.

**Step 2**: Interpolate in variance space. The total variance at $T_c$ for each strike $K$ is:

$$w(K, T_c) = w(K, T_1) + \frac{T_c - T_1}{T_2 - T_1} \left[ w(K, T_2) - w(K, T_1) \right]$$

where $w(K, T) = \sigma^2(K, T) \cdot T$ is the total implied variance.

**Step 3**: Convert back to IV: $\sigma(K, T_c) = \sqrt{w(K, T_c) / T_c}$ and run the B-L pipeline on the interpolated surface.

> **Why variance-linear?** Under diffusion models, total variance is additive across non-overlapping time intervals. Interpolating linearly in variance space is consistent with this. Interpolating linearly in vol space is not.

### 3.3 Term Structure Slope as Signal

Define the **term structure slope**:

$$\text{TS}_{\text{slope}} = \frac{\sigma_{\text{ATM}}(T_2) - \sigma_{\text{ATM}}(T_1)}{T_2 - T_1}$$

| Regime | Interpretation | Signal |
|--------|---------------|--------|
| $\text{TS}_{\text{slope}} > 0$ (contango) | Normal --- longer-dated vol higher | Probabilities evolving smoothly |
| $\text{TS}_{\text{slope}} < 0$ (backwardation) | Near-term event priced in | Short-dated Polymarket contracts should show elevated uncertainty |
| $\text{TS}_{\text{slope}}$ flipping sign | Regime change | Refit all maturities immediately |

### 3.4 Calendar Spread Signal for Event Detection

A **calendar spread** in IV (short near, long far) widens when the market expects a volatility event before the near expiry. For earnings or macro announcements:

$$CS_{\sigma} = \sigma(T_{\text{far}}) - \sigma(T_{\text{near}})$$

When $CS_{\sigma}$ collapses (near-term vol spikes above far-term), the options market is pricing a specific near-term event. This should be reflected in Polymarket contracts expiring on or near that event date.

### 3.5 ThetaData Implementation

Fetch multiple expiry chains in parallel:

```
# Near expiry
GET /v3/option/history/greeks/implied_volatility
  ?symbol=NVDA&expiration=20260403&strike=*&right=both&date=20260402&interval=5m

# Next expiry
GET /v3/option/history/greeks/implied_volatility
  ?symbol=NVDA&expiration=20260410&strike=*&right=both&date=20260402&interval=5m

# Far expiry
GET /v3/option/history/greeks/implied_volatility
  ?symbol=NVDA&expiration=20260417&strike=*&right=both&date=20260402&interval=5m
```

Standard tier allows 2 concurrent threads, so batch 2 expiries at a time. Multi-day range requests limited to 1 month.

### 3.6 SPX/SPY Term Structure for Index Contracts

SPX has the richest expiry set: Mon/Wed/Fri 0DTE expirations plus monthly and quarterly. This gives near-continuous term structure data. For Polymarket index-level contracts (e.g., "Will S&P 500 close above 5,000?"), the SPX term structure is directly applicable.

---

## 4. Greeks-Based Signals

### 4.1 Dealer Gamma Exposure (GEX)

**Concept**: Market makers who sell options are systematically short gamma. Their hedging activity creates **mean-reversion** near high-gamma strikes and **momentum** away from them. If NVDA has massive open interest at the $120 strike, dealer gamma hedging pins the stock near $120 --- affecting $P(S_T > 120)$.

**GEX Calculation**:

$$\text{GEX}(K) = \Gamma(K) \cdot OI(K) \cdot 100 \cdot S$$

where $\Gamma(K)$ is the per-contract gamma at strike $K$, $OI(K)$ is open interest, and $S$ is the spot price. The factor of 100 accounts for the option multiplier.

**Aggregate GEX**:

$$\text{GEX}_{\text{net}} = \sum_{K} \left[ \text{GEX}_{\text{call}}(K) \cdot \text{sign}_{\text{call}} - \text{GEX}_{\text{put}}(K) \cdot \text{sign}_{\text{put}} \right]$$

Convention: assume dealers are net short calls and net long puts (from selling puts to hedgers). So $\text{sign}_{\text{call}} = -1$ and $\text{sign}_{\text{put}} = +1$. When $\text{GEX}_{\text{net}} > 0$ (positive gamma), dealers buy dips and sell rallies --- price pins. When $\text{GEX}_{\text{net}} < 0$ (negative gamma), dealers amplify moves --- momentum.

### 4.2 Strike-Level Gamma Pinning and Binary Probability

If gamma exposure is concentrated at strike $K^*$, the stock tends to pin near $K^*$ into expiry. This affects binary probabilities:

$$P^{\text{GEX-adjusted}}(S_T > K) \approx \begin{cases} \text{elevated vs B-L} & \text{if } K < K^* \text{ (stock pinned above)} \\ \text{depressed vs B-L} & \text{if } K > K^* \text{ (stock pinned below)} \\ \approx 0.5 & \text{if } K = K^* \text{ (max uncertainty at pin)} \end{cases}$$

### 4.3 Charm: Time-Decay-Driven Probability Drift

**Charm** (delta decay): $\frac{\partial \Delta}{\partial t} = -\frac{\partial^2 C}{\partial S \partial t}$

As expiry approaches, delta becomes more binary (approaching 0 or 1). Charm measures how fast this happens. For a slightly OTM call ($K$ just above $S$):

$$\text{Charm} = -\frac{e^{-qT}\phi(d_1)\left[2(r-q)T - d_2\sigma\sqrt{T}\right]}{2T\sigma\sqrt{T}}$$

**Signal**: When charm is large and negative for calls near a Polymarket strike, delta is collapsing quickly toward zero. The B-L probability at that strike is decaying faster than linear --- the Polymarket price should be dropping faster than a naive time-weighted interpolation suggests.

### 4.4 Vanna: Vol-Spot Correlation Signal

**Vanna**: $\frac{\partial \Delta}{\partial \sigma} = \frac{\partial \text{Vega}}{\partial S}$

When vanna is large at a strike $K$:
- If vol rises and vanna > 0, delta increases $\to$ the probability $P(S_T > K)$ increases as perceived by hedging flows
- Vanna exposure drives **correlated movement** between vol and spot, creating feedback loops

**Vanna signal for binary pricing**: If aggregate vanna is negative (dealers short vanna), a vol spike drives spot down, which raises vol further. This is the "vol-down" spiral that accelerates probability repricing for below-strike contracts.

### 4.5 ThetaData Implementation

**Second-order Greeks (gamma, vanna, charm)** are available at **Standard** tier via:

```
# Second-order Greeks --- Standard tier
GET /v3/option/history/greeks/second_order  # Not available; use trade greeks
```

Actually, per ThetaData subscription docs:
- **Greeks 2nd Order** (gamma, vanna, charm, vomma, veta, vera): available at **Standard** and **Pro** tiers
- **Trade Greeks 2nd Order**: also Standard and Pro

Use the history endpoint for backtesting:

```
# EOD Greeks with all orders (including gamma, vanna, charm) --- no tier restriction on EOD
GET /v3/option/history/greeks/eod
  ?symbol=NVDA&expiration=*&start_date=20260401&end_date=20260401
```

The EOD endpoint returns: `delta`, `gamma`, `theta`, `vega`, `rho`, `vanna`, `charm`, `vomma`, `veta`, `vera`, `speed`, `zomma`, `color`, `ultima`, `lambda`, `epsilon`, plus `volume` and `open_interest`.

For **intraday** second-order Greeks, the `/v3/option/history/greeks/all` endpoint (Pro tier) returns all Greeks at arbitrary intervals. At Standard tier, compute gamma and vanna from the IV endpoint:

$$\Gamma = \frac{e^{-qT}\phi(d_1)}{S\sigma\sqrt{T}}, \quad \text{Vanna} = -e^{-qT}\phi(d_1)\frac{d_2}{\sigma}$$

### 4.6 Open Interest Data for GEX

```
# Daily open interest for all NVDA options
GET /v3/option/history/open_interest
  ?symbol=NVDA&expiration=*&date=20260402&format=json
```

OI is reported once per day by OPRA at approximately 06:30 ET. It represents end-of-previous-day OI. Combine with EOD Greeks for daily GEX maps; use intraday volume (from trade endpoint) as a proxy for intraday OI changes.

---

## 5. Risk-Neutral to Physical Probability Bridges

### 5.1 Why This Matters

The [[Breeden-Litzenberger-Pipeline]] extracts $P^{\mathbb{Q}}(S_T > K)$ --- the risk-neutral probability. Polymarket contracts settle on **actual outcomes**, which follow the physical measure $\mathbb{P}$. The systematic gap between $\mathbb{Q}$ and $\mathbb{P}$ is our **edge** if we can estimate it.

See [[Risk-Neutral-vs-Physical-Probabilities]] for the foundational treatment. This section focuses on **estimation methods**.

### 5.2 Variance Risk Premium (VRP) Adjustment

**Reference**: Bollerslev, Tauchen & Zhou (2009), "Expected Stock Returns and Variance Risk Premia", *Review of Financial Studies*.

The variance risk premium is:

$$VRP_t = E^{\mathbb{Q}}_t[\sigma^2_{t \to T}] - E^{\mathbb{P}}_t[\sigma^2_{t \to T}] = IV^2_t - RV^2_{t,\text{forecast}}$$

where $IV^2_t$ is the model-free implied variance (VIX-style calculation) and $RV^2_{t,\text{forecast}}$ is a forecast of realized variance (e.g., HAR-RV model).

**Adjustment to probabilities**: The VRP inflates the risk-neutral distribution's tails. A first-order correction:

$$P^{\mathbb{P}}(S_T > K) \approx P^{\mathbb{Q}}(S_T > K) + \alpha \cdot \text{VRP}_t \cdot \frac{\partial P^{\mathbb{Q}}}{\partial \sigma^2}$$

where $\alpha$ is calibrated from historical data (see Section 5.6). Empirically, VRP is positive on average (options are systematically overpriced), so:
- For $K > F$: $P^{\mathbb{P}}(S_T > K) < P^{\mathbb{Q}}(S_T > K)$ --- options overstate upside tail
- For $K < F$: $P^{\mathbb{P}}(S_T > K) > P^{\mathbb{Q}}(S_T > K)$ --- options overstate downside tail

### 5.3 Tail Risk Premium (Bollerslev & Todorov, 2011)

**Reference**: "Tails, Fears, and Risk Premia", *Journal of Finance*.

The VRP decomposes into continuous and jump components:

$$VRP = \underbrace{VRP_{\text{diffusive}}}_{\text{~25\% of total}} + \underbrace{VRP_{\text{jump}}}_{\text{~75\% of total}}$$

The jump component is estimated from the difference between risk-neutral tail expectations (from deep OTM options) and physical tail expectations (from high-frequency return data):

$$\text{Tail Risk Premium} = E^{\mathbb{Q}}[\text{jump variation}] - E^{\mathbb{P}}[\text{jump variation}]$$

**Implication**: Most of the $\mathbb{Q}$-$\mathbb{P}$ gap is in the **tails**. For Polymarket contracts with strikes near ATM, the VRP adjustment is small. For far OTM strikes (e.g., "Will NVDA drop below $100?" when trading at $120), the adjustment is large and economically significant.

### 5.4 Ross Recovery Theorem

**Reference**: Ross (2015), "The Recovery Theorem", *Journal of Finance*.

The theorem states that under a discrete, irreducible, time-homogeneous Markov chain for the state-price density, one can uniquely recover $\mathbb{P}$ from $\mathbb{Q}$ using the Perron-Frobenius theorem.

**Implementation sketch**:

1. Discretize the state space into $N$ price buckets $(S_1, \ldots, S_N)$
2. Estimate the **transition state price matrix** $A_{ij}$ from options at multiple expiries:
   $$A_{ij} = e^{-r\Delta t} \cdot q^{\mathbb{Q}}(S_j | S_i) \cdot \Delta S$$
3. Find the Perron-Frobenius eigenvector $\pi$ of $A$
4. Recover the pricing kernel: $M(S_j | S_i) \propto \pi_j / \pi_i$
5. Extract physical transition probabilities: $p^{\mathbb{P}}(S_j | S_i) = A_{ij} / (e^{-r\Delta t} \cdot M(S_j | S_i))$

**Empirical verdict**: Jackwerth & Menner (2020) test this empirically on S&P 500 options and find that **recovered probabilities do not outperform risk-neutral probabilities** for forecasting returns. The transition state price matrix is unstable and sensitive to interpolation choices.

**Recommendation**: Use as a **robustness check** rather than primary signal. If Ross-recovered $\mathbb{P}$ and VRP-adjusted $\mathbb{P}$ agree, higher confidence in the adjustment.

### 5.5 Jackwerth (2000) Nonparametric Method

**Reference**: Jackwerth (2000), "Recovering Risk Aversion from Option Prices and Realized Returns", *Review of Financial Studies*.

Compare the **historical return distribution** to the **risk-neutral distribution** to back out the implied pricing kernel:

$$M(S_T) = \frac{q^{\mathbb{Q}}(S_T)}{p^{\mathbb{P}}(S_T)}$$

where $p^{\mathbb{P}}(S_T)$ is estimated from a kernel density estimator on historical returns. Then for any new risk-neutral density, apply the estimated pricing kernel in reverse:

$$\hat{p}^{\mathbb{P}}(S_T) = \frac{q^{\mathbb{Q}}(S_T)}{\hat{M}(S_T)}$$

**Advantage**: Nonparametric --- no assumptions about preferences or dynamics.

**Disadvantage**: Requires a long return history to estimate $p^{\mathbb{P}}(S_T)$ reliably. The pricing kernel estimate is noisy, especially in the tails. The method assumes a time-invariant pricing kernel, which is empirically questionable.

### 5.6 Historical Calibration Protocol

The most practical approach is **empirical calibration** of the $\mathbb{Q}$-to-$\mathbb{P}$ mapping:

1. **Collect**: For every past options expiry, record $P^{\mathbb{Q}}(S_T > K)$ from the B-L pipeline at various $K$ values
2. **Observe**: Record whether $S_T > K$ actually occurred (binary outcome)
3. **Calibrate**: Fit a **logistic regression** (or isotonic regression for non-parametric calibration):

$$P^{\mathbb{P}}(S_T > K) = \text{logit}^{-1}\left(\beta_0 + \beta_1 \cdot \text{logit}(P^{\mathbb{Q}}) + \beta_2 \cdot \text{moneyness} + \beta_3 \cdot \text{DTE} + \beta_4 \cdot \text{VRP}\right)$$

4. **Validate**: Out-of-sample Brier score comparison: calibrated vs. raw $P^{\mathbb{Q}}$

With ThetaData's history back to 2016, we have approximately:
- ~2,500 trading days
- ~20 liquid strikes per day per symbol
- ~50,000 strike-level observations per symbol
- Across 10 liquid underlyings: **500,000+ calibration data points**

This is more than sufficient for robust calibration.

---

## 6. Open Interest and Volume Signals

### 6.1 Unusual Options Activity (UOA)

**Definition**: A trade or series of trades where the volume at a specific strike/expiry significantly exceeds recent average volume and/or open interest.

$$\text{UOA}(K, T) = \frac{V_t(K, T) - \bar{V}_{20d}(K, T)}{\hat{\sigma}_{V,20d}(K, T)}$$

where $V_t$ is today's volume and $\bar{V}_{20d}$ is the 20-day average. A UOA z-score > 3 warrants attention.

### 6.2 OI-Weighted Probability Estimates

Standard B-L weights all strikes equally (by the smoothness of the SVI fit). An alternative: weight the probability estimate by **open interest and volume**, giving more influence to strikes where the market has expressed strong views:

$$\hat{P}^{\mathbb{Q}}_{\text{OI-weighted}}(S_T > K) = \frac{\sum_{i} OI(K_i) \cdot \hat{P}^{\mathbb{Q}}_i(S_T > K_i)}{\sum_{i} OI(K_i)}$$

This is not standard B-L but provides a useful cross-check: if the OI-weighted estimate diverges from the smooth B-L estimate, it signals that informed flow is concentrated at specific strikes.

### 6.3 Put-Call Volume Ratio

$$PCR_t = \frac{\sum_K V_{\text{put}}(K, t)}{\sum_K V_{\text{call}}(K, t)}$$

**Signal interpretation**:
- $PCR > 1.5$: heavy put buying, bearish signal $\to$ below-strike Polymarket contracts should be bid up
- $PCR < 0.5$: heavy call buying, bullish signal $\to$ above-strike contracts should be bid up
- Contrarian view: extreme PCR can signal capitulation (everyone already hedged)

### 6.4 ThetaData Implementation

**Intraday volume**: Use the trade history endpoint aggregated at intervals:

```
# Option trades for volume analysis
GET /v3/option/history/trade
  ?symbol=NVDA&expiration=20260403&strike=*&right=both
  &date=20260402&interval=5m
```

**Open interest**: Daily only (OPRA reporting):

```
GET /v3/option/history/open_interest
  ?symbol=NVDA&expiration=*&date=20260402
```

### 6.5 Combining UOA with B-L Divergence

The highest-conviction signal occurs when:

1. UOA fires at a specific strike $K^*$ (z-score > 3)
2. The B-L probability shifts in the direction of the flow
3. The Polymarket price has **not yet moved**

$$\text{Signal}_{\text{combined}} = \text{UOA}(K^*) \times \text{sign}(\Delta P^{\mathbb{Q}}) \times (P^{\mathbb{Q}} - P^{\text{PM}}_{\text{mid}})$$

When this composite signal is large, we should aggressively quote on Polymarket in the direction of the options flow.

---

## 7. 0DTE and Short-Dated Signals

### 7.1 Why 0DTE Matters

Zero-days-to-expiry options are the purest expression of **today's probability distribution**. No term structure ambiguity, no interpolation needed. For Polymarket contracts expiring at market close today, 0DTE options are the perfect hedge instrument and the most informative signal source.

**SPX 0DTE availability**: Monday, Wednesday, Friday expiries (plus SPXW for Tue/Thu). Since late 2022, 0DTE SPX volume often exceeds 50% of total SPX options volume.

**Single-stock 0DTE**: Limited availability. NVDA, AAPL, TSLA, AMZN, META have weekly Friday expiries. Check ThetaData's expiration list endpoint for exact availability.

### 7.2 0DTE Gamma as Pure Probability Signal

For a 0DTE option with $T \to 0$:

$$\Gamma_{0DTE} = \frac{\phi(d_1)}{S\sigma\sqrt{T}} \to \infty \text{ as } T \to 0$$

Gamma explodes near ATM strikes as expiry approaches. This means:

1. **Massive dealer hedging flows** near ATM strikes of 0DTE options
2. **Price pinning** intensifies --- the stock is "glued" to the highest-gamma strike
3. The **realized probability distribution** becomes concentrated around the pin strike

### 7.3 Intraday Vol Surface for 0DTE

The 0DTE IV surface behaves qualitatively differently from longer-dated surfaces:

- **ATM vol decreases** through the day as uncertainty resolves (absent new information)
- **Skew steepens** as the distribution narrows and tail probabilities collapse
- **Vol of vol** (how much the surface moves) is very high --- the surface reshapes every few minutes

**Signal**: Track the 0DTE ATM vol trajectory. If it deviates from the typical intraday decay pattern, information is arriving:

$$\sigma_{\text{ATM}}^{0DTE}(t) = \sigma_0 \cdot \sqrt{\frac{T_{\text{close}} - t}{T_{\text{close}} - t_{\text{open}}}} + \epsilon_t$$

When $\epsilon_t > 2\sigma_\epsilon$, new information is being priced --- update Polymarket quotes immediately.

### 7.4 ThetaData 0DTE Specifics

The `version` parameter in the Greeks endpoints matters for 0DTE:

- `version=latest` (default): uses **real time-to-expiry** (recommended for 0DTE)
- `version=1`: uses a fixed 0.15 DTE floor, which distorts 0DTE Greeks

```
# 0DTE IV surface, 1-minute intervals, real TTE
GET /v3/option/history/greeks/implied_volatility
  ?symbol=SPX&expiration=20260403&strike=*&right=both
  &date=20260403&interval=1m&version=latest
```

For 0DTE, 1-minute intervals are justified because:
1. The options are highly liquid (tight spreads, fresh quotes)
2. Information content is high (every minute is ~0.15% of remaining time)
3. Polymarket repricing is fast for same-day contracts

### 7.5 Cross-Asset 0DTE Signal

SPX 0DTE options can signal for individual stock Polymarket contracts through **beta-adjusted** probability updates:

$$\Delta P^{\mathbb{Q}}(NVDA_T > K) \approx \beta_{NVDA} \cdot \frac{S_{NVDA}}{S_{SPX}} \cdot \Delta P^{\mathbb{Q}}(SPX_T > K_{SPX,\text{equiv}})$$

where $\beta_{NVDA}$ is NVDA's beta to SPX and $K_{SPX,\text{equiv}}$ is the SPX strike corresponding to the same percentile move. This is crude but provides a **faster signal** than waiting for NVDA's own options chain to update (SPX 0DTE options are more liquid and update faster).

---

## 8. Data Pipeline Architecture

### 8.1 High-Level Flow

```
                    +-----------------+
                    | ThetaData       |
                    | Terminal (local) |
                    +--------+--------+
                             |
              +--------------+--------------+
              |              |              |
         IV Endpoint    Greeks EOD    OI Endpoint
         (5m interval)  (daily)      (daily)
              |              |              |
              v              v              v
        +-----+-----+ +-----+-----+ +-----+-----+
        | IV Surface | | GEX Map   | | OI/Volume |
        | Fitter     | | Builder   | | Analyzer  |
        | (SVI/SABR) | |           | |           |
        +-----+------+ +-----+-----+ +-----+-----+
              |              |              |
              v              v              v
        +-----+------+ +----+------+ +-----+-----+
        | B-L Prob   | | Pin/Drift | | UOA       |
        | Extractor  | | Signals   | | Detector  |
        +-----+------+ +-----+-----+ +-----+-----+
              |              |              |
              +--------------+--------------+
                             |
                             v
                    +--------+--------+
                    | Signal          |
                    | Aggregator      |
                    | (z-scores,      |
                    |  composites)    |
                    +--------+--------+
                             |
              +--------------+--------------+
              |                             |
              v                             v
        +-----+------+              +------+------+
        | Fair Value |              | Polymarket  |
        | Calculator |              | Price Feed  |
        | P_Q, P_P   |              | (1m fidelity)|
        +-----+------+              +------+------+
              |                             |
              +-------------+---------------+
                            |
                            v
                   +--------+--------+
                   | Quote Engine    |
                   | (spread, size,  |
                   |  direction)     |
                   +-----------------+
```

### 8.2 Component Specifications

| Component | Input | Output | Frequency | Latency Target |
|-----------|-------|--------|-----------|----------------|
| IV Surface Fitter | Raw IV chain (all strikes) | SVI/SABR parameters + smooth $\sigma(K)$ | 5 min | < 2 sec |
| B-L Prob Extractor | Smooth $C(K)$ from surface | $P^{\mathbb{Q}}(S_T > K)$ for all relevant $K$ | 5 min | < 500 ms |
| Vol Shape Monitor | IV surface snapshots | $RR_{25}$, $BF_{25}$, $\sigma_{ATM}$ z-scores | 5 min | < 200 ms |
| Term Structure Analyzer | Multi-expiry IV surfaces | Forward vol, TS slope, calendar spreads | 5 min | < 1 sec |
| GEX Map Builder | EOD Greeks + OI | Strike-level GEX, net GEX, pin strikes | Daily | < 5 sec |
| UOA Detector | Intraday trade volume | Strike-level UOA z-scores | 5 min | < 1 sec |
| $\mathbb{Q} \to \mathbb{P}$ Bridge | $P^{\mathbb{Q}}$, VRP, moneyness, DTE | $P^{\mathbb{P}}$ calibrated estimate | Per update | < 100 ms |
| Signal Aggregator | All signal outputs | Composite signal vector | 5 min | < 200 ms |
| Fair Value Calculator | $P^{\mathbb{P}}$, signal vector | Polymarket fair value + confidence | 5 min | < 100 ms |
| Polymarket Price Feed | CLOB API | Current mid, spread, depth | 1 min | < 500 ms |
| Quote Engine | Fair value vs market price | Bid/ask quotes, sizes | Continuous | < 1 sec |

### 8.3 Data Storage Schema

```sql
-- Intraday IV snapshots (primary signal source)
CREATE TABLE iv_snapshots (
    symbol          TEXT,
    expiration      DATE,
    strike          DECIMAL(10,3),
    right           TEXT,          -- 'call' or 'put'
    timestamp       TIMESTAMP,
    bid_iv          DECIMAL(8,6),
    mid_iv          DECIMAL(8,6),
    ask_iv          DECIMAL(8,6),
    underlying_price DECIMAL(10,4),
    iv_error        DECIMAL(8,6),
    PRIMARY KEY (symbol, expiration, strike, right, timestamp)
);

-- Derived vol surface metrics (per snapshot)
CREATE TABLE vol_surface_metrics (
    symbol          TEXT,
    expiration      DATE,
    timestamp       TIMESTAMP,
    atm_vol         DECIMAL(8,6),
    rr_25d          DECIMAL(8,6),   -- 25-delta risk reversal
    bf_25d          DECIMAL(8,6),   -- 25-delta butterfly
    rr_10d          DECIMAL(8,6),   -- 10-delta risk reversal
    bf_10d          DECIMAL(8,6),   -- 10-delta butterfly
    svi_a           DECIMAL(10,6),  -- SVI parameters
    svi_b           DECIMAL(10,6),
    svi_rho         DECIMAL(10,6),
    svi_m           DECIMAL(10,6),
    svi_sigma       DECIMAL(10,6),
    fit_rmse        DECIMAL(10,8),  -- fit quality
    PRIMARY KEY (symbol, expiration, timestamp)
);

-- Daily GEX and OI data
CREATE TABLE daily_gex (
    symbol          TEXT,
    expiration      DATE,
    strike          DECIMAL(10,3),
    right           TEXT,
    date            DATE,
    open_interest   INTEGER,
    volume          INTEGER,
    delta           DECIMAL(10,8),
    gamma           DECIMAL(10,8),
    vanna           DECIMAL(10,8),
    charm           DECIMAL(10,8),
    gex             DECIMAL(15,4),  -- gamma * OI * 100 * S
    PRIMARY KEY (symbol, expiration, strike, right, date)
);

-- Signal outputs
CREATE TABLE signal_log (
    symbol          TEXT,
    polymarket_market_id TEXT,
    timestamp       TIMESTAMP,
    strike          DECIMAL(10,3),
    p_q             DECIMAL(8,6),   -- risk-neutral probability
    p_p             DECIMAL(8,6),   -- physical probability (calibrated)
    pm_mid          DECIMAL(8,6),   -- Polymarket midpoint
    edge            DECIMAL(8,6),   -- p_p - pm_mid
    skew_z          DECIMAL(6,3),
    vol_z           DECIMAL(6,3),
    bf_z            DECIMAL(6,3),
    gex_signal      DECIMAL(10,4),
    uoa_z           DECIMAL(6,3),
    composite_signal DECIMAL(8,4),
    confidence      DECIMAL(4,3),
    PRIMARY KEY (symbol, polymarket_market_id, timestamp)
);
```

### 8.4 Rate Limiting and Data Budget

ThetaData Standard tier: **2 concurrent server threads**, no rate limit on requests (only concurrency limited).

**Estimated daily data pulls** (per symbol, single expiry):

| Endpoint | Requests/Day | Data Volume |
|----------|-------------|-------------|
| IV History (5m, all strikes) | 1 per expiry | ~3,120 rows |
| OI (daily) | 1 | ~100 rows |
| EOD Greeks | 1 | ~100 rows |
| Polymarket prices (1m fidelity) | 1 per market | ~390 rows |

For 5 symbols x 3 expiries: ~15 IV requests + 5 OI + 5 EOD = 25 requests/day. Well within limits.

---

## 9. Backtesting Test Specifications

### 9.1 Universe and Time Period

- **Symbols**: NVDA, AAPL, TSLA, AMZN, META, SPY, QQQ, MSFT, GOOGL, AMD
- **Period**: 2020-01-01 to 2026-03-31 (6+ years, includes COVID crash, 2022 bear, 2023-25 rally)
- **Expiries**: Weekly options (Friday expiry) + daily where available
- **Strikes**: ATM +/- 10% in 1% increments (21 strikes per underlying per expiry)
- **Data source**: ThetaData historical (available from 2016 for Standard tier)

### 9.2 Test 1: Recalibration Frequency Comparison

**Objective**: Determine optimal B-L recalibration interval.

**Setup**:
1. For each trading day and each expiry, compute $P^{\mathbb{Q}}(S_T > K)$ at intervals: EOD, 30m, 5m, 1m
2. Compare each to the actual settlement outcome ($S_T > K$ or not)
3. Compute Brier score: $BS = \frac{1}{N}\sum_{i=1}^{N}(p_i - o_i)^2$ where $o_i \in \{0, 1\}$

**Metrics**:
- Brier score by interval
- Calibration curve (reliability diagram) by interval
- Information ratio: $\frac{\Delta \text{Brier score}}{\Delta \text{compute cost}}$
- Lead time: how far in advance of Polymarket repricing does the signal fire?

**Expected outcome**: 5m achieves 80%+ of the Brier improvement of 1m at 20% of the compute cost.

### 9.3 Test 2: Vol Surface Shape Signals

**Objective**: Validate that skew, butterfly, and ATM vol changes predict Polymarket repricing.

**Setup**:
1. Compute $RR_{25}$, $BF_{25}$, $\sigma_{ATM}$ at 5-min intervals
2. Compute z-scores relative to 20-day rolling mean and std
3. Event study: condition on z-score crossing $\pm 1.5$ or $\pm 2.0$
4. Measure Polymarket price movement in the subsequent 15, 30, 60, 120 minutes

**Metrics**:
- Hit rate: fraction of times signal direction matches subsequent PM move
- Average P&L per signal: $\bar{r} = E[(\text{fair value} - \text{PM mid}) \cdot \text{direction}]$
- Sharpe ratio of signal-based strategy (annualized)
- Decay curve: signal predictive power as function of time after signal

### 9.4 Test 3: GEX Pinning Effect

**Objective**: Quantify how gamma exposure affects realized settlement probability.

**Setup**:
1. For each expiry date, compute GEX map from prior-day OI and EOD Greeks
2. Identify the **max-gamma strike** $K^*$
3. Compare $P^{\mathbb{Q}}(S_T > K)$ vs realized frequency for $K$ near vs far from $K^*$

**Metrics**:
- Pinning frequency: how often does $|S_T - K^*| < 0.5\%$?
- Calibration improvement: does a GEX-adjusted probability estimate (Section 4.2) have lower Brier score than raw B-L?
- Economic significance: what is the average edge for Polymarket contracts near the max-gamma strike?

### 9.5 Test 4: $\mathbb{Q} \to \mathbb{P}$ Calibration

**Objective**: Train and validate the physical probability bridge.

**Setup**:
1. Walk-forward: train on 252 trading days, test on next 63 days, roll forward
2. Features: $\text{logit}(P^{\mathbb{Q}})$, moneyness $(K/S - 1)$, DTE, VRP (IV^2 - HAR-RV forecast), PCR
3. Target: binary outcome $(S_T > K)$
4. Models: logistic regression, isotonic regression, gradient-boosted trees

**Metrics**:
- Out-of-sample Brier score vs. raw $P^{\mathbb{Q}}$ vs. Polymarket price
- Log-loss comparison
- Calibration curve (is $\hat{P}^{\mathbb{P}} = 0.3$ actually realized 30% of the time?)
- Feature importance: which variables most improve calibration?

### 9.6 Test 5: 0DTE Signal for Same-Day Contracts

**Objective**: Evaluate 0DTE options as a real-time signal for same-day Polymarket contracts.

**Setup**:
1. SPX 0DTE options on Mon/Wed/Fri
2. Compute B-L probability from 0DTE chain at 1-minute intervals
3. Compare to SPX/SPY-based Polymarket contract prices

**Metrics**:
- Cross-correlation at various lags: does 0DTE lead Polymarket?
- Intraday P&L from trading Polymarket contracts based on 0DTE signal updates
- Comparison: 0DTE signal vs. weekly options signal for same-day contracts

### 9.7 Test 6: Composite Signal Portfolio

**Objective**: Combine all signals into a unified scoring system and test full strategy.

**Setup**:
1. Signal vector: $[\Delta P^{\mathbb{Q}}_{5m}, z_{\text{skew}}, z_{\text{vol}}, z_{\text{bf}}, \text{GEX}_{\text{adj}}, \text{UOA}_z, P^{\mathbb{P}} - P^{PM}]$
2. Train a simple linear model (or ridge regression) on $\text{subsequent PM price change}$
3. Strategy: quote on Polymarket when composite signal exceeds threshold
4. Walk-forward validation with realistic Polymarket execution (cross spread, limit order fill rate)

**Metrics**:
- Sharpe ratio (gross and net of Polymarket fees)
- Maximum drawdown
- Average daily P&L
- Win rate by signal strength quintile
- Turnover and inventory holding time

### 9.8 Backtesting Infrastructure Integration

All tests integrate with the existing backtesting engine at `backtesting-engine/bt_engine/`. Key requirements:

1. **Data loaders**: ThetaData historical IV, Greeks, OI fetchers with local caching
2. **Signal generators**: Modular Python classes per signal family (sections 1--7)
3. **Evaluators**: Brier score, calibration curves, lead-lag analysis, P&L simulation
4. **Polymarket simulator**: historical price feed replay with realistic execution model

```python
# Pseudocode for backtesting harness integration
class OptionsSignalBacktest:
    def __init__(self, symbol: str, start: date, end: date):
        self.iv_loader = ThetaDataIVLoader(symbol, tier="standard")
        self.greeks_loader = ThetaDataGreeksLoader(symbol)
        self.oi_loader = ThetaDataOILoader(symbol)
        self.pm_loader = PolymarketHistoryLoader(symbol)

    def run_recalibration_test(self, intervals: list[str]) -> dict:
        """Test 1: Compare recalibration frequencies."""
        results = {}
        for interval in intervals:
            iv_chain = self.iv_loader.fetch(interval=interval)
            surface = SVIFitter().fit(iv_chain)
            probs = BreedenLitzenberger(surface).extract_cdf()
            outcomes = self.get_settlement_outcomes()
            results[interval] = brier_score(probs, outcomes)
        return results

    def run_vol_shape_test(self, z_threshold: float = 2.0) -> dict:
        """Test 2: Vol surface shape signal predictiveness."""
        shape_ts = self.compute_shape_timeseries(interval="5m")
        z_scores = rolling_zscore(shape_ts, window=20*78)  # 20 days x 78 5-min bars
        events = z_scores[abs(z_scores) > z_threshold]
        pm_reactions = self.pm_loader.get_forward_returns(events.index, horizons=[15, 30, 60, 120])
        return event_study_stats(events, pm_reactions)
```

---

## 10. Priority and Implementation Roadmap

### Phase 1: Foundation (Week 1--2)
- [ ] Implement 5-min IV chain fetcher and local cache
- [ ] Build SVI surface fitter with 5-min recalibration
- [ ] Run Test 1 (recalibration frequency) on NVDA historical data
- [ ] Confirm 5-min as optimal interval (or adjust)

### Phase 2: Shape Signals (Week 3--4)
- [ ] Compute intraday skew, butterfly, ATM vol time series
- [ ] Build z-score signal generator
- [ ] Run Test 2 (vol shape signals) on 5 symbols
- [ ] Integrate shape signals into backtesting engine

### Phase 3: Greeks and Flow (Week 5--6)
- [ ] Build daily GEX map from EOD Greeks + OI
- [ ] Implement UOA detector from intraday volume
- [ ] Run Test 3 (GEX pinning) and OI/volume signal analysis
- [ ] Add to signal aggregator

### Phase 4: Probability Bridge (Week 7--8)
- [ ] Collect historical B-L probabilities vs. outcomes (2020--2026)
- [ ] Train $\mathbb{Q} \to \mathbb{P}$ calibration models (logistic, isotonic, GBT)
- [ ] Run Test 4 (walk-forward validation)
- [ ] Integrate VRP adjustment into fair value calculator

### Phase 5: 0DTE and Composite (Week 9--10)
- [ ] Build 0DTE SPX signal pipeline (1-min recalibration)
- [ ] Run Test 5 (0DTE signal)
- [ ] Build composite signal aggregator
- [ ] Run Test 6 (full strategy backtest)
- [ ] Production deployment decision

---

## Appendix A: Key Mathematical Identities

### Black-Scholes Greeks (for computing from IV when All Greeks endpoint unavailable)

$$\Delta_{\text{call}} = e^{-qT}\Phi(d_1), \quad d_1 = \frac{\ln(S/K) + (r - q + \frac{1}{2}\sigma^2)T}{\sigma\sqrt{T}}$$

$$\Gamma = \frac{e^{-qT}\phi(d_1)}{S\sigma\sqrt{T}}, \quad \text{Vanna} = -e^{-qT}\phi(d_1)\frac{d_2}{\sigma}$$

$$\text{Charm} = -e^{-qT}\phi(d_1)\frac{2(r-q)T - d_2\sigma\sqrt{T}}{2T\sigma\sqrt{T}}$$

$$\text{Vega} = Se^{-qT}\phi(d_1)\sqrt{T}, \quad d_2 = d_1 - \sigma\sqrt{T}$$

### Model-Free Implied Variance (for VRP calculation)

$$\sigma^2_{\text{MF}} = \frac{2e^{rT}}{T}\left[\int_0^F \frac{P(K)}{K^2}dK + \int_F^{\infty}\frac{C(K)}{K^2}dK\right]$$

Discretized:

$$\sigma^2_{\text{MF}} \approx \frac{2e^{rT}}{T}\sum_i \frac{\Delta K_i}{K_i^2} \cdot Q(K_i)$$

where $Q(K_i)$ is the OTM option price (put if $K_i < F$, call if $K_i > F$).

### HAR-RV Model for Realized Variance Forecast

$$RV_{t+1}^{(d)} = \beta_0 + \beta_d \cdot RV_t^{(d)} + \beta_w \cdot RV_t^{(w)} + \beta_m \cdot RV_t^{(m)} + \epsilon_t$$

where $RV^{(d)}$, $RV^{(w)}$, $RV^{(m)}$ are daily, weekly (5-day), and monthly (22-day) realized variances computed from intraday returns.

---

## Appendix B: ThetaData Endpoint Reference (Standard Tier)

| Endpoint | Tier | Interval Support | Key Fields |
|----------|------|-----------------|------------|
| `/v3/option/history/greeks/implied_volatility` | Value+ | tick to 1h | bid_iv, mid_iv, ask_iv, underlying_price |
| `/v3/option/history/greeks/eod` | Free+ | EOD only | All Greeks (1st, 2nd, 3rd order), OHLCV, OI |
| `/v3/option/history/open_interest` | Free+ | Daily (06:30 ET) | open_interest by strike/expiry |
| `/v3/option/history/trade` | Value+ | tick to 1h | price, size, condition |
| `/v3/option/history/greeks/all` | **Pro** | tick to 1h | All Greeks at intraday intervals |
| `/v3/option/snapshot/greeks/implied_volatility` | Value+ | Real-time | Current IV snapshot |
| `/v3/option/snapshot/open_interest` | Free+ | Real-time | Current OI snapshot |

**Standard tier capabilities**: Tick-level options data from 2016, 2 concurrent threads, real-time, 2nd/3rd order Greeks, trade Greeks. The critical gap vs Pro is the lack of intraday All Greeks history --- work around by computing from IV.

---

## Appendix C: Polymarket API Reference

| Endpoint | Auth | Key Parameters |
|----------|------|----------------|
| `GET /prices-history` | None | `market` (asset_id), `interval` (1h/6h/1d/1w/1m/max/all), `fidelity` (minutes, default 1), `startTs`, `endTs` |
| `GET /midpoint` | None | `token_id` |
| `GET /book` | None | `token_id` --- full L2 orderbook |
| `GET /spread` | None | `token_id` |
| `WSS /ws/market` | None | Real-time price updates |

**Price history response**: Array of `{t: unix_seconds, p: float}` where `p` is the midpoint price (0 to 1 scale, directly interpretable as market-implied probability).
