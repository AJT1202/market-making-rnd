---
title: Risk-Neutral vs Physical Probabilities
tags:
  - quantitative-finance
  - risk-neutral
  - physical-probability
  - variance-risk-premium
  - skew-premium
  - pricing-kernel
  - polymarket
  - market-making
created: 2026-03-31
status: research
related:
  - "[[Breeden-Litzenberger-Pipeline]]"
  - "[[Vol-Surface-Fitting]]"
---

# Risk-Neutral vs Physical Probabilities

> **Goal**: Understand the gap between risk-neutral probabilities $P^{\mathbb{Q}}(S_T > K)$ extracted from options (via the [[Breeden-Litzenberger-Pipeline]]) and real-world probabilities $P^{\mathbb{P}}(S_T > K)$ that determine Polymarket contract settlement. Determine whether and how to adjust.

---

## 1. The Fundamental Distinction

### 1.1 Two Probability Measures

Financial markets operate under two distinct probability frameworks:

| | Physical Measure $\mathbb{P}$ | Risk-Neutral Measure $\mathbb{Q}$ |
|---|---|---|
| **Also called** | Real-world, statistical, actuarial | Pricing measure, equivalent martingale measure |
| **What it represents** | Actual frequency of outcomes | Market-implied pricing of outcomes |
| **Derived from** | Historical data, econometric models | Current option prices |
| **Used for** | Risk management, forecasting | Derivative pricing, replication |
| **Risk premium** | Embedded | Removed (by construction) |

### 1.2 The Pricing Kernel Connection

The relationship between the two measures is mediated by the **pricing kernel** (stochastic discount factor) $M$:

$$q^{\mathbb{Q}}(S_T) = \frac{M(S_T) \cdot p^{\mathbb{P}}(S_T)}{E^{\mathbb{P}}[M(S_T)]}$$

where $q^{\mathbb{Q}}$ is the risk-neutral density and $p^{\mathbb{P}}$ is the physical density. The pricing kernel encodes investor risk preferences:

- $M(S_T)$ is **high** in bad states (market crashes) --- investors value payoffs more when the market is down
- $M(S_T)$ is **low** in good states (market rallies) --- payoffs are less valuable when everyone is already doing well

This means the risk-neutral measure **overweights bad states** and **underweights good states** relative to physical reality.

### 1.3 Implications for Binary Option Pricing

For a Polymarket contract "Will $S_T > K$?":

$$P^{\mathbb{Q}}(S_T > K) \neq P^{\mathbb{P}}(S_T > K)$$

The direction of the bias depends on where $K$ sits:

| Strike Location | Risk-Neutral vs Physical | Intuition |
|-----------------|--------------------------|-----------|
| $K \ll S_0$ (deep ITM binary) | $P^{\mathbb{Q}} < P^{\mathbb{P}}$ | $\mathbb{Q}$ overweights crash scenarios, reducing the probability of finishing above $K$ |
| $K \approx S_0$ (near ATM) | Approximately equal, but $P^{\mathbb{Q}}$ slightly lower | Crash premium slightly dominates |
| $K \gg S_0$ (deep OTM binary) | $P^{\mathbb{Q}} < P^{\mathbb{P}}$ | $\mathbb{Q}$ overweights left tail, underweights right tail |

> [!important] Key Insight for Polymarket
> Risk-neutral probabilities extracted from options are **not** the same as the physical probabilities that determine whether a Polymarket contract settles YES or NO. The risk-neutral measure systematically distorts probabilities by embedding risk premia. For market making, this creates both a challenge (fair value estimation) and an opportunity (if you can estimate the adjustment correctly, you have an edge).

---

## 2. The Variance Risk Premium

### 2.1 Definition

The **variance risk premium** (VRP) is the difference between expected variance under $\mathbb{Q}$ and under $\mathbb{P}$:

$$\text{VRP} = E^{\mathbb{Q}}[\sigma^2_{\text{realized}}] - E^{\mathbb{P}}[\sigma^2_{\text{realized}}]$$

