---
title: Cross-Platform Statistical Arbitrage Strategies
created: 2026-04-02
tags:
  - stat-arb
  - convergence
  - mispricing
  - cross-platform
  - polymarket
  - options
  - signal-analysis
sources:
  - https://hudsonthames.org/optimal-trading-thresholds-for-the-o-u-process/
  - https://hudsonthames.org/optimal-stopping-in-pairs-trading-ornstein-uhlenbeck-model/
  - https://www.researchgate.net/publication/228260813_Analytic_Solutions_for_Optimal_Statistical_Arbitrage_Trading
  - https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0244541
  - https://www.bauer.uh.edu/rsusmel/phd/hasbrouck95.pdf
  - https://www.sciencedirect.com/science/article/abs/pii/S0169207023000936
  - https://pmc.ncbi.nlm.nih.gov/articles/PMC9107071/
  - https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr677.pdf
  - https://reasonabledeviations.com/2020/10/10/option-implied-pdfs-2/
  - https://macrosynergy.com/research/detecting-trends-and-mean-reversion-with-the-hurst-exponent/
  - https://letianzj.github.io/mean-reversion.html
  - https://www.quantstart.com/articles/Basics-of-Statistical-Mean-Reversion-Testing-Part-II/
  - https://www.cambridge.org/core/journals/anziam-journal/article/on-optimal-thresholds-for-pairs-trading-in-a-onedimensional-diffusion-model/9D74C2412859519FE8009615C64C7A4F
---

# Cross-Platform Statistical Arbitrage Strategies

> **Objective**: Develop convergence trading strategies that exploit systematic mispricings between Polymarket binary event contracts and options-implied probabilities derived via [[Breeden-Litzenberger-Pipeline]]. Move beyond the naive alpha signal toward strategies with positive expected value after transaction costs and adverse selection.

> [!warning] NVDA POC Lesson
> The basic strategy of quoting Polymarket around the B-L fair value lost $18 in the NVDA POC (see [[NVDA-POC-Results]]). Primary cause: **adverse selection** — informed flow on Polymarket moved against our quotes before we could reprice. Any viable strategy must account for information asymmetry and execution dynamics, not just static mispricing.

---

## 1. Mispricing Signal Statistical Properties

### 1.1 Signal Definition

Define the core mispricing signal:

$$\alpha(t) = P_{\text{poly}}(t) - P_{\text{BL}}(t)$$

where:
- $P_{\text{poly}}(t)$ = Polymarket mid-price for the binary contract at time $t$
- $P_{\text{BL}}(t)$ = Breeden-Litzenberger implied probability $P^{\mathbb{Q}}(S_T > K)$ extracted from the options chain at time $t$

The sign convention is:
- $\alpha > 0$: Polymarket overprices the event relative to options (sell YES / buy NO on Polymarket)
- $\alpha < 0$: Polymarket underprices the event relative to options (buy YES on Polymarket)

### 1.2 Mean-Reversion Testing

If $\alpha(t)$ is mean-reverting, convergence trading is viable. Three complementary tests:

#### Augmented Dickey-Fuller (ADF) Test

Test the null hypothesis that $\alpha(t)$ has a unit root (random walk) against the alternative that it is stationary:

$$\Delta \alpha_t = \gamma + \delta \alpha_{t-1} + \sum_{i=1}^{p} \beta_i \Delta \alpha_{t-i} + \varepsilon_t$$

- Reject $H_0$ if test statistic < critical value (e.g., $-3.43$ at 1% level)
- **Implementation**: `statsmodels.tsa.stattools.adfuller(alpha_series, maxlag=None, regression='c')`
- Run on 1-minute, 5-minute, and 15-minute sampled $\alpha$ to check for timescale dependence

#### Hurst Exponent

The Hurst exponent $H$ classifies the memory structure:

| $H$ | Interpretation |
|-----|---------------|
| $H < 0.5$ | Mean-reverting (anti-persistent) |
| $H = 0.5$ | Random walk (Brownian motion) |
| $H > 0.5$ | Trending (persistent) |

Estimate via rescaled range (R/S) analysis or detrended fluctuation analysis (DFA):

$$\mathbb{E}\left[\frac{R(n)}{S(n)}\right] = C \cdot n^H$$

where $R(n)$ is the range and $S(n)$ is the standard deviation over a window of size $n$.

- **Target**: $H < 0.45$ would indicate strong mean-reversion suitable for convergence trading
- **Implementation**: `hurst` Python package or manual R/S calculation

#### Variance Ratio Test

Test whether the variance of $k$-period returns scales linearly with $k$ (as it would for a random walk):

$$VR(k) = \frac{\text{Var}(\alpha_t - \alpha_{t-k})}{k \cdot \text{Var}(\alpha_t - \alpha_{t-1})}$$

- $VR(k) < 1$: mean-reverting
- $VR(k) = 1$: random walk
- $VR(k) > 1$: trending

Use Lo-MacKinlay (1988) test with heteroskedasticity-robust standard errors. Test at $k = 2, 5, 10, 30, 60$ (minutes).

### 1.3 Ornstein-Uhlenbeck Process Fitting

If mean-reversion is confirmed, model $\alpha(t)$ as an OU process:

$$d\alpha = \theta(\mu - \alpha) \, dt + \sigma \, dW$$

where:
- $\theta > 0$: speed of mean reversion (higher = faster convergence)
- $\mu$: long-run mean of $\alpha$ (ideally near zero if markets are efficient on average)
- $\sigma$: volatility of the mispricing process
- $W$: standard Brownian motion

**Parameter Estimation** via maximum likelihood on discretely sampled data. For observations $\alpha_0, \alpha_1, \ldots, \alpha_n$ at intervals $\Delta t$:

