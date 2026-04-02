---
title: Implied Volatility Surface Fitting
tags:
  - quantitative-finance
  - implied-volatility
  - SABR
  - SVI
  - vol-surface
  - calibration
  - market-making
  - polymarket
created: 2026-03-31
status: research
related:
  - "[[Breeden-Litzenberger-Pipeline]]"
  - "[[Risk-Neutral-vs-Physical-Probabilities]]"
---

# Implied Volatility Surface Fitting

> **Goal**: Choose and calibrate the best implied volatility model for extracting risk-neutral probabilities from short-dated equity options (0-5 DTE) to price Polymarket binary contracts.

---

## 1. Why We Need a Vol Surface Model

The [[Breeden-Litzenberger-Pipeline]] requires a **smooth** call price function $C(K)$ to compute second derivatives. Raw market data is noisy and discrete. We need to:

1. **Interpolate** between listed strikes to get prices at arbitrary $K$
2. **Smooth** out bid-ask noise without destroying genuine smile features
3. **Extrapolate** cautiously beyond the last liquid strike
4. **Guarantee no-arbitrage** so that the extracted density is non-negative

Working in **implied volatility space** $\sigma(K)$ rather than price space $C(K)$ is critical: IV is a much smoother function of strike, and converting back through Black-Scholes automatically enforces many arbitrage constraints.

---

## 2. The SVI Model (Stochastic Volatility Inspired)

### 2.1 Raw SVI Parameterization

Introduced by Gatheral at Merrill Lynch (1999), published in 2004. The **total implied variance** $w(k) = \sigma^2(k) \cdot T$ as a function of **log-forward moneyness** $k = \ln(K/F)$:

$$\boxed{w(k) = a + b\left[\rho(k - m) + \sqrt{(k - m)^2 + \sigma^2}\right]}$$

**Five parameters:**

| Parameter | Domain | Interpretation |
|-----------|--------|----------------|
| $a$ | $a \in \mathbb{R}$ | Overall variance level (vertical shift) |
| $b$ | $b \geq 0$ | Tightness of the smile (controls slope of wings) |
| $\rho$ | $\rho \in (-1, 1)$ | Skew / rotation of the smile |
| $m$ | $m \in \mathbb{R}$ | Horizontal translation (center of smile) |
| $\sigma$ | $\sigma > 0$ | Smoothness / ATM curvature |

**Key constraint for non-negative variance:**

$$a + b\sigma\sqrt{1 - \rho^2} \geq 0$$

### 2.2 Asymptotic Behavior

As $|k| \to \infty$, SVI becomes linear in $k$:

$$w(k) \sim a + b(1 + \rho)k \quad \text{(right wing, } k \to +\infty\text{)}$$
$$w(k) \sim a + b(\rho - 1)k \quad \text{(left wing, } k \to -\infty\text{)}$$

This linearity is consistent with **Roger Lee's moment formula** (2004), which bounds the asymptotic slope of total variance:

$$\limsup_{k \to +\infty} \frac{w(k)}{k} \leq 2$$

This implies $b(1 + \rho) \leq 2$ and $b(1 - \rho) \leq 2$.

### 2.3 SVI Jump-Wings (SVI-JW) Parameterization

A reparameterization using more interpretable quantities, attributed to Tim Klassen (Goldman Sachs):

| Parameter | Meaning |
|-----------|---------|
| $v_t$ | ATM implied variance |
| $\psi_t$ | ATM volatility skew |
| $p_t$ | Slope of left (put) wing |
| $c_t$ | Slope of right (call) wing |
| $\tilde{v}_t$ | Minimum implied variance |

Conversion to raw parameters:

$$b = \frac{\sqrt{w_t}}{2}(c_t + p_t), \qquad \rho = 1 - \frac{p_t \sqrt{w_t}}{b}$$

where $w_t = v_t \cdot T$ is the ATM total variance.

### 2.4 SSVI (Surface SVI)

For fitting **across multiple expiries** simultaneously, Gatheral & Jacquier (2014) proposed:

$$w(k, \theta_t) = \frac{\theta_t}{2}\left\{1 + \rho\,\varphi(\theta_t)\,k + \sqrt{\left[\varphi(\theta_t)\,k + \rho\right]^2 + (1 - \rho^2)}\right\}$$

where $\theta_t$ is the ATM total variance at maturity $t$, and $\varphi(\theta)$ is a smooth function. Common choice:

$$\varphi(\theta) = \frac{1}{\eta\theta}\left(1 - \frac{1 - e^{-\eta\theta}}{{\eta\theta}}\right)^{1/2} \quad \text{(power-law Heston-like)}$$

**SSVI advantages:** Only 3 surface-level parameters ($\eta$, $\rho$, $\lambda$) plus the ATM variance term structure $\theta_t$, making it very parsimonious for a full surface.

### 2.5 No-Arbitrage Conditions

#### Butterfly Arbitrage (within a single slice)

The density extracted via Breeden-Litzenberger is non-negative if and only if the function $g(k)$ is non-negative everywhere:

$$g(k) = \left(1 - \frac{k\,w'(k)}{2\,w(k)}\right)^2 - \frac{w'(k)^2}{4}\left(\frac{1}{w(k)} + \frac{1}{4}\right) + \frac{w''(k)}{2} \geq 0$$

If $g(k) < 0$ anywhere, the calibration admits butterfly arbitrage and the implied density has negative regions.

#### Calendar Spread Arbitrage (across slices)

No calendar spread arbitrage requires total variance to be non-decreasing in maturity:

$$\frac{\partial w(k, t)}{\partial t} \geq 0 \quad \forall\, k, t$$

This means slices at different maturities must not cross.

#### SSVI Sufficient Conditions (Theorem 4.2 of Gatheral & Jacquier)

The SSVI surface is free of butterfly arbitrage if:

$$\theta\,\varphi(\theta)(1 + |\rho|) < 4 \quad \forall\, \theta > 0$$

This is also **necessary** (Lemma 4.2). Additionally:

$$\theta\,\varphi(\theta)^2(1 + |\rho|) \leq 4 \quad \forall\, \theta > 0$$

### 2.6 SVI Calibration Procedure

**Objective:** minimize the weighted sum of squared errors between model and market IVs:

$$\min_{\chi} \sum_{i=1}^{N} \omega_i \left[\sigma_{\text{market}}(K_i) - \sigma_{\text{SVI}}(K_i; \chi)\right]^2$$

where $\chi = (a, b, \rho, m, \sigma)$ and $\omega_i$ are weights (e.g., by vega, by inverse bid-ask spread, or uniform).

**Practical algorithm:**

1. **Initialize** using a quasi-explicit method:
   - Fix $\rho = 0$, $m = 0$ and solve the resulting 3-parameter problem analytically
   - Or use the fact that the ATM level, slope, and curvature of the smile give 3 equations in 3 unknowns
2. **Optimize** using SLSQP (Sequential Least-Squares Quadratic Programming) or Levenberg-Marquardt with bounds
3. **Enforce constraints**: $b \geq 0$, $|\rho| < 1$, $\sigma > 0$, $a + b\sigma\sqrt{1-\rho^2} \geq 0$, Lee bounds
4. **Validate**: check $g(k) \geq 0$ across the fitted domain

---

## 3. The SABR Model (Stochastic Alpha Beta Rho)

### 3.1 Model Dynamics

The SABR model specifies two coupled SDEs for the forward price $F_t$ and its volatility $\sigma_t$:

$$dF_t = \sigma_t F_t^\beta \, dW_t$$
$$d\sigma_t = \nu \sigma_t \, dZ_t$$
$$dW_t \, dZ_t = \rho \, dt$$

**Four parameters:**

| Parameter | Domain | Interpretation |
|-----------|--------|----------------|
| $\alpha$ ($\sigma_0$) | $\alpha > 0$ | Initial / ATM volatility level |
| $\beta$ | $0 \leq \beta \leq 1$ | Backbone: controls how vol scales with forward level |
| $\rho$ | $-1 < \rho < 1$ | Correlation between forward and vol (drives skew) |
| $\nu$ | $\nu \geq 0$ | Vol-of-vol (drives smile curvature / kurtosis) |

**Special cases:**
- $\beta = 0$: Normal (Bachelier) model with stochastic vol
- $\beta = 1$: Log-normal model with stochastic vol
- $\nu = 0$: Deterministic CEV (constant elasticity of variance) model

### 3.2 Hagan's Asymptotic Implied Volatility Formula

The key practical result from Hagan et al. (2002) --- an **analytic approximation** for Black-Scholes implied volatility:

$$\sigma_B(K, F) = \frac{\alpha}{(FK)^{(1-\beta)/2}\left[1 + \frac{(1-\beta)^2}{24}\ln^2\frac{F}{K} + \frac{(1-\beta)^4}{1920}\ln^4\frac{F}{K}\right]} \cdot \frac{z}{x(z)} \cdot \left[1 + \epsilon T\right]$$

where:

$$z = \frac{\nu}{\alpha}(FK)^{(1-\beta)/2}\ln\frac{F}{K}$$

$$x(z) = \ln\left[\frac{\sqrt{1 - 2\rho z + z^2} + z - \rho}{1 - \rho}\right]$$

$$\epsilon = \frac{(1-\beta)^2 \alpha^2}{24(FK)^{1-\beta}} + \frac{\rho\beta\nu\alpha}{4(FK)^{(1-\beta)/2}} + \frac{(2-3\rho^2)\nu^2}{24}$$

For ATM ($K = F$), this simplifies to:

$$\sigma_{\text{ATM}} = \frac{\alpha}{F^{1-\beta}}\left[1 + \left(\frac{(1-\beta)^2\alpha^2}{24 F^{2(1-\beta)}} + \frac{\rho\beta\nu\alpha}{4 F^{1-\beta}} + \frac{(2 - 3\rho^2)\nu^2}{24}\right)T\right]$$

> [!note] Obloj's Correction
> Obloj (2008) proposed a correction to the leading-order term in Hagan's formula that improves accuracy, especially away from ATM. The normal (Bachelier) SABR approximation is generally **more accurate** than the log-normal one.

### 3.3 SABR Calibration Procedure

**Step 1: Fix $\beta$**

$\beta$ is typically **pre-specified** based on market convention or view:
- **Equity options**: $\beta = 0.5$ or $\beta = 1$ are common
- **Interest rate options**: $\beta = 0.5$ is standard
- For our use case (equity/index options): start with $\beta = 1$ (log-normal backbone)

**Step 2: Calibrate $\alpha$, $\rho$, $\nu$**

With $\beta$ fixed, only 3 parameters remain:

1. **Initialize** using Le Floc'h & Kennedy's method:
   - Fit a second-degree polynomial to three points near ATM
   - Solve for initial $\alpha$, $\rho$, $\nu$ from coefficients
   - Caveat: $\nu^2$ can sometimes come out negative; use fallback initialization

2. **Iterative calibration:**
   ```
   For each iteration:
     a. Update rho and nu
     b. Find alpha such that model matches sigma_ATM exactly
     c. Compute total error across all strikes
     d. If converged, stop; else go to (a)
   ```

3. **Ensure ATM fit is exact**: anchor $\alpha$ to match ATM vol perfectly at each step, since ATM is the most traded and most reliable data point.

### 3.4 Known Limitations

| Issue | Description | Severity for Our Use Case |
|-------|-------------|--------------------------|
| **Negative density at low strikes** | Hagan's approximation can produce negative implied probability density for far OTM puts | Medium --- affects deep OTM tail |
| **Short-maturity inaccuracy** | The asymptotic expansion breaks down as $T \to 0$ because higher-order terms matter | **High** --- we use 0-5 DTE |
| **Only 3 free parameters** | Cannot fit complex smile shapes (e.g., W-shaped) | Low --- equity smiles are typically monotonic-ish |
| **No term structure** | SABR is calibrated per-slice; no surface consistency | Low --- we often need only one slice |

**Mitigation for short maturities:**
- Use the **Obloj correction** or the normal SABR formula
- Consider **PDE-based SABR** (numerically solve the Fokker-Planck equation) instead of the Hagan approximation
- Use **stochastic collocation** to project onto arbitrage-free variables

---

## 4. Why Cubic Splines Are Dangerous

### 4.1 The Oscillation Problem

Cubic splines enforce $C^2$ continuity (continuous second derivatives) by construction. When forced to pass **exactly** through noisy market data points, they:

- **Oscillate** between data points (Runge's phenomenon)
- Produce **non-monotonic** total variance (calendar spread arbitrage)
- Generate **negative implied densities** (butterfly arbitrage)

Even if input market data is arbitrage-free, cubic interpolation between points can **introduce arbitrage**.

### 4.2 Consequences for Our Pipeline

If the IV function $\hat{\sigma}(K)$ oscillates, then:

$$q(K) = e^{rT} \frac{\partial^2 C}{\partial K^2}$$

will have **negative regions**, meaning the extracted "probability" density has negative values --- nonsensical for pricing binary options.

For digital options specifically, fitting risk-neutral densities using cubic splines "might cause P&L shifts for digital options and other exotics," since the non-unimodal density distorts the cumulative probability $P(S_T > K)$.

### 4.3 Safe Alternatives

| Method | Key Property | Recommended? |
|--------|-------------|-------------|
| **Smoothing spline** (with regularization $\lambda$) | Allows residual error; trades fit vs smoothness | Yes, with care |
| **SVI** | Parametric; 5 parameters control shape | Yes, primary choice |
| **SABR** | Parametric; theoretically motivated | Yes, especially short-dated |
| **SSVI** | Parametric surface; built-in no-arb | Yes, for multi-expiry |
| **Monotone convex interpolation** | Guarantees no calendar/butterfly arb | Yes, specialized |
| **Cubic spline** (exact) | Passes through all points | **No** --- avoid |

> [!danger] Rule of Thumb
> **Never** use exact cubic spline interpolation through market IV data for density extraction. Always use either a parametric model (SVI, SABR) or a smoothing spline with regularization.

---

## 5. SABR vs SVI: Head-to-Head Comparison

### 5.1 Feature Comparison

| Feature | SVI | SABR |
|---------|-----|------|
| **Parameters per slice** | 5 | 3 (with $\beta$ fixed) |
| **Theoretical foundation** | Phenomenological (inspired by Heston asymptotics) | Stochastic vol model with analytic approximation |
| **Arbitrage-free?** | Can be enforced via constraints (Gatheral-Jacquier) | Hagan formula can violate; PDE version is safe |
| **Calibration difficulty** | Moderate (non-convex 5-param optimization) | Easier (3 params, ATM anchored) |
| **Surface consistency** | SSVI provides cross-maturity consistency | Per-slice only; no built-in term structure |
| **Short-maturity performance** | **Degrades** --- struggles with pronounced curvature | **Better** --- even with 2 fewer parameters |
| **Long-maturity performance** | Excellent | Good |
| **Lee's bounds** | Built into the functional form | Not automatic; must verify |
| **Industry adoption** | Equity/crypto options | Interest rate options, increasingly equity |

### 5.2 Empirical Evidence

From a direct comparison on SPX options (Chase the Devil blog, 2017):

> "In current market conditions, SVI does not work well for short maturities." With VIX at 11.32 and 1-week expiry, SVI produced "an obviously wrong right wing" and poor near-the-money fit.

> "SABR ($\beta = 1$) performed much better, even though it has two less parameters than SVI."

From the UPF comparative study (2024):
> Both models were calibrated on multi-day equity ETF options chains. SVI provided better overall fit for standard maturities, but SABR showed superior stability for short-dated slices.

### 5.3 Recommendation for Our Use Case

**Primary: SABR ($\beta = 1$) for 0-2 DTE, SVI for 3+ DTE.**

Rationale:
- Our core use case is **daily/weekly expiries on liquid stocks/indices**
- For 0-2 DTE, the smile is sharply curved and SVI's functional form cannot capture it well
- SABR with 3 parameters (anchored to ATM) is more robust for these extreme short maturities
- For 3+ DTE, SVI's additional flexibility and built-in Lee bounds make it preferable

**Alternative: SSVI for multi-expiry consistency** when pricing binary options across multiple expiries simultaneously.

**Hybrid approach:**
```
if DTE <= 2:
    model = SABR(beta=1)
elif DTE <= 14:
    model = SVI()
else:
    model = SSVI()  # or SVI per-slice
```

### 5.4 Calibration Recommendations

**For SABR (short-dated):**
- Fix $\beta = 1$ for equities
- Use Obloj's corrected formula (not original Hagan)
- Anchor $\alpha$ to ATM vol exactly
- Weight ATM and near-ATM strikes heavily (most liquid, most informative)
- Cross-validate: compute the density and verify non-negativity

**For SVI (medium-dated):**
- Use raw SVI parameterization (not JW for calibration --- JW is for interpretation)
- Initialize with quasi-explicit method
- Enforce all constraints: $b \geq 0$, $|\rho| < 1$, $\sigma > 0$, Lee bounds, $g(k) \geq 0$
- Use SLSQP optimizer with bounds
- For surface: prefer SSVI when multiple expiries available

---

## 6. Short-Dated Options: Special Considerations

### 6.1 Vol Behavior for 0-5 DTE

Short-dated options exhibit distinctive characteristics:

- **Pronounced smile curvature**: vol-of-vol dominates; the smile is "sharper" than longer-dated options
- **Gamma dominance**: delta and gamma are extremely large near ATM; vega is minimal
- **Theta bleed**: time decay is enormous; option values are small relative to bid-ask spreads
- **Charm effects**: deltas decay rapidly toward zero during the trading day, forcing constant dealer hedge adjustments
- **Vanna dynamics**: changes in implied vol cause rapid delta changes, driving late-day reversals (the "2-3 PM vanna unwind" in SPX)

### 6.2 Overnight vs Intraday Volatility

For 1+ DTE options, the premium reflects **two distinct risk components**:

$$\sigma_{\text{total}}^2 T = \sigma_{\text{overnight}}^2 T_{\text{night}} + \sigma_{\text{intraday}}^2 T_{\text{day}}$$

Empirically, overnight volatility per unit time is **higher** than intraday volatility because:
- Earnings announcements, macro releases, geopolitical events cluster outside market hours
- No opportunity to hedge during the overnight gap
- The market prices a "gap risk premium" into options spanning overnight periods

For **0DTE options**, there is no overnight component --- only intraday realized vol matters. This means:
- 0DTE IV is **lower** than what you'd naively interpolate from the term structure
- The VIX1D index (launched 2023) specifically measures expected intraday volatility from 0DTE SPX options

### 6.3 Term Structure Effects

The implied volatility term structure for very short maturities often shows:

- **Steep contango** (upward-sloping) in calm markets: longer-dated options carry more uncertainty
- **Backwardation** around events (FOMC, CPI, earnings): short-dated options spike in IV
- **0DTE vs 1DTE gap**: the overnight risk premium creates a discontinuity between 0DTE (intraday only) and 1DTE (includes overnight)

### 6.4 Implications for Density Extraction

| Challenge | Impact | Mitigation |
|-----------|--------|------------|
| Wide bid-ask relative to value | Noisy IV inputs | Aggressive filtering; use only most liquid strikes |
| Few liquid strikes | Sparse data for fitting | Use parametric model (SABR) with few parameters |
| Extreme curvature | SVI may not fit | Use SABR; accept wider confidence intervals |
| Intraday dynamics | Prices change rapidly | Refresh frequently (every 1-5 min for 0DTE) |
| Overnight gap (1DTE) | Must account for gap risk | Use term structure models; adjust for overnight vol |

---

## 7. Practical Implementation Notes

### 7.1 Calibration Code Structure

```python
# Pseudocode for the vol surface fitting pipeline

class VolSurfaceFitter:
    def __init__(self, model='auto'):
        self.model = model  # 'sabr', 'svi', 'ssvi', 'auto'

    def fit(self, strikes, ivs, forward, time_to_expiry, rate):
        """Fit vol model to market data."""
        if self.model == 'auto':
            if time_to_expiry <= 2/365:
                return self._fit_sabr(strikes, ivs, forward, time_to_expiry)
            else:
                return self._fit_svi(strikes, ivs, forward, time_to_expiry)
        # ...

    def _fit_sabr(self, strikes, ivs, forward, T):
        """SABR calibration with beta=1, Obloj formula."""
        beta = 1.0
        alpha_init = ivs[np.argmin(np.abs(strikes - forward))]
        # ... Levenberg-Marquardt optimization
        # Anchor alpha to ATM vol at each step
        return SABRParams(alpha, beta, rho, nu)

    def _fit_svi(self, strikes, ivs, forward, T):
        """Raw SVI calibration with constraints."""
        k = np.log(strikes / forward)  # log-moneyness
        w = ivs**2 * T  # total variance
        # ... SLSQP optimization with bounds
        return SVIParams(a, b, rho, m, sigma)

    def evaluate(self, strike):
        """Return fitted IV at arbitrary strike."""
        # ...

    def density(self, strike_grid):
        """Extract risk-neutral density via B-L."""
        prices = bs_call(self.forward, strike_grid, self.T,
                         self.r, self.evaluate(strike_grid))
        dK = strike_grid[1] - strike_grid[0]
        d2CdK2 = np.diff(prices, n=2) / dK**2
        return np.exp(self.r * self.T) * d2CdK2
```

### 7.2 Goodness-of-Fit Metrics

| Metric | Target | Description |
|--------|--------|-------------|
| RMSE(IV) | < 0.5 vol points | Root mean squared error in implied vol |
| Max absolute error | < 1.5 vol points | Worst individual strike fit |
| $\int q(K) dK$ | $\in [0.98, 1.02]$ | Density integrates to ~1 |
| $\min q(K)$ | $\geq 0$ | No negative densities |
| $E^{\mathbb{Q}}[S_T]$ | $\approx F$ | Mean matches forward |

### 7.3 Monitoring and Alerts

In production, flag the following conditions:
- Calibration fails to converge (> 100 iterations)
- Density has negative regions
- Implied forward deviates > 1% from computed forward
- ATM IV changes > 5 vol points between refreshes (data quality issue or major event)
- Fewer than 5 liquid strikes available (unreliable fit)

---

## 8. Key References

1. **Gatheral, J.** (2004). "A Parsimonious Arbitrage-Free Implied Volatility Parameterization with Application to the Valuation of Volatility Derivatives." Presentation at Global Derivatives & Risk Management, Madrid.
2. **Gatheral, J. & Jacquier, A.** (2014). "Arbitrage-free SVI volatility surfaces." *Quantitative Finance*, 14(1), 59-71. [arXiv:1204.0646](https://arxiv.org/abs/1204.0646)
3. **Hagan, P.S., Kumar, D., Lesniewski, A.S., & Woodward, D.E.** (2002). "Managing Smile Risk." *Wilmott Magazine*, September, 84-108.
4. **Obloj, J.** (2008). "Fine-tune your smile: Correction to Hagan et al." [arXiv:0708.0998](https://arxiv.org/abs/0708.0998)
5. **Lee, R.W.** (2004). "The Moment Formula for Implied Volatility at Extreme Strikes." *Mathematical Finance*, 14(3), 469-480.
6. **Le Floc'h, F. & Kennedy, G.** (2014). "Explicit SABR calibration through simple expansions." SSRN Working Paper.
7. **Chase the Devil** (2017). "When SVI Breaks Down." [chasethedevil.github.io](https://chasethedevil.github.io/post/when-svi-breaks-down/)
8. **UPF Thesis** (2024). "Multi-day implied volatility surface calibration in equity ETF options: a comparative study of SVI and SABR models."