Equivalently, it can be measured as:

$$\text{VRP} = \text{IV}^2 - E^{\mathbb{P}}[\text{RV}^2]$$

where IV is implied volatility (the $\mathbb{Q}$-expectation) and RV is subsequently realized volatility.

### 2.2 Empirical Magnitude

The VRP is **consistently positive** for equity indices --- implied volatility systematically exceeds realized volatility:

| Study | Asset | Period | Mean VRP (annualized var points) | Key Finding |
|-------|-------|--------|----------------------------------|-------------|
| Carr & Wu (2009) | S&P 500 | 1990-2005 | ~21.4 (IV$^2$ = 36.3, RV$^2$ = 14.9) | Large, persistent, precisely estimated |
| Bollerslev, Tauchen & Zhou (2009) | S&P 500 | 1990-2007 | Mean 0.0195, Vol 0.0225 | Moderate persistence; predicts returns |
| Fed IFDP 1035 | Global markets | Various | Positive across all developed markets | Universal phenomenon |

**In volatility terms:** If IV $\approx$ 19% and RV $\approx$ 12%, the VRP represents about 7 vol points, or roughly 37% of IV.

### 2.3 Why Does the VRP Exist?

Investors are willing to **overpay** for options (especially puts) because:

1. **Crash insurance**: equity options provide portfolio insurance against market crashes; investors accept negative expected returns on this insurance
2. **Variance aversion**: investors dislike uncertainty about future volatility itself (not just directional risk)
3. **Leverage constraints**: investors who cannot leverage sufficiently buy options to gain exposure, pushing up prices
4. **Supply-demand imbalance**: structural demand for protective puts from pension funds and asset managers exceeds natural supply

### 2.4 Impact on Binary Option Pricing

The VRP makes the risk-neutral distribution **wider** (fatter-tailed) than the physical distribution. For a binary option:

- $P^{\mathbb{Q}}(S_T > K)$ is computed using IV (which is too high)
- Under Black-Scholes: $P^{\mathbb{Q}} = N(d_2)$ where $d_2$ uses IV
- The "true" probability uses RV: $P^{\mathbb{P}} \approx N(\hat{d}_2)$ where $\hat{d}_2$ uses expected RV

Since IV > RV, the $\mathbb{Q}$-distribution is wider, and:
- OTM binaries (both puts and calls) are **overpriced** under $\mathbb{Q}$
- ATM binaries are approximately fairly priced (the width effect is symmetric at ATM)

---

## 3. The Skew Risk Premium

### 3.1 Definition

Beyond the overall variance premium, the **skew risk premium** captures the systematic overpricing of downside protection:

$$\text{Skew Premium} = \text{Skew}^{\mathbb{Q}} - \text{Skew}^{\mathbb{P}}$$

The risk-neutral distribution is more **negatively skewed** than the physical distribution. This manifests as:
- OTM puts are more expensive (in IV terms) than equidistant OTM calls
- The IV smile/skew is steeper than what realized return distributions would justify

### 3.2 Empirical Evidence

- VIX consistently exceeds SVIX (simple variance expectation) by an average of 27% of VIX$^2$, with the spread isolating skewness and kurtosis effects
- The spread widens dramatically during stress episodes (e.g., COVID crash)
- Persistent negative skew in the risk-neutral distribution reflects **crash aversion** in equity options

### 3.3 Impact on Binary Options

The skew premium means:

| Binary Strike Position | Effect |
|------------------------|--------|
| Below current price (ITM digital call) | $P^{\mathbb{Q}}$ **understates** the true probability (too much weight on crashes) |
| Well below current price | Effect is strongest --- crash scenarios drag down $P^{\mathbb{Q}}$ |
| Above current price (OTM digital call) | Smaller effect; slight overstatement of probability of being above |