$$\alpha_{t+\Delta t} \mid \alpha_t \sim \mathcal{N}\left(\mu + (\alpha_t - \mu) e^{-\theta \Delta t}, \; \frac{\sigma^2}{2\theta}(1 - e^{-2\theta \Delta t})\right)$$

MLE estimates:

$$\hat{\theta} = -\frac{\ln(\hat{\rho})}{\Delta t}, \quad \hat{\mu} = \frac{\hat{b}}{1 - \hat{\rho}}, \quad \hat{\sigma}^2 = \frac{2\hat{\theta} \hat{s}^2}{1 - e^{-2\hat{\theta} \Delta t}}$$

where $\hat{\rho}, \hat{b}, \hat{s}^2$ are from the AR(1) regression $\alpha_{t+1} = b + \rho \alpha_t + \varepsilon_t$.

**Half-life of mean reversion**:

$$t_{1/2} = \frac{\ln 2}{\theta}$$

This is the expected time for a deviation to decay to half its current value. If $t_{1/2} \gg$ market duration, convergence trading is impractical.

> [!example] Interpretation Guide
> - $t_{1/2} < 30\text{min}$: Fast convergence. Active intraday trading viable.
> - $30\text{min} < t_{1/2} < 4\text{hr}$: Moderate. Hold positions intraday, but need patience.
> - $t_{1/2} > 1\text{day}$: Slow convergence. Not suitable for intraday strategies on daily-expiry contracts.

### 1.4 Conditional Signal Analysis

The raw $\alpha$ signal likely has different properties depending on market state. Stratify the analysis:

| Conditioning Variable | Buckets | Hypothesis |
|----------------------|---------|------------|
| **Moneyness** ($S/K$) | Deep OTM (<0.90), OTM (0.90-0.97), ATM (0.97-1.03), ITM (1.03-1.10), Deep ITM (>1.10) | OTM and ITM contracts have wider B-L confidence intervals → larger $\alpha$ but noisier |
| **Time-to-expiry** (DTE) | <1hr, 1-4hr, 4hr-1day, 1-3day, 3-7day | Alpha should compress as expiry approaches (convergence to binary payoff) |
| **Underlying** | 11 tickers (NVDA, AAPL, TSLA, etc.) | Some underlyings may have more efficient Polymarket pricing |
| **Time-of-day** | Pre-market (before 9:30), Open (9:30-10:00), Midday (10:00-15:00), Close (15:00-16:00), After-hours | Opening and closing periods likely show different dynamics |
| **VIX regime** | Low (<15), Medium (15-25), High (>25) | High-vol periods may widen mispricings (options more expensive, participants uncertain) |

For each stratum, compute: mean $\alpha$, std $\alpha$, ADF p-value, Hurst $H$, OU parameters $(\theta, \mu, \sigma)$, half-life $t_{1/2}$.

### 1.5 Distribution Characterization

Characterize the full distribution of $\alpha$:

- **Normality**: Jarque-Bera test, Shapiro-Wilk test, Q-Q plot
- **Fat tails**: Excess kurtosis $\kappa - 3$. If $> 0$, extreme mispricings occur more than Gaussian predicts
- **Skewness**: Systematic directional bias? Does Polymarket consistently overprice or underprice?
- **Autocorrelation**: ACF/PACF plots. Strong autocorrelation at short lags confirms exploitable persistence
- **Regime shifts**: Hidden Markov Model with 2-3 states to detect switching between "efficient" ($\sigma_\alpha$ low) and "dislocated" ($\sigma_\alpha$ high) regimes

> [!important] Data Requirements
> - **Polymarket**: L2 orderbook snapshots (Telonex) at 1-second or finer granularity. Compute mid-price as $(P_{\text{best\_bid}} + P_{\text{best\_ask}}) / 2$. Also need trades for realized signal validation.
> - **Options**: ThetaData `option/at_time/quote` endpoint for time-aligned NBBO across the full chain. Compute B-L probability at matching timestamps.
> - **Minimum sample**: At least 20 trading days per underlying for reliable OU parameter estimation.

---

## 2. Convergence Trading (OU Optimal Stopping)

### 2.1 Theoretical Framework

Given the OU dynamics $d\alpha = \theta(\mu - \alpha) \, dt + \sigma \, dW$, the convergence trading problem reduces to an **optimal stopping problem**: when to enter and exit a trade to maximize expected profit per unit time.

Bertram (2010) provides analytic solutions. Define:
- Entry threshold: $a$ (enter long when $\alpha \leq -a$, enter short when $\alpha \geq a$)
- Exit threshold: $m$ (exit when $\alpha$ crosses back to $m$, typically $m = \mu$)
- Transaction cost per round trip: $c$

### 2.2 Bertram (2010) Optimal Thresholds

For a symmetric strategy around $\mu = 0$, the expected profit per trade is:

$$\mathbb{E}[\text{profit}] = a - m - c$$

and the expected trade duration (mean first-passage time from $a$ to $m$ in an OU process):

$$\mathbb{E}[\tau_{a \to m}] = \frac{1}{\theta} \sum_{n=0}^{\infty} \frac{(-1)^n}{n!} \left[ \Phi_n\left(\frac{a\sqrt{2\theta}}{\sigma}\right) - \Phi_n\left(\frac{m\sqrt{2\theta}}{\sigma}\right) \right]$$

where $\Phi_n$ involves parabolic cylinder functions.

The **optimal entry threshold** maximizes the expected return per unit time:

$$a^* = \arg\max_a \frac{\mathbb{E}[\text{profit}(a)]}{\mathbb{E}[\tau(a)]}$$

In practice, this is solved numerically. The key trade-off:
- **Higher $a$**: Larger profit per trade, but longer waiting time and fewer trades
- **Lower $a$**: More frequent trades, but smaller edge per trade and higher transaction cost drag