> [!example] Worked Example
> Suppose NVDA is at \$120, and the Polymarket contract asks "Will NVDA close above \$115?"
> - From options: $P^{\mathbb{Q}}(S_T > 115) = 0.78$
> - The risk-neutral measure overweights the scenario where NVDA crashes below \$115
> - True physical probability: $P^{\mathbb{P}}(S_T > 115) \approx 0.82$
> - A market maker who prices at 0.78 is leaving 4 cents of edge on the table

---

## 4. Methods to Adjust from $\mathbb{Q}$ to $\mathbb{P}$

### 4.1 Overview of Approaches

| Method | Complexity | Data Requirements | Reliability |
|--------|------------|-------------------|-------------|
| Empirical calibration | Low | Historical options + outcomes | Moderate; regime-dependent |
| Exponential tilting (Esscher) | Medium | Risk aversion parameter | Theoretically clean; hard to calibrate $\theta$ |
| Power utility pricing kernel | Medium | Risk aversion coefficient $\gamma$ | Common assumption; may be misspecified |
| Historical comparison | Low | Historical return data | Simple but ignores forward-looking info |
| Ross Recovery Theorem | High | Full transition matrix from options | **Poor empirical performance** |
| Variance premium subtraction | Low | VRP estimate | Quick-and-dirty; ignores skew |

### 4.2 Method 1: Empirical Calibration (Recommended)

**Approach:** Historically compare $P^{\mathbb{Q}}(S_T > K)$ from options with actual binary outcomes, then fit a calibration function.

**Step 1: Collect historical data**

For each historical date $t$ and each strike $K$ available:
- Compute $\hat{p}^{\mathbb{Q}}_t = P^{\mathbb{Q}}_t(S_T > K)$ from the options chain (via [[Breeden-Litzenberger-Pipeline]])
- Record the actual outcome: $Y_t = \mathbb{1}(S_T > K)$

**Step 2: Fit a calibration curve**

Group by $\hat{p}^{\mathbb{Q}}$ bins and compute the empirical frequency of $Y_t = 1$ in each bin. Fit:

$$P^{\mathbb{P}}(S_T > K) = f(\hat{p}^{\mathbb{Q}})$$

Options for $f$:
- **Logistic regression**: $f(p) = \text{logit}^{-1}(\alpha + \beta \cdot \text{logit}(p))$
- **Platt scaling**: same as logistic, widely used in ML probability calibration
- **Isotonic regression**: non-parametric monotone mapping
- **Beta calibration**: $f(p) = \text{Beta\_CDF}(p; a, b, c)$

**Step 3: Apply the calibration**

For a new contract with $\hat{p}^{\mathbb{Q}} = 0.65$, look up $f(0.65)$ to get the calibrated physical probability.

> [!tip] Practical Note
> Segment the calibration by:
> - **DTE bucket** (0DTE, 1DTE, 2-5 DTE, 5+ DTE) --- the risk premium structure varies with maturity
> - **Underlying** (index vs single stock) --- single stocks have idiosyncratic vol that behaves differently
> - **Volatility regime** (VIX < 15, 15-25, 25+) --- risk premia are regime-dependent
> - **Moneyness** (how far $K$ is from $S_0$) --- the skew premium varies with moneyness

### 4.3 Method 2: Exponential Tilting (Esscher Transform)

The Esscher transform converts a probability density by exponential reweighting:

$$p^{\mathbb{P}}(x) = \frac{e^{\theta x} \cdot q^{\mathbb{Q}}(x)}{\int e^{\theta x} \cdot q^{\mathbb{Q}}(x) \, dx}$$

where $\theta$ is the **tilting parameter** that encodes the aggregate risk premium. Under exponential utility, $\theta$ equals the Arrow-Pratt coefficient of absolute risk aversion (CARA).

**Properties:**
- $\theta > 0$: shifts weight toward higher returns (undoing the crash overweighting of $\mathbb{Q}$)
- For normal distributions: $\text{Esscher}(N(\mu, \sigma^2)) = N(\mu + \theta\sigma^2, \sigma^2)$ --- it shifts the mean but preserves variance
- The transform preserves the exponential family structure

**Calibration of $\theta$:**
- Use the historical equity risk premium: $E^{\mathbb{P}}[R] - r_f \approx \theta \cdot \text{Var}^{\mathbb{Q}}[R]$
- With equity premium $\approx 5\%$ and variance $\approx 0.04$: $\theta \approx 1.25$
- Cross-validate on historical binary outcomes

**Advantages:** Theoretically principled; single-parameter adjustment; preserves distributional shape.

**Disadvantages:** Assumes exponential/CARA utility; the true pricing kernel may not have this form; single parameter may not capture both variance and skew premia.

### 4.4 Method 3: Power Utility Pricing Kernel

Assume a representative agent with power utility $U(W) = W^{1-\gamma}/(1-\gamma)$. The pricing kernel is:

$$M(S_T) = \delta \left(\frac{S_T}{S_0}\right)^{-\gamma}$$

where $\gamma$ is the coefficient of relative risk aversion and $\delta$ is the time discount factor. Then:

$$p^{\mathbb{P}}(S_T) \propto \frac{q^{\mathbb{Q}}(S_T)}{M(S_T)} \propto q^{\mathbb{Q}}(S_T) \cdot S_T^{\gamma}$$

**Calibration:**
- Empirical estimates of $\gamma$ range from 2 to 10 for equity markets
- A common starting point is $\gamma \approx 3$
- The sensitivity to $\gamma$ is moderate for near-ATM binaries and larger for deep OTM

**Procedure:**
1. Extract $q^{\mathbb{Q}}(S_T)$ via Breeden-Litzenberger
2. Compute $\tilde{p}(S_T) = q^{\mathbb{Q}}(S_T) \cdot S_T^{\gamma}$
3. Normalize: $p^{\mathbb{P}}(S_T) = \tilde{p}(S_T) / \int \tilde{p}(S) \, dS$
4. Integrate: $P^{\mathbb{P}}(S_T > K) = \int_K^{\infty} p^{\mathbb{P}}(S) \, dS$

### 4.5 Method 4: Historical Comparison

The simplest approach --- use historical return data directly:

1. Compute the historical frequency of $R_T > \ln(K/S_0)$ using a rolling window
2. Possibly adjust for current market conditions (higher vol = wider distribution)
3. Blend with the risk-neutral estimate:

$$P^{\text{fair}} = \lambda \cdot P^{\mathbb{Q}}(S_T > K) + (1 - \lambda) \cdot P^{\text{hist}}(S_T > K)$$

where $\lambda \in [0, 1]$ controls the blend weight.

**Advantages:** Simple; no model assumptions.

**Disadvantages:** Backward-looking; ignores current market-implied information; assumes stationarity of returns.

### 4.6 Method 5: Variance Premium Subtraction (Quick and Dirty)

Adjust implied volatility downward by the estimated variance risk premium before computing the binary probability:

$$\sigma_{\text{adj}} = \sqrt{\text{IV}^2 - \text{VRP}} \approx \text{IV} \times (1 - \text{VRP\_ratio}/2)$$

Then compute:

$$P^{\mathbb{P}}(S_T > K) \approx N\left(\frac{\ln(S/K) + (r - d - \sigma_{\text{adj}}^2/2)T}{\sigma_{\text{adj}}\sqrt{T}}\right)$$

**Typical VRP ratios:**
- Calm markets (VIX < 15): VRP/IV$^2 \approx 0.15$-$0.25$
- Normal markets (VIX 15-25): VRP/IV$^2 \approx 0.25$-$0.40$
- Stressed markets (VIX > 25): VRP/IV$^2 \approx 0.30$-$0.50$ (but highly variable)

> [!warning] This method ignores the skew premium and applies a uniform adjustment across all strikes. It is a reasonable first approximation but should be refined with the empirical calibration approach.

### 4.7 The Ross Recovery Theorem (and Why It Fails)