### 2.3 Practical Entry/Exit Rules

```
ENTRY (Long Alpha):
  IF alpha(t) < -a_entry AND half_life < remaining_time_to_expiry:
    BUY YES on Polymarket at best ask
    Target: alpha returns to mu (~ 0)

ENTRY (Short Alpha):
  IF alpha(t) > +a_entry AND half_life < remaining_time_to_expiry:
    SELL YES (or BUY NO) on Polymarket at best bid
    Target: alpha returns to mu (~ 0)

EXIT:
  IF |alpha(t)| < a_exit (convergence achieved):
    Close position → take profit
  IF |alpha(t)| > a_stop (divergence, stop-loss):
    Close position → cut loss
  IF time_to_expiry < min_holding_period:
    Close position → avoid binary resolution risk
```

### 2.4 Dynamic Threshold Calibration

Static thresholds degrade as market conditions change. Implement regime-adaptive thresholds:

**Rolling OU Estimation**: Re-estimate $(\theta, \mu, \sigma)$ on a rolling window (e.g., 2-hour window updated every 15 minutes). Compute rolling half-life and rolling equilibrium sigma:

$$\sigma_{\text{eq}} = \frac{\sigma}{\sqrt{2\theta}}$$

**Regime-Dependent Thresholds**:

| Regime | Condition | Entry Threshold | Position Size |
|--------|-----------|----------------|---------------|
| **Tight** | $\sigma_{\text{eq}} < \sigma_{\text{eq,median}}$ | $1.5 \sigma_{\text{eq}}$ | Full |
| **Normal** | $\sigma_{\text{eq}} \approx \sigma_{\text{eq,median}}$ | $2.0 \sigma_{\text{eq}}$ | Full |
| **Wide** | $\sigma_{\text{eq}} > 2 \sigma_{\text{eq,median}}$ | $2.5 \sigma_{\text{eq}}$ | Half (regime uncertainty) |

### 2.5 Position Sizing: Kelly Criterion for Binary Outcomes

For a binary outcome with edge derived from the alpha signal, the Kelly criterion gives optimal fraction of bankroll:

$$f^* = \frac{p \cdot b - q}{b}$$

where:
- $p$ = estimated probability of winning the trade (from historical convergence rate conditional on entry at threshold $a$)
- $q = 1 - p$
- $b$ = payoff odds (net profit / amount risked)

For a Polymarket convergence trade:
- If buying YES at price $P_{\text{poly}}$ with fair value $P_{\text{BL}}$, the expected edge is $P_{\text{BL}} - P_{\text{poly}}$
- The win probability $p$ is the historical frequency that $\alpha$ reverts from $a$ to $m$ before hitting stop-loss
- Apply **fractional Kelly** ($f = 0.25 f^*$ to $0.5 f^*$) to account for parameter estimation error

> [!caution] Kelly Sizing Risks
> Full Kelly is extremely aggressive for binary outcomes. Parameter estimation error in $p$ maps directly to catastrophic overbetting. Always use fractional Kelly and impose hard position limits per market (e.g., max $500 per contract).

---

## 3. Lead-Lag Analysis

### 3.1 Motivation

The $\alpha$ signal conflates two sources of mispricing:
1. **Stale pricing**: One market hasn't updated to reflect new information yet
2. **Structural mispricing**: Persistent efficiency gap due to different participant bases

Understanding which market **leads** price discovery determines whether we should trade Polymarket based on options signals (options lead) or vice versa (Polymarket leads).

### 3.2 Hasbrouck (1995) Information Shares

Model the joint price process of both markets as a vector error correction model (VECM):

$$\Delta \mathbf{p}_t = \boldsymbol{\alpha} \boldsymbol{\beta}' \mathbf{p}_{t-1} + \sum_{i=1}^{k} \mathbf{\Gamma}_i \Delta \mathbf{p}_{t-i} + \mathbf{u}_t$$

where $\mathbf{p}_t = [P_{\text{poly}}(t), P_{\text{BL}}(t)]'$, $\boldsymbol{\beta}' = [1, -1]$ (cointegrating vector), and $\mathbf{u}_t \sim \mathcal{N}(0, \Omega)$.

The **information share** of market $j$ is the proportion of the efficient price innovation variance attributable to market $j$:

$$IS_j = \frac{([\boldsymbol{\psi} \mathbf{F}]_j)^2}{\boldsymbol{\psi} \Omega \boldsymbol{\psi}'}$$

where $\boldsymbol{\psi}$ is the common-factor coefficient vector and $\mathbf{F}$ is the Cholesky factorization of $\Omega$.

Since the Cholesky decomposition depends on variable ordering, compute upper and lower bounds by trying both orderings.

### 3.3 Gonzalo-Granger (1995) Permanent-Transitory Decomposition

An alternative that does not depend on variable ordering. Decompose the price vector into a permanent (efficient price) component and a transitory (noise) component:

$$\mathbf{p}_t = \mathbf{f}_t + \mathbf{z}_t$$

where $\mathbf{f}_t = \boldsymbol{\alpha}_\perp' \mathbf{p}_t$ is the permanent component and $\boldsymbol{\alpha}_\perp$ is orthogonal to the error-correction loadings.

The **GG weight** for market $j$:

$$GG_j = \frac{\alpha_{\perp,j}}{\sum_k \alpha_{\perp,k}}$$

A higher $GG_j$ means market $j$ contributes more to long-run price discovery.

### 3.4 Cross-Correlation Function

A simpler non-parametric approach: compute the cross-correlation between returns in both markets at various leads and lags:

$$\rho(\ell) = \text{Corr}(\Delta P_{\text{poly}}(t), \Delta P_{\text{BL}}(t + \ell))$$

for $\ell = -30, -29, \ldots, 0, \ldots, +29, +30$ (minutes).

- If $\rho(\ell)$ peaks at $\ell > 0$: options lead (Polymarket responds with a lag)
- If $\rho(\ell)$ peaks at $\ell < 0$: Polymarket leads (options respond with a lag)
- If $\rho(\ell)$ peaks at $\ell = 0$: simultaneous incorporation

### 3.5 Expected Lead-Lag Structure

| Information Type | Expected Leader | Rationale |
|-----------------|----------------|-----------|
| Earnings / macro news | Options market | Institutional traders reprice options within milliseconds; Polymarket participants are slower |
| General stock movement | Options market | Continuous delta hedging keeps options fair; Polymarket is passive |
| Event-specific sentiment | Polymarket (possibly) | Polymarket traders may have direct event views not reflected in options |
| Overnight gaps | Options (at open) | Options reprice immediately at 9:30 ET; Polymarket is 24/7 but thin overnight |

### 3.6 Exploiting the Lag

If options systematically lead by $L$ minutes:

```
SIGNAL: Option-implied probability shifts by delta_BL > threshold in last L minutes
        AND Polymarket has not yet adjusted (|alpha| has widened)
ACTION: Trade Polymarket in the direction implied by the options move
EXIT:   When Polymarket catches up (alpha narrows) or after max_hold_time
```

**Key parameter**: The lag $L$ and the adjustment speed determine strategy profitability. If $L < 1$ minute, execution must be sub-second. If $L \sim 5{-}15$ minutes, manual or moderate-frequency execution is viable.

> [!note] Data Alignment
> Lead-lag analysis requires precise time alignment between markets. See the [[#Backtesting Framework]] section for the synchronization methodology using ThetaData `option/at_time/quote` and Polymarket L2 snapshots.

---

## 4. Event-Driven Mispricing Patterns

### 4.1 Event Taxonomy

Predictable events create windows where alpha behavior is non-stationary:

| Event | Time (ET) | Expected Alpha Behavior |
|-------|-----------|------------------------|
| **Market Open** | 09:30 | Large alpha spike as options reprice; Polymarket may be stale from overnight |
| **Options Expiry** | 16:00 on expiry day | Alpha compresses to zero as both converge to binary outcome |
| **FOMC Announcement** | 14:00 (8x/year) | Vol spike → B-L probability shifts rapidly; Polymarket may lag |
| **Earnings Release** | Varies (pre/post market) | Large $S$ move → complete repricing of binary probability |
| **Market Close** | 15:50-16:00 | MOC imbalances may cause brief mispricings |
| **Intraday Vol Shock** | Random | Sudden IV change → B-L shift without Polymarket response |

### 4.2 Event Study Methodology

For each event type $E$, measure $\alpha$ in a window $[t_E - \tau_{\text{pre}}, \; t_E + \tau_{\text{post}}]$:

1. **Pre-event alpha**: $\bar{\alpha}_{\text{pre}} = \text{mean}(\alpha(t))$ for $t \in [t_E - \tau_{\text{pre}}, t_E)$
2. **Event-time alpha**: $\alpha(t_E)$
3. **Post-event alpha**: $\bar{\alpha}_{\text{post}} = \text{mean}(\alpha(t))$ for $t \in (t_E, t_E + \tau_{\text{post}}]$
4. **Abnormal alpha**: $\alpha_{\text{abnormal}} = \alpha(t_E) - \bar{\alpha}_{\text{pre}}$
5. **Reversion time**: Time for $|\alpha|$ to return to pre-event levels

Aggregate across multiple instances of each event type. Test significance via bootstrap.

### 4.3 Event-Driven Strategy

```
PRE-POSITION (for predictable events):
  T-15min before FOMC: Estimate current alpha
  IF |alpha| is small: Place limit orders at expected post-event alpha levels

POST-EVENT (reactive):
  IF |alpha(t_E)| > 2 * sigma_alpha_normal:
    Enter convergence trade
    Expected reversion: alpha returns to normal within estimated tau_revert
```

### 4.4 Market Open Strategy (High Priority)

The market open at 9:30 ET is particularly promising:

- **Overnight**: Polymarket trades thinly; options markets are closed
- **Pre-market**: Options don't trade, but futures and pre-market equities price in overnight news
- **9:30 ET**: Options chains go live with updated IV. B-L probability jumps to reflect overnight information. Polymarket may not reprice for minutes.
- **Strategy**: At 9:30:05, compute $P_{\text{BL}}$ from the first options quotes. Compare to Polymarket mid. Trade the gap.

> [!warning] Execution Risk
> The first minutes of options trading have wide bid-ask spreads and unstable IV. B-L extraction from noisy early quotes may produce unreliable probabilities. Consider waiting until 9:35-9:45 for more stable inputs.

---

## 5. Sum-to-One Arbitrage (Range Markets)

### 5.1 Theoretical Basis

For a complete set of non-overlapping range markets covering the entire price space (see [[Range-Market-Strategy]]):

$$\sum_{i=1}^{N} P_i(t) = 1.00$$

where $P_i(t)$ is the Polymarket price for range $i$. This is an **accounting identity** — the underlying must close in exactly one range.

### 5.2 Tracking the Sum Deviation

Define:

$$S(t) = \sum_{i=1}^{N} P_i^{\text{ask}}(t) \quad \text{(cost to buy all)}, \qquad \hat{S}(t) = \sum_{i=1}^{N} P_i^{\text{bid}}(t) \quad \text{(proceeds from selling all)}$$

- If $\hat{S}(t) > 1 + c$: **Sell all ranges**. Guaranteed profit $= \hat{S} - 1 - c$ per unit (since exactly one range pays $1.00, and you received $\hat{S}$)
- If $S(t) < 1 - c$: **Buy all ranges**. Guaranteed profit $= 1 - S - c$ per unit (you pay $S$, receive $1.00$)

where $c$ is total execution cost (taker fees + slippage across $N$ legs).

### 5.3 Execution Considerations

- **Leg risk**: Must execute all $N$ legs atomically or near-atomically. If some legs fill and others don't, you have directional exposure, not arbitrage.
- **Typical range events**: $N = 5{-}8$ ranges. Need to hit $N$ separate order books.
- **Liquidity**: Each range market may have only $500{-}2{,}000$ of liquidity at the best level. Maximum arb size is limited by the thinnest leg.
- **Frequency**: Monitor $S(t)$ in real-time via WebSocket. Historically, pure arbs ($|S - 1| > $ execution cost) are expected to be rare but highly profitable when they occur.

### 5.4 Near-Arbitrage (Statistical Version)

Even when $|S - 1|$ is too small for pure arbitrage, deviations carry information:

- If $S > 1.01$: The market collectively overprices the event. Systematically sell the most overpriced range(s) relative to B-L.
- If $S < 0.99$: The market collectively underprices. Buy the most underpriced range(s).

This is a **statistical** strategy (not guaranteed profit) but benefits from the structural tendency of $S \to 1$.

### 5.5 Empirical Questions

- How often does $|S(t) - 1| > 0.02$ (enough to cover execution costs)?
- What is the typical magnitude and duration of sum deviations?
- Do sum deviations predict which individual ranges are most mispriced?
- Is there a time-of-day pattern to sum deviations?

> [!todo] Backtest Priority
> Sum-to-one arbitrage is the **lowest-risk strategy** in this document. It should be the first to backtest once range market L2 data is available from Telonex.

---

## 6. Multi-Signal Combination

### 6.1 Signal Universe

Beyond the raw $\alpha$ signal, several complementary signals are available:

| Signal | Definition | Source | Update Frequency |
|--------|-----------|--------|------------------|
| **$\alpha_{\text{BL}}$** | $P_{\text{poly}} - P_{\text{BL}}$ | Options chain + Polymarket | Per options quote update (~seconds) |
| **Micro-price** | Volume-weighted mid: $P_\mu = P_{\text{ask}} \frac{Q_{\text{bid}}}{Q_{\text{bid}} + Q_{\text{ask}}} + P_{\text{bid}} \frac{Q_{\text{ask}}}{Q_{\text{bid}} + Q_{\text{ask}}}$ | Polymarket L2 | Per L2 update |
| **OBI** (Order Book Imbalance) | $\frac{Q_{\text{bid}} - Q_{\text{ask}}}{Q_{\text{bid}} + Q_{\text{ask}}}$ across top $N$ levels | Polymarket L2 | Per L2 update |
| **VPIN** (Volume-Synchronized PIN) | Probability of informed trading estimated from trade flow bucketing | Polymarket trades | Per volume bucket |
| **Trade flow** | Net signed volume over rolling window: $\sum_i \text{sign}_i \cdot v_i$ | Polymarket trades | Per trade |
| **IV change** | $\Delta IV_{\text{ATM}}$ over rolling window | ThetaData options | Per options update |
| **Delta exposure** | Net delta of nearest-strike options vs Polymarket equivalent | ThetaData Greeks | Per options update |

### 6.2 Linear Combination (Weighted Signal)

Combine signals into a composite:

$$\hat{P}_{\text{fair}}(t) = w_0 + w_1 P_{\text{BL}}(t) + w_2 P_\mu(t) + w_3 \text{OBI}(t) + w_4 \text{VPIN}(t) + w_5 \text{TradeFlow}(t)$$

Estimate weights $\mathbf{w}$ by minimizing prediction error on realized outcomes:

$$\mathbf{w}^* = \arg\min_{\mathbf{w}} \sum_{j} \left( \mathbf{1}_{S_T > K_j} - \hat{P}_{\text{fair},j} \right)^2$$

This is a logistic regression problem (since the outcome is binary).

### 6.3 Principal Component Analysis

Reduce dimensionality of the signal matrix:

1. Standardize all signals to zero mean, unit variance
2. Compute PCA on the signal correlation matrix
3. If PC1 explains >60% of variance, a single factor drives mispricing
4. Trade based on the PC1 score: extreme values indicate strong aggregate mispricing

### 6.4 Walk-Forward Optimization

To avoid overfitting:

```
For each trading day D:
  1. Training window: [D-60, D-1] (60 days of historical data)
  2. Estimate signal weights w* on training window
  3. Apply w* to day D (out-of-sample)
  4. Record P&L for day D
  5. Slide window forward by 1 day

Aggregate out-of-sample P&L across all days = unbiased strategy performance estimate
```

**Minimum data requirement**: 80+ trading days (60 training + 20 test) per underlying.

> [!important] Avoiding Look-Ahead Bias
> The B-L probability must be computed using only options data available at time $t$, not future quotes. The walk-forward framework enforces this temporally, but the backtesting engine must also enforce it within each day (no peeking at future L2 snapshots).

---

## 7. Cross-Underlying Correlation

### 7.1 Common Factor Hypothesis

If mispricings across the 11 underlyings (NVDA, AAPL, MSFT, GOOGL, AMZN, META, TSLA, NFLX, PLTR, SPX, NDX) are correlated, there exists a **common mispricing factor** driven by market-wide forces (e.g., Polymarket-wide liquidity withdrawal, systematic options repricing after a macro event).

### 7.2 Factor Model

Construct the cross-sectional alpha matrix:

$$\mathbf{A}(t) = [\alpha_{\text{NVDA}}(t), \; \alpha_{\text{AAPL}}(t), \; \ldots, \; \alpha_{\text{NDX}}(t)]$$

Apply PCA to the correlation matrix of $\mathbf{A}$:

$$\mathbf{A}(t) = \mathbf{B} \mathbf{F}(t) + \boldsymbol{\varepsilon}(t)$$

where $\mathbf{F}(t)$ is the vector of principal components (common factors) and $\mathbf{B}$ is the loading matrix.

**Interpretation**:
- **PC1** ("Market mispricing factor"): If all loadings have the same sign, this captures market-wide mispricing direction
- **PC2** ("Sector rotation"): May capture tech vs. index relative mispricing
- $\boldsymbol{\varepsilon}$ ("Idiosyncratic mispricing"): Stock-specific deviations, potentially from earnings or company-specific events

### 7.3 Factor-Based Trading

When the common factor $F_1(t)$ is extreme:

$$F_1(t) > k \cdot \sigma_{F_1} \implies \text{Sell all underlyings (diversified convergence portfolio)}$$
$$F_1(t) < -k \cdot \sigma_{F_1} \implies \text{Buy all underlyings}$$

Benefits of diversified convergence:
- **Risk reduction**: Idiosyncratic risk $\varepsilon_i$ diversifies across $N$ underlyings. Portfolio variance $\propto 1/N$ for the idiosyncratic component.
- **Higher Sharpe**: The common factor is more predictable (smoother) than individual alphas.
- **Capital efficiency**: Can allocate less per-underlying while maintaining total exposure.

### 7.4 Pair-Specific Correlation

Beyond PCA, examine specific pairs:
- **NVDA-AAPL**: Both mega-cap tech; alpha likely highly correlated
- **SPX-NDX**: Index pair; alpha should be nearly identical for similar strikes
- **TSLA-PLTR**: Higher-vol names; may exhibit unique alpha dynamics

If $\alpha_{\text{NVDA}}$ and $\alpha_{\text{AAPL}}$ diverge (one positive, one negative), this signals an idiosyncratic mispricing in one of them — trade the outlier.

---

## 8. Dynamic Position Sizing and Capital Allocation

### 8.1 Kelly Criterion for Binary Outcomes with Alpha Edge

For a binary contract with:
- Fair probability: $p = P_{\text{BL}}$ (our estimate of the true probability)
- Polymarket ask price: $P_{\text{ask}}$ (our cost basis if buying YES)
- Payoff: $1.00$ if YES, $0.00$ if NO

Expected value of buying YES: $\mathbb{E}[\text{profit}] = p \cdot (1 - P_{\text{ask}}) - (1-p) \cdot P_{\text{ask}} = p - P_{\text{ask}}$

Kelly fraction:

$$f^* = \frac{p - P_{\text{ask}}}{1 - P_{\text{ask}}} = \frac{\alpha}{1 - P_{\text{ask}}}$$

where $\alpha = p - P_{\text{ask}}$ is the edge. For selling YES (buying NO), the analogous formula applies with reversed signs.

### 8.2 Volatility-Scaled Sizing

Adjust position size inversely to the recent volatility of $\alpha$:

$$\text{Size}(t) = \text{BaseSize} \times \frac{\sigma_{\text{target}}}{\sigma_{\alpha}(t)}$$

where $\sigma_{\alpha}(t)$ is the rolling standard deviation of $\alpha$ over the last $N$ observations.

**Rationale**: When $\alpha$ is volatile, there is more uncertainty about whether the current signal is genuine or noise. Reducing size during volatile periods limits losses from false signals.

### 8.3 Time-to-Expiry Adjustment

As expiry approaches, the binary contract becomes increasingly gamma-sensitive: small moves in the underlying cause large probability swings. This creates:
- **Higher potential P&L per trade** (larger moves in $P_{\text{poly}}$)
- **Higher risk per trade** (binary resolution approaches — you're betting on the final price)

Scaling function:

$$\text{ExpiryScale}(\tau) = \begin{cases} 1.0 & \text{if } \tau > 4\text{hr} \\ 0.5 + 0.5 \cdot \frac{\tau}{4\text{hr}} & \text{if } 1\text{hr} < \tau \leq 4\text{hr} \\ 0.25 & \text{if } \tau \leq 1\text{hr} \end{cases}$$

where $\tau$ is time remaining to market resolution.

### 8.4 Cross-Market Capital Allocation

Given a total capital budget $C$, allocate across the 11 underlyings based on signal quality:

$$C_i = C \times \frac{q_i}{\sum_j q_j}$$

where $q_i$ is a quality score for underlying $i$:

$$q_i = \frac{|\bar{\alpha}_i|}{\sigma_{\alpha,i}} \times \frac{1}{t_{1/2,i}} \times \text{Liquidity}_i$$

This allocates more capital to markets with:
- Higher signal-to-noise ratio ($|\bar{\alpha}| / \sigma_\alpha$)
- Faster mean reversion ($1 / t_{1/2}$)
- Better liquidity (lower execution costs)

Re-compute allocations daily or when OU parameters are re-estimated.

---

## 9. Mispricing Signal Characterization — Methodology

### 9.1 End-to-End Pipeline

```
┌──────────────────┐     ┌──────────────────┐
│ Polymarket L2    │     │ ThetaData Options │
│ (Telonex WS)     │     │ (REST / Stream)   │
│                  │     │                   │
│ bid/ask/size     │     │ Full chain NBBO   │
│ @ 1sec snapshots │     │ + Greeks + IV     │
└────────┬─────────┘     └────────┬──────────┘
         │                        │
         ▼                        ▼
   ┌───────────┐          ┌──────────────┐
   │ Mid-price │          │ B-L Pipeline │
   │ Micro-px  │          │ Vol interp   │
   │ OBI/VPIN  │          │ CDF extract  │
   └─────┬─────┘          └──────┬───────┘
         │                       │
         │    ┌──────────┐       │
         └───►│ Time     │◄──────┘
              │ Aligner  │
              │ (nearest │
              │ timestamp)│
              └─────┬────┘
                    │
                    ▼
            ┌──────────────┐
            │ alpha(t) =   │
            │ P_poly - P_BL│
            └──────┬───────┘
                   │
         ┌─────────┴─────────┐
         ▼                   ▼
  ┌─────────────┐    ┌──────────────┐
  │ Statistical │    │ Strategy     │
  │ Tests       │    │ Engine       │
  │ ADF/Hurst/  │    │ OU optimal   │
  │ VR/OU fit   │    │ stopping     │
  └─────────────┘    └──────────────┘
```

### 9.2 Signal Construction Details

**Polymarket Mid-Price**:
$$P_{\text{poly}}(t) = \frac{P_{\text{best\_bid}}(t) + P_{\text{best\_ask}}(t)}{2}$$

Use micro-price as an alternative when book is asymmetric:
$$P_\mu(t) = P_{\text{ask}} \cdot \frac{Q_{\text{bid}}}{Q_{\text{bid}} + Q_{\text{ask}}} + P_{\text{bid}} \cdot \frac{Q_{\text{ask}}}{Q_{\text{bid}} + Q_{\text{ask}}}$$

**B-L Probability**: From [[Breeden-Litzenberger-Pipeline]], using the full options chain:
1. Retrieve NBBO quotes for all strikes at target expiry (or nearest expiry with interpolation)
2. Compute call mid-prices: $C_{\text{mid}}(K_i) = (C_{\text{bid}} + C_{\text{ask}}) / 2$
3. Fit vol surface (cubic spline on IV vs. strike) per [[Vol-Surface-Fitting]]
4. Compute continuous call price function $C(K)$ from fitted IV
5. Numerically differentiate: $P_{\text{BL}}(S_T > K) = -\frac{\partial C}{\partial K} \cdot e^{rT}$

### 9.3 Noise Reduction

Raw $\alpha(t)$ is noisy due to:
- **Polymarket bid-ask bounce**: Mid-price oscillates between discrete tick levels ($0.01 resolution)
- **Options bid-ask noise**: Wide spreads on OTM options contaminate B-L extraction
- **Asynchronous updates**: Options and Polymarket don't update at the same instant

Mitigation:
- **EWMA smoothing**: $\alpha_{\text{smooth}}(t) = \lambda \alpha(t) + (1-\lambda) \alpha_{\text{smooth}}(t-1)$ with $\lambda \in [0.05, 0.2]$
- **Minimum tick filter**: Ignore alpha changes smaller than Polymarket tick size ($0.01)
- **Options staleness filter**: Discard B-L probability if the underlying options chain hasn't updated in >30 seconds (use ThetaData quote timestamps)

---

## 10. Backtesting Framework

### 10.1 Time Alignment Challenge

The core difficulty: Polymarket and options markets operate on different clocks and update frequencies.

| Property | Polymarket | Options (OPRA via ThetaData) |
|----------|-----------|------------------------------|
| **Trading hours** | 24/7 | 09:30-16:00 ET (some indices to 16:15) |
| **Update frequency** | Per order/trade (ms-level) | Per NBBO change (ms-level via streaming, or at-time snapshots) |
| **Tick size** | $0.01 (1 cent) | $0.01-$0.05 depending on premium |
| **Data source** | Telonex WebSocket / CLOB API | ThetaData Terminal REST API |
| **Timestamp format** | Unix milliseconds | ISO 8601 / milliseconds ET |

### 10.2 Synchronization Method

Use ThetaData's `option/at_time/quote` endpoint for precise point-in-time alignment:

```
For each Polymarket L2 snapshot at time t:
  1. Round t to nearest second: t_rounded
  2. Query ThetaData: GET /v3/option/at_time/quote
       ?symbol=NVDA
       &expiration=20260402
       &strike=*           (all strikes)
       &right=both
       &start_date=20260402
       &end_date=20260402
       &time_of_day={t_rounded in HH:mm:ss.SSS ET}
  3. Returns NBBO for every strike at exactly t_rounded
  4. Run B-L pipeline on the returned chain
  5. Compute alpha(t) = P_poly(t) - P_BL(t_rounded)
```

**Alternative for streaming backtest**: Use ThetaData's US Options Quote Stream for real-time NBBO updates, merged with Polymarket WebSocket via a time-ordered event queue.

### 10.3 Backtest Architecture

```python
# Pseudocode for the backtesting loop
class StatArbBacktest:
    def __init__(self, polymarket_data, options_data, strategy_params):
        self.poly = polymarket_data      # L2 snapshots, trades
        self.opts = options_data          # At-time option chains
        self.params = strategy_params     # OU thresholds, sizing, etc.
        self.positions = {}               # Current positions
        self.pnl = []                     # Trade-level P&L

    def run(self):
        for t in self.aligned_timestamps():
            # 1. Compute signals
            p_poly = self.poly.mid_price(t)
            p_bl = self.opts.bl_probability(t)
            alpha = p_poly - p_bl

            # 2. Update OU parameters (rolling window)
            if self.should_recalibrate(t):
                self.ou_params = self.fit_ou(self.alpha_history[-self.window:])

            # 3. Check entry/exit conditions
            for market_id in self.active_markets:
                pos = self.positions.get(market_id)
                if pos is None:
                    self.check_entry(market_id, alpha, t)
                else:
                    self.check_exit(market_id, alpha, t)

            # 4. Record state
            self.alpha_history.append(alpha)

    def check_entry(self, market_id, alpha, t):
        threshold = self.params.entry_k * self.ou_params.sigma_eq
        if abs(alpha) > threshold:
            size = self.compute_size(alpha, t)
            side = 'sell' if alpha > 0 else 'buy'
            cost = self.simulate_execution(market_id, side, size, t)
            self.positions[market_id] = Position(side, size, alpha, t, cost)

    def simulate_execution(self, market_id, side, size, t):
        """Walk the L2 book to simulate realistic fill with slippage."""
        book = self.poly.orderbook(market_id, t)
        filled, avg_price = book.simulate_market_order(side, size)
        return avg_price  # Includes slippage from walking the book
```

### 10.4 Execution Simulation Realism

The NVDA POC demonstrated that naive execution assumptions are fatal. The backtest must simulate:

1. **L2 book walking**: Large orders consume multiple price levels. Use actual L2 depth snapshots.
2. **Latency**: Assume 50-200ms between signal generation and order arrival at Polymarket. Use the book state at $t + \text{latency}$, not at $t$.
3. **Adverse selection**: After a trade, the book may move against you. Measure post-trade price impact using historical trade data.
4. **Maker vs. taker**: Resting limit orders avoid taker fees but risk non-fill or adverse selection. Model both execution modes.
5. **Position limits**: Polymarket may have position limits or liquidity constraints. Cap simulated position sizes at realistic levels.

### 10.5 Performance Metrics

| Metric | Formula | Target |
|--------|---------|--------|
| **Total P&L** | $\sum_i \text{PnL}_i$ | > 0 (net profitable) |
| **Sharpe Ratio** | $\frac{\bar{r}}{\sigma_r} \sqrt{252}$ (annualized) | > 1.5 |
| **Win Rate** | $\frac{\text{winning trades}}{\text{total trades}}$ | > 55% |
| **Profit Factor** | $\frac{\sum \text{wins}}{\sum |\text{losses}|}$ | > 1.5 |
| **Max Drawdown** | Maximum peak-to-trough decline | < 20% of capital |
| **Average Trade Duration** | Mean holding time | Consistent with OU half-life |
| **Alpha Decay** | P&L by entry alpha decile | Higher alpha → higher P&L (monotonic) |
| **Execution Cost Ratio** | $\frac{\text{total costs}}{\text{gross profit}}$ | < 30% |

---

## 11. Implementation Priority and Roadmap

### Phase 1: Signal Characterization (Current Priority)

- [ ] Collect 20+ days of aligned Polymarket + options data for NVDA
- [ ] Compute $\alpha(t)$ at 1-minute frequency
- [ ] Run ADF, Hurst, variance ratio tests
- [ ] Fit OU process and estimate half-life
- [ ] Characterize distribution (moments, tails, regimes)
- [ ] Document findings in [[NVDA-POC-Results]]

### Phase 2: Lead-Lag and Event Studies

- [ ] Implement Hasbrouck information shares
- [ ] Compute cross-correlation function at multiple lags
- [ ] Event study around market open (highest expected signal)
- [ ] Quantify options-to-Polymarket lag across underlyings

### Phase 3: Strategy Backtesting

- [ ] Implement OU optimal stopping strategy in backtesting engine (see [[Capital-Efficiency-and-Edge-Cases]])
- [ ] Backtest convergence trading with realistic execution simulation
- [ ] Backtest sum-to-one arbitrage on range markets
- [ ] Walk-forward multi-signal combination

### Phase 4: Multi-Underlying Expansion

- [ ] Extend to all 11 underlyings
- [ ] PCA of cross-underlying alpha
- [ ] Factor-based diversified convergence portfolio
- [ ] Cross-market capital allocation optimization

---

## Related Notes

- [[Breeden-Litzenberger-Pipeline]] — Mathematical foundation for $P_{\text{BL}}$ extraction
- [[Core-Market-Making-Strategies]] — Traditional MM strategies adapted for Polymarket
- [[Risk-Neutral-vs-Physical-Probabilities]] — Why $P^{\mathbb{Q}} \neq P^{\mathbb{P}}$ and implications for alpha
- [[Range-Market-Strategy]] — Sum-to-one arbitrage and range pricing details
- [[NVDA-POC-Results]] — Lessons from the initial $18 loss POC
- [[Polymarket-Data-API]] — CLOB API endpoints for orderbook, prices, WebSocket streams
- [[ThetaData-Options-API]] — Options chain retrieval, at-time quotes, Greeks, streaming
- [[Capital-Efficiency-and-Edge-Cases]] — Position limits, margin, and edge case handling
- [[Vol-Surface-Fitting]] — IV interpolation methods feeding into B-L pipeline

---

## References

1. Bertram, W.K. (2010). "Analytic solutions for optimal statistical arbitrage trading." *Physica A: Statistical Mechanics and its Applications*, 389(11), 2234-2243.
2. Breeden, D.T. & Litzenberger, R.H. (1978). "Prices of state-contingent claims implicit in option prices." *Journal of Business*, 51(4), 621-651.
3. Hasbrouck, J. (1995). "One security, many markets: Determining the contributions to price discovery." *Journal of Finance*, 50(4), 1175-1199.
4. Gonzalo, J. & Granger, C. (1995). "Estimation of common long-memory components in cointegrated systems." *Journal of Business & Economic Statistics*, 13(1), 27-35.
5. Lo, A. & MacKinlay, A.C. (1988). "Stock market prices do not follow random walks: Evidence from a simple specification test." *Review of Financial Studies*, 1(1), 41-66.
6. Easley, D., Lopez de Prado, M., & O'Hara, M. (2012). "Flow toxicity and liquidity in a high-frequency world." *Review of Financial Studies*, 25(5), 1457-1493. (VPIN methodology)
7. Kelly, J.L. (1956). "A new interpretation of information rate." *Bell System Technical Journal*, 35(4), 917-926.
8. Elliott, R.J., van der Hoek, J., & Malcolm, W.P. (2005). "Pairs trading." *Quantitative Finance*, 5(3), 271-276.