Ross (2015) proposed that physical probabilities can be recovered from options prices alone under the assumption that the pricing kernel is a function of the **state variable only** (transition-independent). The theorem shows that if you can observe the full matrix of state prices (Arrow-Debreu prices) across states and maturities, you can decompose it into the pricing kernel and physical probabilities.

**Why it fails in practice:**
- Requires the full transition matrix of state prices --- hard to obtain from sparse option data
- Transition state prices are **unstable** and exhibit multimodality
- Implies extreme and unrealistic risk-free rates in different states
- Empirical tests using S&P 500 options show recovered probabilities are **incompatible** with actual future returns (Jackwerth & Menner, 2020)
- It "offers limited additional information compared to risk-neutral probabilities"

> [!danger] Do Not Use
> The Ross Recovery Theorem is theoretically elegant but empirically unreliable. Do not use it for production pricing.

---

## 5. How Much Does the Adjustment Matter?

### 5.1 Magnitude by DTE

| DTE | VRP Impact on Binary Price | Skew Impact | Total Adjustment |
|-----|---------------------------|-------------|------------------|
| 0 DTE | Very small (~0.5-1 cent) | Negligible | **Minimal** |
| 1 DTE | Small (~1-2 cents) | Small (~0.5 cents) | **Small** |
| 2-5 DTE | Moderate (~2-4 cents) | Moderate (~1-2 cents) | **Noticeable** |
| 5-30 DTE | Large (~3-6 cents) | Significant (~2-4 cents) | **Material** |

**Why short-dated adjustments are smaller:**
- The VRP accumulates over time: $\text{VRP}_T \approx \text{VRP}_{\text{annual}} \times T$
- For $T = 1/252$ (1 day), the variance premium is only $\sim$1/252 of the annual amount
- Skew effects are also compressed into a very short horizon
- The physical and risk-neutral distributions converge as $T \to 0$

### 5.2 Practical Guidance for Polymarket Market Making

**For 0-1 DTE contracts (our primary use case):**
- The $\mathbb{Q}$-$\mathbb{P}$ gap is **small relative to the bid-ask spread** we'll be quoting
- A 1-2 cent adjustment on a 50-cent binary is 2-4% of the price
- Typical market-making spreads on Polymarket are 2-5 cents
- **Conclusion**: the risk-neutral probability is a reasonable first approximation; adjustment provides a small but real edge

**For 3-7 DTE contracts:**
- The gap becomes more material (3-5 cents)
- Adjustment is worth implementing for edge optimization
- Use the empirical calibration method with DTE-specific bins

**For longer-dated contracts:**
- The gap is significant and must be accounted for
- Use the power utility or empirical calibration approach
- The risk-neutral price is **not** a reliable estimate of fair value without adjustment

### 5.3 Edge Decomposition

For a Polymarket market maker, the total edge per trade decomposes as:

$$\text{Edge} = \underbrace{\frac{\text{spread}}{2}}_{\text{bid-ask capture}} + \underbrace{(P^{\mathbb{P}} - P^{\mathbb{Q}})}_{\text{risk premium edge}} \cdot \underbrace{\text{position sign}}_{\pm 1}$$

The risk premium edge is:
- Positive when **selling** OTM binaries (both puts and calls) priced at $P^{\mathbb{Q}}$ --- you're selling overpriced insurance
- Negative when **buying** OTM binaries at $P^{\mathbb{Q}}$ --- you're buying overpriced insurance

> [!tip] Market Making Strategy Implication
> If you can only quote at $P^{\mathbb{Q}}$ (the risk-neutral estimate), you have zero risk premium edge --- you're pricing where the options market prices. Your edge comes only from bid-ask spread capture. If you adjust toward $P^{\mathbb{P}}$, you gain an additional systematic edge from harvesting the risk premium, but you must ensure your adjustment is accurate to avoid being adversely selected.

---

## 6. Recommended Approach

### 6.1 Production Pipeline

```
1. Extract P_Q from options chain (Breeden-Litzenberger)
2. For 0-1 DTE: use P_Q directly (adjustment < spread)
3. For 2+ DTE: apply empirical calibration function f(P_Q)
4. Blend with historical base rate (weight 20-30%)
5. Use result as fair value for Polymarket quotes
6. Continuously backtest calibration accuracy
```

### 6.2 Calibration Function Design

The calibration function $f: P^{\mathbb{Q}} \to P^{\mathbb{P}}$ should satisfy:

1. **Monotonicity**: $f$ is strictly increasing (higher $P^{\mathbb{Q}}$ means higher $P^{\mathbb{P}}$)
2. **Boundary**: $f(0) = 0$, $f(1) = 1$
3. **Near-identity at extremes**: for $P^{\mathbb{Q}}$ near 0 or 1, $f(P^{\mathbb{Q}}) \approx P^{\mathbb{Q}}$
4. **Concavity**: typically $f(P^{\mathbb{Q}}) > P^{\mathbb{Q}}$ for $P^{\mathbb{Q}} \in (0, 1)$ due to the variance premium making $\mathbb{Q}$ distributions wider (pushing probability mass toward tails, reducing central probabilities)

A simple functional form:

$$f(p) = \frac{p^{1-\alpha}}{p^{1-\alpha} + (1-p)^{1-\alpha}} \quad \text{for } \alpha \in (0, 0.1)$$

This is a probability weighting function (Prelec-style) that adjusts for the systematic bias while preserving boundary conditions.

### 6.3 Backtesting Protocol

1. For each historical date, compute $\hat{p}^{\mathbb{Q}}$ from the options chain
2. Record the actual binary outcome $Y \in \{0, 1\}$
3. Compute calibration metrics:
   - **Brier score**: $\text{BS} = \frac{1}{N}\sum(p_i - Y_i)^2$ (lower is better)
   - **Log loss**: $-\frac{1}{N}\sum[Y_i \ln p_i + (1-Y_i)\ln(1-p_i)]$
   - **Calibration plot**: binned $\hat{p}$ vs observed frequency
   - **Reliability diagram**: should be close to the diagonal
4. Compare Brier scores for $P^{\mathbb{Q}}$ alone vs adjusted $f(P^{\mathbb{Q}})$
5. Improvement should be statistically significant (bootstrap confidence intervals)

---

## 7. Advanced Topics

### 7.1 The Equity Risk Premium and Binary Options

Under log-normal dynamics, the drift under $\mathbb{P}$ is $\mu$ (including equity risk premium) while under $\mathbb{Q}$ it is $r$ (risk-free rate). For a binary call:

$$P^{\mathbb{Q}}(S_T > K) = N(d_2^{\mathbb{Q}}), \quad d_2^{\mathbb{Q}} = \frac{\ln(S/K) + (r - \sigma^2/2)T}{\sigma\sqrt{T}}$$

$$P^{\mathbb{P}}(S_T > K) = N(d_2^{\mathbb{P}}), \quad d_2^{\mathbb{P}} = \frac{\ln(S/K) + (\mu - \sigma_{\text{phys}}^2/2)T}{\sigma_{\text{phys}}\sqrt{T}}$$

The difference depends on both:
- **Drift gap**: $\mu - r \approx 5\text{-}7\%$ annualized for equities
- **Volatility gap**: $\sigma_{\text{implied}} - \sigma_{\text{physical}} \approx 2\text{-}7$ vol points

For short-dated options ($T$ small), both effects are compressed:
- Drift effect: $(\mu - r)T \approx 0.02\%$ for 1-day options --- negligible
- Vol effect: the wider $\mathbb{Q}$-distribution matters more, but also compressed by $\sqrt{T}$

### 7.2 Conditional Risk Premia

The variance risk premium is **not constant** --- it varies with:

| Condition | VRP Behavior |
|-----------|-------------|
| Low VIX (< 15) | VRP is small (2-3 vol points); $\mathbb{Q}$ and $\mathbb{P}$ are close |
| Normal VIX (15-25) | VRP is moderate (4-7 vol points) |
| High VIX (> 25) | VRP is large but unstable (5-15 vol points) |
| Pre-earnings | VRP spikes for the specific stock; post-earnings IV crush |
| Macro events (FOMC, CPI) | VRP elevated for 0DTE on event day |

### 7.3 Single Stocks vs Indices

| | Index (SPY, QQQ) | Single Stock (NVDA, AAPL) |
|---|---|---|
| VRP magnitude | Large, stable | Variable, event-driven |
| Skew premium | Strong (crash insurance) | Moderate (less systematic risk) |
| Predictability | Higher (well-studied) | Lower (idiosyncratic) |
| $\mathbb{Q}$-$\mathbb{P}$ gap | Consistent, calibratable | Noisy, harder to calibrate |
| Recommendation | Adjust using established VRP estimates | Use empirical calibration with larger confidence intervals |

### 7.4 Martin's Lower Bound on Expected Returns

Martin (2017) showed that the risk-neutral variance provides a **lower bound** on the equity premium:

$$E^{\mathbb{P}}[R_T] - R_f \geq \frac{1}{R_f} \text{Var}^{\mathbb{Q}}(R_T) = \frac{\text{SVIX}^2}{R_f}$$

where SVIX is the simple variance swap rate. Empirically, the bound is far from tight (estimated slope ~5.2 vs theoretical 1.0), indicating that the covariance between returns and the stochastic discount factor is economically dominant.

This provides a useful **sanity check**: the $\mathbb{Q}$-$\mathbb{P}$ gap should be at least as large as the SVIX-implied equity premium.

---

## 8. Summary Decision Framework

```
┌─────────────────────────────────┐
│   Options Chain → P_Q (B-L)     │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│   Is DTE ≤ 1?                   │
│   YES → Use P_Q directly        │──── Fair value ≈ P_Q
│          (adjustment < spread)   │
└────────────┬────────────────────┘
             │ NO
             ▼
┌─────────────────────────────────┐
│   Apply empirical calibration   │
│   f(P_Q) → P_hat               │
│   Segment by DTE, underlying,  │
│   VIX regime, moneyness         │
└────────────┬────────────────────┘
             │
             ▼
┌─────────────────────────────────┐
│   Blend with historical rate    │
│   P_fair = 0.75*P_hat +        │──── Fair value for quoting
│            0.25*P_hist          │
└─────────────────────────────────┘
```

---

## 9. Key References

1. **Breeden, D.T. & Litzenberger, R.H.** (1978). "Prices of State-Contingent Claims Implicit in Option Prices." *Journal of Business*, 51(4), 621-651.
2. **Carr, P. & Wu, L.** (2009). "Variance Risk Premiums." *Review of Financial Studies*, 22(3), 1311-1341.
3. **Bollerslev, T., Tauchen, G. & Zhou, H.** (2009). "Expected Stock Returns and Variance Risk Premia." *Review of Financial Studies*, 22(11), 4463-4492.
4. **Ross, S.A.** (2015). "The Recovery Theorem." *Journal of Finance*, 70(2), 615-648.
5. **Jackwerth, J.C. & Menner, M.** (2020). "Does the Ross Recovery Theorem Work Empirically?" *Journal of Financial Economics*, 137(3), 723-739.
6. **Martin, I.** (2017). "What is the Expected Return on the Market?" *Quarterly Journal of Economics*, 132(1), 367-433.
7. **Esscher, F.** (1932). "On the Probability Function in the Collective Theory of Risk." *Skandinavisk Aktuarietidskrift*, 15, 175-195.
8. **Figlewski, S.** (2017). "Risk Neutral Densities: A Review." NYU Stern Working Paper.
9. **Zhu, E.** (2024). "On the Predictive Power of Breeden-Litzenberger's Risk-Neutral-Distribution." [Medium](https://medium.com/@ezhu1009/on-the-predictive-power-of-breeden-litzenbergers-risk-neutral-distribution-d6ad63c9db41)
