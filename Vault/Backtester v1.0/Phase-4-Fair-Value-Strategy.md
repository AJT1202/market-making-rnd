---
title: "Phase 4: Fair Value Integration and Strategy Interface"
created: 2026-04-02
status: plan
phase: 4
milestone: "Backtester v1.0"
tags:
  - backtesting
  - fair-value
  - breeden-litzenberger
  - strategy
  - market-making
  - architecture
  - specification
related:
  - "[[Engine-Architecture-Plan]]"
  - "[[Breeden-Litzenberger-Pipeline]]"
  - "[[Vol-Surface-Fitting]]"
  - "[[Core-Market-Making-Strategies]]"
  - "[[Inventory-and-Risk-Management]]"
  - "[[Risk-Neutral-vs-Physical-Probabilities]]"
---

# Phase 4: Fair Value Integration and Strategy Interface

> **Purpose**: Build Layer 3 (Fair Value) and Layer 5.5 (Strategy Interface) of the backtesting engine. This phase connects options-derived probability estimation to trading decisions, transforming raw market data into actionable quotes.
>
> **Depends on**: Phase 1-3 (DataProvider, Dual-Book State, Execution Simulator). This phase consumes the unified timeline and book state from earlier phases and produces order actions that flow into the execution simulator.
>
> **Produces**: A pluggable fair value computation layer, a strategy callback interface, and three reference strategies for validation and benchmarking.

---

## Table of Contents

1. [Breeden-Litzenberger Pipeline Specification](#1-breeden-litzenberger-pipeline-specification)
2. [IV Fitting Decision: SABR First](#2-iv-fitting-decision-sabr-first)
3. [Expiry Mismatch Handling](#3-expiry-mismatch-handling)
4. [Fair Value Provider Interface](#4-fair-value-provider-interface)
5. [Strategy Interface](#5-strategy-interface)
6. [Reference Strategy Specifications](#6-reference-strategy-specifications)
7. [Strategy-Engine Integration](#7-strategy-engine-integration)
8. [Configuration](#8-configuration)
9. [Testing Strategy](#9-testing-strategy)
10. [Task Breakdown](#10-task-breakdown)

---

## 1. Breeden-Litzenberger Pipeline Specification

The B-L pipeline is the mathematical core of the system. It extracts model-free risk-neutral probabilities from listed options chains to price Polymarket binary contracts. The full mathematical foundation is in [[Breeden-Litzenberger-Pipeline]]; this section specifies the concrete algorithm as it integrates with DataProvider.

### 1.1 Pipeline Steps

The pipeline executes six steps in sequence. Each step has defined inputs, outputs, and failure modes.

```
Step 1: Ingest       Step 2: Clean       Step 3: Fit IV
options chain   -->  & unify via    -->  smile (SABR
from DataProvider    put-call parity     or SVI)

Step 4: Reprice     Step 5: Finite      Step 6: Integrate
on fine strike  --> differences     --> to get P(S_T > K)
grid via B-S        for density q(K)    and validate
```

#### Step 1: Data Ingestion from DataProvider

The B-L pricer requests options chain data through the DataProvider's `OptionsChainStore`. For backtesting, options data is pre-loaded as Parquet files from ThetaData (see [[Engine-Architecture-Plan]] Section 2).

```python
@dataclass(frozen=True)
class OptionsQuote:
    """Single option quote from ThetaData."""
    strike_cents: int          # Strike in cents (e.g., 12000 = $120.00)
    right: str                 # "C" or "P"
    expiry_us: int             # Expiry timestamp (microseconds)
    bid: float                 # Best bid price
    ask: float                 # Best ask price
    bid_iv: float              # Bid implied volatility
    ask_iv: float              # Ask implied volatility
    mid_iv: float              # Mid implied volatility
    open_interest: int         # Open interest
    underlying_price: float    # Underlying price at snapshot time
    timestamp_us: int          # Snapshot timestamp
```

The pricer queries: "Give me all options quotes for `ticker` at expiry `T` as of timestamp `t`." DataProvider returns the most recent snapshot at or before `t`.

#### Step 2: Data Cleaning and Put-Call Parity Unification

This step converts raw quotes into a clean set of OTM implied volatilities as a function of strike. The algorithm follows [[Breeden-Litzenberger-Pipeline]] Section 4.

```python
def clean_and_unify(
    quotes: list[OptionsQuote],
    forward_price: float,
    r: float,
    tau: float,
) -> list[tuple[float, float]]:
    """
    Clean raw options quotes and return (strike, mid_iv) pairs.

    Returns: sorted list of (strike, implied_volatility) for OTM options.
    """
```

**Filtering rules** (applied in order):

| Filter | Criterion | Rationale |
|--------|-----------|-----------|
| Zero bid | `bid <= 0` | No real market; quote is stale or indicative |
| Zero OI | `open_interest == 0` | No positions; quote may be auto-generated |
| Wide spread | `(ask - bid) / mid > 0.50` | Microstructure noise dominates |
| IV outlier | `abs(iv - neighbor_median) > 2 * neighbor_std` | Statistical outlier relative to local smile |
| Stale quote | `timestamp_us < threshold` | Quote older than staleness window |

**OTM selection and unification**:

1. Compute the implied forward: $\hat{F} = K^* + e^{rT}[C(K^*) - P(K^*)]$ where $K^*$ is the strike with smallest $|C - P|$.
2. For $K < \hat{F}$: use **put** mid-IV (OTM puts are more liquid).
3. For $K > \hat{F}$: use **call** mid-IV (OTM calls are more liquid).
4. For $K \approx \hat{F}$ (within one strike width): average put and call mid-IV.
5. Sort by strike ascending.

**Minimum data requirement**: At least 5 valid strikes spanning both sides of the forward. If fewer pass filters, fall back to `BlackScholesFairValue` and log a warning.

#### Step 3: IV Smile Fitting

Fit the cleaned IV data with SABR (primary) or SVI (configurable). See [Section 2](#2-iv-fitting-decision-sabr-first) for the decision rationale.

```python
@dataclass
class SmileFit:
    """Result of IV smile fitting."""
    method: str                    # "sabr" or "svi"
    params: dict[str, float]       # Model parameters
    forward: float                 # Implied forward price
    tau: float                     # Time to expiry in years
    r: float                       # Risk-free rate
    strike_range: tuple[float, float]  # (K_min, K_max) of valid domain
    residual_rmse: float           # Root mean squared error of fit (in vol points)
    is_valid: bool                 # Whether density validation passed

    def iv(self, strike: float) -> float:
        """Evaluate fitted IV at arbitrary strike."""
        ...
```

**SABR fitting procedure** (with $\beta = 1$ fixed):

1. Initialize $\alpha$ from ATM IV: $\alpha_0 = \sigma_{\text{ATM}} \cdot F^{1-\beta}$.
2. Initialize $\rho = -0.3$ (typical equity skew), $\nu = 0.5$ (moderate vol-of-vol).
3. Optimize $(\alpha, \rho, \nu)$ via SLSQP to minimize weighted sum of squared IV errors, with ATM anchoring (force exact ATM match at each iteration).
4. Bounds: $\alpha > 0$, $-0.99 < \rho < 0.99$, $0 < \nu < 5$.
5. If optimization fails to converge within 50 iterations, use the initial parameters (which already match ATM exactly).

**SVI fitting procedure** (alternative):

1. Initialize using quasi-explicit method: fix $\rho = 0$, $m = 0$, solve 3-parameter subproblem.
2. Optimize all 5 parameters $(a, b, \rho, m, \sigma)$ via SLSQP.
3. Enforce constraints: $b \geq 0$, $|\rho| < 1$, $\sigma > 0$, $a + b\sigma\sqrt{1-\rho^2} \geq 0$, Lee bounds $b(1+\rho) \leq 2$ and $b(1-\rho) \leq 2$.

#### Step 4: Reprice on Fine Grid

Evaluate the fitted IV on a uniformly spaced strike grid and convert back to Black-Scholes call prices.

```python
def reprice_on_grid(
    fit: SmileFit,
    n_points: int = 400,
    extension: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Reprice on fine grid.

    Args:
        fit: Fitted smile parameters.
        n_points: Number of grid points (default 400).
        extension: Extend grid by this fraction beyond fitted range.

    Returns:
        (strikes, call_prices) arrays of length n_points.
    """
```

- Grid range: extend 5% beyond the fitted strike range in each direction to reduce boundary effects.
- At each grid point $K_j$: compute $\hat{\sigma}(K_j)$ from the fit, then $\hat{C}(K_j) = \text{BS}_\text{call}(S, K_j, \tau, r, \hat{\sigma}(K_j))$.
- The grid spacing is $\Delta K = (K_\text{max} - K_\text{min}) / N$.

#### Step 5: Finite Differences for Density

Apply the central difference formula on the smooth call price curve to extract the risk-neutral density:

$$\hat{q}(K_j) = e^{rT} \frac{\hat{C}(K_{j+1}) - 2\hat{C}(K_j) + \hat{C}(K_{j-1})}{(\Delta K)^2}$$

This is the Breeden-Litzenberger formula in discrete form. The density is only computed for interior grid points ($j = 1, \ldots, N-1$).

#### Step 6: Integration and Validation

Compute the survival function (exceedance probability) by integrating the density from right to left:

$$P(S_T > K_j) = \sum_{i=j}^{N-1} \hat{q}(K_i) \cdot \Delta K$$

Equivalently, use the first-derivative shortcut directly on the smooth call prices:

$$P(S_T > K_j) \approx -e^{rT} \frac{\hat{C}(K_{j+1}) - \hat{C}(K_{j-1})}{2 \Delta K}$$

Both methods should agree; use the first-derivative method as primary (less noise amplification) and the density integration as a cross-check.

**Validation checks** (from [[Breeden-Litzenberger-Pipeline]] Section 2.3):

| Check | Expected | Tolerance | Action if Failed |
|-------|----------|-----------|------------------|
| $\hat{q}(K) \geq 0\ \forall K$ | Non-negative density | Allow up to 3 points with $q < -10^{-6}$ | Clamp negatives to zero, re-normalize |
| $\int \hat{q}(K) \, dK \approx 1$ | Integrates to 1 | Within 2% | Normalize by dividing by integral |
| $E^{\mathbb{Q}}[S_T] \approx F$ | Mean equals forward | Within 1% | Log warning; check forward calculation |
| $P(S_T > K)$ monotone in $K$ | Decreasing | Strict | Isotonic regression to enforce |

If more than 10% of density points are negative, the fit is rejected and the pricer falls back to `BlackScholesFairValue` for this timestamp.

### 1.2 Recomputation Policy

The strategy controls when to trigger a B-L recomputation. The pricer caches results and serves them until invalidated. Three trigger modes (configurable, can combine):

| Trigger | Condition | Default |
|---------|-----------|---------|
| **Time-based** | Every $\Delta t$ seconds since last computation | 300s (5 minutes) |
| **Move-based** | Underlying price moves more than $\Delta S$ from last computation price | 0.5% of underlying |
| **Event-based** | New options chain snapshot arrives from DataProvider | On new data |

The strategy calls `fair_value_provider.needs_recompute(current_price, current_time) -> bool` to check, then calls `fair_value_provider.recompute(...)` when needed. Between recomputations, the pricer interpolates using the cached CDF and the current underlying price (delta-adjusted):

$$P_\text{approx}(S_T > K; S_\text{new}) \approx P_\text{cached}(S_T > K; S_\text{old}) + \Delta_\text{binary} \cdot (S_\text{new} - S_\text{old})$$

where $\Delta_\text{binary}$ is the binary option's delta at strike $K$ from the cached fit. This avoids re-running the full pipeline on every tick.

### 1.3 Caching Architecture

```python
@dataclass
class BLCacheEntry:
    """Cached result of one B-L computation."""
    timestamp_us: int                      # When computed
    underlying_price: float                # Underlying at computation time
    forward_price: float                   # Implied forward
    expiry_us: int                         # Options expiry used
    fit: SmileFit                          # IV fit parameters
    strike_grid: np.ndarray                # Fine grid strikes
    cdf_grid: np.ndarray                   # P(S_T > K) at each grid point
    density_grid: np.ndarray               # q(K) at each grid point
    validation: dict[str, bool]            # Which checks passed
    interpolation_method: str              # "direct" | "variance_linear" | "bs_bridge"
    expiry_distance_days: float            # Distance to nearest options expiry

    def lookup(self, strike: float) -> float:
        """Interpolate P(S_T > K) from cached grid."""
        return float(np.interp(strike, self.strike_grid[::-1],
                               self.cdf_grid[::-1]))
```

The cache holds one entry per expiry slice. For variance-linear interpolation (Section 3), two entries are held (bracketing expiries) and the interpolated result is computed on demand.

---

## 2. IV Fitting Decision: SABR First

### 2.1 Recommendation

**Implement SABR first, SVI second.** Both will be available behind the same interface, but SABR is the default for initial development and validation.

### 2.2 Rationale

The decision is driven by our specific use case: **short-dated equity options (0-5 DTE)** for pricing binary contracts on Polymarket.

| Factor | SABR | SVI | Winner |
|--------|------|-----|--------|
| Short-maturity performance | Good; even with 2 fewer params | "Does not work well for short maturities" (empirical) | **SABR** |
| Parameters to calibrate | 3 (with $\beta$ fixed) | 5 | **SABR** (simpler, faster, less overfitting) |
| ATM anchoring | Natural; $\alpha$ pinned to ATM | Requires constrained optimization | **SABR** |
| Theoretical motivation | Stochastic vol model with analytic approx | Phenomenological (Heston-inspired shape) | **SABR** |
| Density positivity | Hagan approx can violate at low strikes | Can enforce via $g(k) \geq 0$ constraint | **SVI** |
| Surface consistency | Per-slice only | SSVI provides cross-maturity consistency | **SVI** (for multi-expiry) |
| Implementation complexity | Moderate (Hagan formula + Obloj correction) | Moderate (non-convex 5-param optimization) | Tie |

The empirical evidence from [[Vol-Surface-Fitting]] Section 5.2 is decisive: on SPX options with VIX at 11.32 and 1-week expiry, "SVI does not work well for short maturities" while "SABR ($\beta = 1$) performed much better, even though it has two less parameters."

### 2.3 SABR Configuration Defaults

```python
SABR_DEFAULTS = {
    "beta": 1.0,           # Log-normal backbone (equity convention)
    "use_obloj": True,     # Obloj correction for better OTM accuracy
    "alpha_init": None,    # Derived from ATM IV (auto)
    "rho_init": -0.3,      # Typical equity skew
    "nu_init": 0.5,        # Moderate vol-of-vol
    "max_iter": 50,        # Calibration iterations
    "atm_weight": 5.0,     # Weight multiplier for ATM point in objective
}
```

### 2.4 SVI as Second Priority

SVI should be implemented after SABR as an alternative for:
- Longer-dated options (5+ DTE) where its 5-parameter flexibility fits better.
- Multi-expiry interpolation via SSVI, needed for [[#3. Expiry Mismatch Handling|expiry mismatch]] when using the full vol surface approach.
- A/B comparison: run both fitters in parallel during backtesting to quantify the fair value difference.

### 2.5 Tail Handling

For strikes beyond the fitted domain, use **log-normal tail extrapolation** as the default (simpler, adequate for near-ATM binaries). GPD tails are a future enhancement for deep OTM contracts.

From [[Breeden-Litzenberger-Pipeline]] Section 3.4: "Tail handling is rarely the binding constraint for near-ATM binary pricing" since Polymarket strikes are typically within a few percent of current price.

Log-normal extrapolation procedure:
1. At the boundary strike $K_b$, record the fitted IV $\sigma_b$ and its derivative $\sigma'_b$.
2. For $K > K_\text{max}$ (right tail): hold IV constant at $\sigma(K_\text{max})$ and let the B-S formula handle the natural decay.
3. For $K < K_\text{min}$ (left tail): hold IV constant at $\sigma(K_\text{min})$.
4. The resulting density tails are log-normal by construction, matching the boundary density value continuously.

---

## 3. Expiry Mismatch Handling

### 3.1 The Problem

Polymarket markets resolve on arbitrary calendar days. Listed options expire on specific dates (Fridays for weeklies, third Friday for monthlies). When the Polymarket resolution date $T_P$ does not match any options expiry, we cannot directly apply B-L extraction. See [[Breeden-Litzenberger-Pipeline]] Section 7 for full discussion.

### 3.2 Variance-Linear Interpolation (Default Method)

This is the recommended approach from [[Breeden-Litzenberger-Pipeline]] Section 7, Solution 2. It uses options chains from the two expiries that bracket the Polymarket date.

**Setup**: Let $T_1 < T_P < T_2$ be the bracketing options expiries.

**Algorithm**:

1. Run the B-L pipeline separately for expiry $T_1$ and $T_2$, obtaining fitted IV smiles $\sigma_1(K)$ and $\sigma_2(K)$.

2. At each strike $K$, compute total implied variance for each expiry:
   $$w_1(K) = \sigma_1^2(K) \cdot T_1, \quad w_2(K) = \sigma_2^2(K) \cdot T_2$$

3. Interpolate total variance linearly in time to the Polymarket date:
   $$w_P(K) = \frac{(T_2 - T_P) \cdot w_1(K) + (T_P - T_1) \cdot w_2(K)}{T_2 - T_1}$$

4. Convert back to implied volatility:
   $$\sigma_P(K) = \sqrt{w_P(K) / T_P}$$

5. Compute the binary probability using Black-Scholes with the interpolated smile:
   $$P(S_{T_P} > K) = \Phi\!\left(\frac{\ln(F/K) + \frac{1}{2} w_P(K)}{\sqrt{w_P(K)}}\right)$$

**Why variance-linear?** Total implied variance $w = \sigma^2 T$ is the natural quantity for interpolation because:
- Calendar spread arbitrage freedom requires $w$ to be non-decreasing in $T$ at each $K$.
- Linear interpolation in $w$ preserves this monotonicity if both endpoints satisfy it.
- Interpolating raw IV (not total variance) would violate no-arbitrage constraints.

### 3.3 DataProvider Integration

The `BreedenLitzenbergerFairValue` provider must coordinate with DataProvider to:

1. **Identify bracketing expiries**: Query DataProvider for available options expiry dates, find the two nearest to $T_P$.
2. **Fetch both chains**: Request options chain snapshots for both $T_1$ and $T_2$.
3. **Run dual pipeline**: Execute Steps 1-5 of the B-L pipeline independently for each expiry.
4. **Interpolate**: Apply variance-linear interpolation at the Polymarket strike.
5. **Record metadata**: Log which expiries were used, the interpolation weights, and $T_P - T_1$ and $T_2 - T_P$ distances.

```python
def compute_with_interpolation(
    self,
    polymarket_expiry_us: int,
    option_expiry_1_us: int,
    option_expiry_2_us: int,
    chain_1: list[OptionsQuote],
    chain_2: list[OptionsQuote],
    underlying_price: float,
    strike: float,
) -> tuple[float, dict]:
    """
    Compute P(S > K) at polymarket_expiry via variance-linear interpolation.

    Returns:
        (probability, metadata_dict)
    """
```

### 3.4 Lucky Cases: No Interpolation Needed

For SPX/NDX, options expire Mon/Wed/Fri (0DTE), so most Polymarket dates have an exact match. For single-name stocks, weekly Friday expiries cover Friday-resolving markets. The engine should detect exact matches and skip interpolation:

```python
def select_method(self, polymarket_expiry_us: int,
                  available_expiries: list[int]) -> str:
    """Select interpolation method based on expiry alignment."""
    exact = [e for e in available_expiries
             if abs(e - polymarket_expiry_us) < 3600 * 1_000_000]  # within 1 hour
    if exact:
        return "direct"  # No interpolation needed

    bracket = self._find_bracket(polymarket_expiry_us, available_expiries)
    if bracket:
        return "variance_linear"

    nearest = min(available_expiries,
                  key=lambda e: abs(e - polymarket_expiry_us))
    return "bs_bridge"  # Fallback: nearest expiry + B-S transition kernel
```

### 3.5 Backtesting Audit Requirement

From [[Breeden-Litzenberger-Pipeline]] Section 7: "The backtesting engine must record WHICH interpolation method was used for each fair value computation." Every `BLCacheEntry` stores `interpolation_method` and `expiry_distance_days` so that post-hoc analysis can segment accuracy by method.

---

## 4. Fair Value Provider Interface

### 4.1 Protocol Definition

All fair value providers implement the same protocol, matching the `FairValuePricer` from [[Engine-Architecture-Plan]] Section 8.2 but extended with richer return types and lifecycle methods.

```python
from typing import Protocol, Optional
from dataclasses import dataclass
from enum import Enum


class FairValueMethod(Enum):
    BLACK_SCHOLES = "black_scholes"
    BREEDEN_LITZENBERGER = "breeden_litzenberger"
    MICRO_PRICE = "micro_price"


@dataclass(frozen=True)
class FairValue:
    """
    Result of a fair value computation.

    All probabilities are in basis points (0-10000) for integer arithmetic
    compatibility with the engine. 7200 bps = 72.00%.
    """
    probability_bps: int           # P(S_T > K) in basis points [0, 10000]
    confidence: float              # 0.0-1.0, how much to trust this estimate
    method: FairValueMethod        # Which provider produced this
    stale: bool                    # True if using cached/interpolated value
    timestamp_us: int              # When this was computed
    metadata: dict                 # Provider-specific details

    @property
    def yes_price_ticks(self) -> int:
        """YES fair value in Polymarket ticks (0-100)."""
        return (self.probability_bps + 50) // 100

    @property
    def no_price_ticks(self) -> int:
        """NO fair value in Polymarket ticks (0-100)."""
        return 100 - self.yes_price_ticks

    @property
    def probability(self) -> float:
        """P(S_T > K) as a float in [0, 1]."""
        return self.probability_bps / 10000.0


class FairValueProvider(Protocol):
    """Protocol that all fair value providers must implement."""

    def initialize(
        self,
        strikes: list[int],
        expiry_us: int,
        config: dict,
    ) -> None:
        """
        One-time initialization before simulation starts.

        Args:
            strikes: List of active Polymarket strikes.
            expiry_us: Polymarket resolution timestamp.
            config: Provider-specific configuration.
        """
        ...

    def compute(
        self,
        underlying_price_cents: int,
        strike: int,
        timestamp_us: int,
    ) -> FairValue:
        """
        Compute fair value for a single strike.

        This is the hot-path method called on every strategy update.
        Implementations should use caching aggressively.

        Args:
            underlying_price_cents: Current underlying price in cents.
            strike: Polymarket strike price (integer dollars).
            timestamp_us: Current simulation time.

        Returns:
            FairValue with probability and metadata.
        """
        ...

    def needs_recompute(
        self,
        underlying_price_cents: int,
        timestamp_us: int,
    ) -> bool:
        """
        Check whether the provider needs to re-run its pipeline.

        Called by the strategy to decide whether to trigger recomputation.
        """
        ...

    def recompute(
        self,
        underlying_price_cents: int,
        timestamp_us: int,
        options_chain: Optional[list] = None,
    ) -> None:
        """
        Re-run the full computation pipeline.

        For B-L: re-fit the IV smile and recompute the CDF.
        For B-S: update tau (trivial).
        For micro-price: no-op (computed on each call).
        """
        ...
```

### 4.2 BlackScholesFairValue

The simplest provider. Computes $P(S_T > K) = \Phi(d_2)$ using a fixed implied volatility. This is what the POC used (see `Code/Telonex testing/src/fair_value.py`).

```python
class BlackScholesFairValue:
    """
    Black-Scholes binary call pricer.

    Fast but crude: uses a single IV for all strikes, ignoring the smile.
    Suitable as a fallback when options chain data is unavailable or
    when B-L fitting fails.

    Configuration:
        sigma: float = 0.50    # Annualized implied volatility
        r: float = 0.0         # Risk-free rate (0 for short-dated)
    """
```

**Compute cost**: O(1) per call. No caching needed.

**When to use**:
- As the fallback when B-L fitting fails (too few strikes, density validation failure).
- For quick prototyping and strategy development before options data is integrated.
- As the control arm in A/B tests against B-L.

### 4.3 BreedenLitzenbergerFairValue

The full B-L pipeline from Section 1.

```python
class BreedenLitzenbergerFairValue:
    """
    Full Breeden-Litzenberger pipeline.

    Pipeline: fetch chain -> clean/filter -> fit IV smile (SABR/SVI) ->
    reprice on fine grid -> finite differences -> P(S_T > K).

    Configuration:
        iv_method: str = "sabr"           # "sabr" or "svi"
        grid_points: int = 400            # Fine grid resolution
        recompute_interval_s: int = 300   # Time-based recompute trigger
        recompute_move_pct: float = 0.005 # Move-based recompute trigger
        min_strikes: int = 5              # Minimum strikes for valid fit
        tail_method: str = "lognormal"    # "lognormal" or "gpd"
        sabr_config: dict = SABR_DEFAULTS
    """
```

**Compute cost**: O(N) for full pipeline (N = grid points), O(1) for cached lookup.

**`needs_recompute` logic**:
```python
def needs_recompute(self, underlying_price_cents, timestamp_us) -> bool:
    if self._cache is None:
        return True
    dt = (timestamp_us - self._cache.timestamp_us) / 1_000_000
    if dt > self.config["recompute_interval_s"]:
        return True
    price_move = abs(underlying_price_cents / 100.0
                     - self._cache.underlying_price)
    if price_move / self._cache.underlying_price > self.config["recompute_move_pct"]:
        return True
    return False
```

### 4.4 MicroPriceFairValue

A complementary signal derived from the Polymarket orderbook itself, not from options.

```python
class MicroPriceFairValue:
    """
    Orderbook-derived micro-price fair value.

    micro_price = (bid * ask_size + ask * bid_size) / (bid_size + ask_size)

    This is NOT a probability estimate; it's the liquidity-weighted
    midpoint of the Polymarket book. Useful as:
    - A real-time signal when options data is stale.
    - A complement to B-L for detecting Polymarket-specific dynamics.
    - A benchmark for "what does the Polymarket book itself imply?"

    Configuration:
        depth_levels: int = 3   # How many levels to include in weighting
    """
```

**Compute cost**: O(1) per call. Requires current book state from DataProvider.

**Interface note**: The `compute` method takes `underlying_price_cents` for protocol compatibility, but the micro-price provider ignores it and reads from the book state instead. The `strike` parameter selects which market's book to read.

### 4.5 FairValueManager

Orchestrates multiple providers and enforces cross-strike monotonicity.

```python
class FairValueManager:
    """
    Orchestrates fair value computation across all strikes.

    Responsibilities:
    1. Select and call the appropriate FairValueProvider.
    2. Enforce monotonicity: P(S > K1) >= P(S > K2) for K1 < K2.
    3. Provide both YES and NO fair values.
    4. Cache results for the current timestamp.
    5. Fall back to BlackScholes if primary provider fails.
    """

    def __init__(
        self,
        primary_provider: FairValueProvider,
        fallback_provider: FairValueProvider,  # Always BlackScholesFairValue
        strikes: list[int],
        expiry_us: int,
    ):
        ...

    def update(
        self,
        underlying_price_cents: int,
        timestamp_us: int,
        options_chain: Optional[list] = None,
    ) -> dict[int, FairValue]:
        """
        Recompute fair values for all strikes if needed.

        Steps:
        1. Check if primary provider needs recompute.
        2. If yes and options_chain available, trigger recompute.
        3. Compute fair values for all strikes.
        4. Enforce monotonicity across strikes.
        5. Return dict[strike -> FairValue].
        """
        ...

    def _enforce_monotonicity(
        self, values: dict[int, FairValue]
    ) -> dict[int, FairValue]:
        """
        Ensure P(S > K1) >= P(S > K2) for K1 < K2.

        Uses isotonic regression (pool adjacent violators algorithm)
        for a principled monotonicity fix, rather than simple averaging.
        """
        ...
```

### 4.6 Error Handling

| Failure Mode | Detection | Recovery |
|-------------|-----------|----------|
| Too few strikes pass filters | `len(clean_quotes) < min_strikes` | Fall back to `BlackScholesFairValue` |
| SABR/SVI optimization fails | Optimizer does not converge | Use initial parameters (ATM-anchored) |
| Negative density > 10% | Validation check | Reject fit, fall back to B-S |
| $\int q \, dK$ far from 1 | `abs(integral - 1.0) > 0.05` | Normalize; if > 0.10, reject fit |
| Options data unavailable | DataProvider returns empty | Use B-S with configured default sigma |
| Stale options data | `timestamp_us - chain_ts > staleness_threshold` | Use cached B-L; set `stale=True` in result |

Every fallback is logged to the audit journal with the reason, so post-hoc analysis can quantify how often each failure mode occurs.

---

## 5. Strategy Interface

### 5.1 Design Principles

The strategy interface bridges fair value computation with order generation. It is designed around these principles:

1. **Callback-driven**: The engine pushes events to the strategy; the strategy never polls.
2. **Stateful**: Strategies maintain their own state (inventory tracking, signal history, etc.).
3. **Access-rich**: Strategies can query DataProvider and FairValueProvider on demand.
4. **Action-oriented**: Strategies return concrete order actions, not hints or signals.
5. **Deterministic**: Given the same event sequence and initial state, the strategy produces the same actions.

### 5.2 Abstract Base Class

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderActionType(Enum):
    """Types of order actions a strategy can return."""
    SUBMIT = "SUBMIT"          # Place a new order
    CANCEL = "CANCEL"          # Cancel a resting order by ID
    AMEND = "AMEND"            # Modify price/size of a resting order


class TokenSide(Enum):
    """Which token to trade."""
    YES = "YES"
    NO = "NO"


class TradeSide(Enum):
    """Direction of the trade."""
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class OrderAction:
    """
    A single order action returned by the strategy.

    For SUBMIT: all fields except cancel_order_id are required.
    For CANCEL: only cancel_order_id is required.
    For AMEND: cancel_order_id + new price/size.
    """
    action_type: OrderActionType
    strike: int = 0
    token_side: TokenSide = TokenSide.YES
    trade_side: TradeSide = TradeSide.BUY
    price_ticks: int = 0                     # 1-99 in Polymarket ticks
    size_centishares: int = 0                # Size in centishares
    cancel_order_id: Optional[str] = None    # For CANCEL and AMEND
    tag: str = ""                            # Strategy-defined label for audit


@dataclass
class MarketSnapshot:
    """
    Current state of one Polymarket market, delivered to the strategy.

    Combines book state, fair value, position, and context.
    """
    timestamp_us: int
    strike: int

    # YES token book
    yes_best_bid_ticks: int
    yes_best_ask_ticks: int
    yes_best_bid_size: int
    yes_best_ask_size: int
    yes_bid_depth_total: int
    yes_ask_depth_total: int

    # NO token book
    no_best_bid_ticks: int
    no_best_ask_ticks: int
    no_best_bid_size: int
    no_best_ask_size: int
    no_bid_depth_total: int
    no_ask_depth_total: int

    # Fair values (from FairValueManager)
    fair_value: FairValue

    # Cross-leg parity
    ask_sum_ticks: int             # YES_ask + NO_ask (>= 100 = no arb)
    bid_sum_ticks: int             # YES_bid + NO_bid (<= 100 = no arb)

    # Underlying
    underlying_price_cents: int
    time_to_expiry_seconds: int

    # Own state
    yes_position_cs: int
    no_position_cs: int
    net_yes_cs: int                # yes_position - no_position
    cash_available_tc: int
    resting_orders: list           # Current resting orders for this strike


@dataclass(frozen=True)
class TradeEvent:
    """A trade that occurred on the Polymarket book."""
    timestamp_us: int
    strike: int
    token_side: TokenSide
    trade_side: TradeSide         # Aggressor side
    price_ticks: int
    size_centishares: int


@dataclass(frozen=True)
class FillEvent:
    """Notification that one of the strategy's orders was filled."""
    timestamp_us: int
    strike: int
    order_id: str
    token_side: TokenSide
    trade_side: TradeSide
    price_ticks: int
    filled_size_cs: int
    remaining_size_cs: int
    position_after_cs: int        # New position after this fill


class Strategy(ABC):
    """
    Abstract base class for backtesting strategies.

    The engine calls lifecycle methods in a defined order. The strategy
    has access to DataProvider (for querying any data) and FairValueProvider
    (for requesting probability recomputation).
    """

    @abstractmethod
    def on_init(
        self,
        strikes: list[int],
        expiry_us: int,
        config: dict,
        data_provider: "DataProvider",
        fair_value_provider: FairValueProvider,
    ) -> None:
        """
        Called once before simulation begins.

        Use this to:
        - Store references to data_provider and fair_value_provider.
        - Initialize internal state (inventory trackers, signal buffers).
        - Pre-compute any static values.

        Args:
            strikes: Active Polymarket strikes for this simulation.
            expiry_us: Polymarket resolution timestamp.
            config: Strategy-specific parameters (from TOML config).
            data_provider: For querying book state, trades, underlying prices.
            fair_value_provider: For requesting fair value computation.
        """
        ...

    @abstractmethod
    def on_book_update(
        self,
        snapshots: dict[int, MarketSnapshot],
    ) -> list[OrderAction]:
        """
        Called when any Polymarket book snapshot updates.

        This is the primary quoting callback. The strategy receives
        snapshots for ALL active strikes (not just the one that changed)
        so it can make cross-strike decisions.

        Args:
            snapshots: dict[strike -> MarketSnapshot] for all strikes.

        Returns:
            List of OrderAction to execute. The engine processes them
            in order. Cancels are processed before submits.
        """
        ...

    @abstractmethod
    def on_trade(
        self,
        trade: TradeEvent,
        snapshots: dict[int, MarketSnapshot],
    ) -> list[OrderAction]:
        """
        Called when a trade occurs on the Polymarket book.

        Use this for:
        - Adverse selection detection (trade in your queue).
        - Momentum/flow signals.
        - Urgent requoting after large trades.

        Args:
            trade: The trade event.
            snapshots: Current state of all markets.

        Returns:
            List of OrderAction to execute.
        """
        ...

    @abstractmethod
    def on_fill(
        self,
        fill: FillEvent,
        snapshots: dict[int, MarketSnapshot],
    ) -> list[OrderAction]:
        """
        Called when one of the strategy's resting orders is filled.

        Use this for:
        - Updating inventory tracking.
        - Triggering inventory-reduction quotes.
        - Logging fill quality metrics.

        Args:
            fill: The fill event with full details.
            snapshots: Current state of all markets.

        Returns:
            List of OrderAction (e.g., replace filled order, skew quotes).
        """
        ...

    def on_timer(
        self,
        timestamp_us: int,
        snapshots: dict[int, MarketSnapshot],
    ) -> list[OrderAction]:
        """
        Called at regular intervals (configurable, default 1 second).

        Optional callback for strategies that need periodic housekeeping:
        - Fair value recomputation checks.
        - Time-decay spread adjustments.
        - Stale order cleanup.

        Default implementation returns no actions.

        Args:
            timestamp_us: Current simulation time.
            snapshots: Current state of all markets.

        Returns:
            List of OrderAction.
        """
        return []

    def on_end(self, final_snapshots: dict[int, MarketSnapshot]) -> None:
        """
        Called after the last event, before settlement.

        Use for final logging, state dumps, or pre-settlement adjustments.
        Default implementation is a no-op.
        """
        pass
```

### 5.3 Order Action Processing Rules

The engine processes `OrderAction` lists returned by any callback with these guarantees:

1. **Cancel-first**: All `CANCEL` actions are processed before any `SUBMIT` actions within the same callback return. This prevents self-crossing.
2. **Sequential within type**: Actions of the same type are processed in list order.
3. **Latency applied**: Submitted orders become visible to the fill engine after the configured latency delay (see [[Engine-Architecture-Plan]] Section 4).
4. **Validation**: The engine validates each action before processing:
   - `price_ticks` in [1, 99]
   - `size_centishares` > 0
   - `strike` is in the active strike set
   - Cash sufficient for the order (collateral check)
   - Position limits respected
5. **Rejected actions**: Invalid actions are logged to the audit journal with the rejection reason and silently dropped. The strategy is not notified (to maintain determinism -- the strategy should enforce its own constraints).

### 5.4 Strategy State Management

Strategies manage their own internal state. The engine provides no state persistence between runs. Strategies should track:

```python
@dataclass
class StrategyInternalState:
    """Example of state a strategy might maintain."""
    # Inventory
    positions: dict[int, int]          # strike -> net YES position (centishares)
    total_exposure: int                # Sum of |position| across all strikes

    # Signal history
    fair_value_history: dict[int, list[float]]  # strike -> recent FVs
    alpha_signals: dict[int, float]    # strike -> current alpha (PM - FV)

    # Order tracking
    resting_order_ids: dict[int, list[str]]  # strike -> order IDs
    next_order_tag: int                # Monotonic counter for order tags

    # Performance
    fills_count: int
    total_spread_captured_bps: int
    adverse_selection_count: int
```

---

## 6. Reference Strategy Specifications

Three reference strategies are implemented for validation and benchmarking. Each is fully parameterizable and implements the `Strategy` ABC from Section 5.

### 6.1 ProbabilityQuotingStrategy

**Source**: [[Core-Market-Making-Strategies]] Section 1, adapted from POC `strategy.py`.

**Algorithm**: Quote symmetrically around the B-L (or B-S) fair value with a fixed half-spread, subject to minimum edge and position limits.

```
on_book_update(snapshots):
    actions = []
    for strike, snap in snapshots.items():
        # 1. Cancel all resting orders for this strike
        for order in snap.resting_orders:
            actions.append(CANCEL(order.id))

        # 2. Check minimum edge
        poly_mid = (snap.yes_best_bid_ticks + snap.yes_best_ask_ticks) / 2
        fv_ticks = snap.fair_value.yes_price_ticks
        edge = abs(fv_ticks - poly_mid)
        if edge < min_edge_ticks:
            continue  # No orders: not enough edge

        # 3. Compute bid/ask
        bid = clamp(fv_ticks - half_spread_ticks, 1, 99)
        ask = clamp(fv_ticks + half_spread_ticks, 1, 99)
        if bid >= ask:
            continue

        # 4. Place orders respecting position limits
        pos = snap.net_yes_cs
        if pos < max_position_cs:
            actions.append(SUBMIT(strike, YES, BUY, bid, order_size))
        if pos > -max_position_cs:
            actions.append(SUBMIT(strike, YES, SELL, ask, order_size))

    return actions
```

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `half_spread_ticks` | int | 2 | Half-spread in Polymarket ticks ($0.02) |
| `min_edge_ticks` | int | 3 | Minimum |FV - PM mid| to quote |
| `max_position_cs` | int | 5000 | Max net YES position (centishares = 50 shares) |
| `order_size_cs` | int | 1000 | Size per order (centishares = 10 shares) |

**Expected behavior**: Earns the spread on round trips when the B-L fair value is accurate. Loses when Polymarket price moves away from the B-L estimate (adverse selection). Profits are proportional to edge (FV - PM mid) and inversely proportional to spread width.

### 6.2 AvellanedaStoikovStrategy

**Source**: [[Core-Market-Making-Strategies]] Section 2.

**Algorithm**: Compute a reservation price that skews away from inventory, then quote with an optimal spread derived from the AS model.

```
on_book_update(snapshots):
    actions = []
    for strike, snap in snapshots.items():
        # 1. Cancel all resting orders
        for order in snap.resting_orders:
            actions.append(CANCEL(order.id))

        # 2. Compute reservation price
        fv = snap.fair_value.probability
        q = snap.net_yes_cs / 100.0   # Convert to shares
        tau = snap.time_to_expiry_seconds / (365.25 * 24 * 3600)
        sigma_p = estimate_prob_volatility(strike, snap)

        reservation = fv - q * gamma * sigma_p**2 * tau

        # 3. Compute optimal spread
        base_spread = (1.0 / gamma) * math.log(1 + gamma / kappa)
        risk_spread = 0.5 * gamma * sigma_p**2 * tau
        half_spread = (base_spread + risk_spread) / 2.0

        # 4. Derive quotes
        bid_prob = reservation - half_spread
        ask_prob = reservation + half_spread
        bid_ticks = clamp(round(bid_prob * 100), 1, 99)
        ask_ticks = clamp(round(ask_prob * 100), 1, 99)

        if bid_ticks >= ask_ticks:
            continue

        # 5. Place orders (same position limit logic)
        ...

    return actions
```

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gamma` | float | 0.1 | Risk aversion (higher = more aggressive skew) |
| `kappa` | float | 1.5 | Order arrival intensity (higher = tighter base spread) |
| `sigma_p_default` | float | 0.05 | Default probability volatility per hour |
| `max_position_cs` | int | 5000 | Max net YES position |
| `order_size_cs` | int | 1000 | Size per order |
| `min_spread_ticks` | int | 1 | Floor on half-spread |
| `max_spread_ticks` | int | 10 | Ceiling on half-spread |

**Probability volatility estimation**: $\sigma_p$ is the volatility of the fair value probability process. Estimated from the rolling standard deviation of recent fair value changes, with a floor at `sigma_p_default`. Requires a warm-up period of at least 10 fair value observations.

**Expected behavior**: Inventory self-corrects toward zero. When long, the reservation price drops, making the ask more attractive to takers. Spread widens with volatility and time remaining. At expiry approach, spreads compress (low $\tau$) unless inventory is extreme.

### 6.3 CrossMarketArbStrategy

**Source**: [[Core-Market-Making-Strategies]] Section 5.

**Algorithm**: Compute the alpha signal (Polymarket price vs B-L fair value), and trade directionally when alpha exceeds a threshold. This is not a pure market-making strategy -- it takes directional positions when the mispricing is large.

```
on_book_update(snapshots):
    actions = []
    for strike, snap in snapshots.items():
        # 1. Compute alpha signal
        poly_mid = (snap.yes_best_bid_ticks + snap.yes_best_ask_ticks) / 2.0
        fv_ticks = snap.fair_value.yes_price_ticks
        alpha = (poly_mid - fv_ticks) / 100.0  # In probability units

        # 2. Check threshold
        if abs(alpha) < threshold:
            continue  # Within fair value band

        # 3. Determine direction
        pos = snap.net_yes_cs
        if alpha > threshold and pos > -max_position_cs:
            # YES overpriced on Polymarket -> SELL YES
            sell_price = snap.yes_best_bid_ticks  # Hit the bid
            if sell_price > 0:
                actions.append(SUBMIT(strike, YES, SELL,
                               sell_price, aggress_size))

        elif alpha < -threshold and pos < max_position_cs:
            # YES underpriced on Polymarket -> BUY YES
            buy_price = snap.yes_best_ask_ticks  # Lift the ask
            if buy_price > 0:
                actions.append(SUBMIT(strike, YES, BUY,
                               buy_price, aggress_size))

    return actions
```

**Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `threshold` | float | 0.04 | Minimum |alpha| to trade (4 cents) |
| `aggress_size_cs` | int | 500 | Size per aggressive order |
| `max_position_cs` | int | 3000 | Max net exposure per strike |
| `cooldown_us` | int | 30_000_000 | Minimum microseconds between trades per strike (30s) |
| `require_bl` | bool | True | Only trade when B-L is available (not B-S fallback) |

**Threshold calibration** (from [[Core-Market-Making-Strategies]] Section 5):

$$\theta = \frac{s_\text{PM}}{2} + \epsilon_\text{model} + c_\text{capital}$$

Typical: $s_\text{PM}/2 \approx 0.02$, $\epsilon_\text{model} \approx 0.01\text{-}0.02$, $c_\text{capital} \approx 0.002$. Total $\theta \approx 0.03\text{-}0.05$.

**Expected behavior**: Low trade frequency (only when mispricing exceeds threshold). Positive expected P&L per trade if the B-L estimate is accurate. Exposed to model risk -- if B-L is wrong, every trade loses. The `cooldown_us` parameter prevents overtrading on a single mispricing event.

---

## 7. Strategy-Engine Integration

### 7.1 Event Loop Callback Ordering

The engine's event loop (from [[Engine-Architecture-Plan]] Section 3) processes events in strict timestamp order. Within each timestamp, the callback sequence is:

```
For each event at timestamp T:

  1. Update book state (YES and NO TokenBooks)
  2. Update underlying price (if price event)
  3. Update fair values (FairValueManager.update)
  4. Process pending fills (queue drain from trades)
  5. Build MarketSnapshot for all strikes
  6. Dispatch to strategy callback:
     - Book snapshot event  -> strategy.on_book_update(snapshots)
     - Trade event          -> strategy.on_trade(trade, snapshots)
     - Fill event           -> strategy.on_fill(fill, snapshots)
     - Timer tick           -> strategy.on_timer(timestamp, snapshots)
  7. Collect OrderActions from callback return
  8. Process actions:
     a. All CANCELs first
     b. All AMENDs second
     c. All SUBMITs last
  9. Apply latency to new orders (become active at T + latency)
  10. Log to audit journal
```

### 7.2 Ordering Guarantees

| Guarantee | Description |
|-----------|-------------|
| **External-before-internal** | Market data at time $T$ is fully processed before any strategy actions at $T$ |
| **No same-timestamp reaction** | Strategy decisions at $T$ create orders visible at $T + \text{latency}$, never at $T$ |
| **Fair value before strategy** | `FairValueManager.update()` runs before any strategy callback at each timestamp |
| **Fill before requote** | If a fill occurs at $T$, `on_fill` is called before `on_book_update` at $T$ |
| **Single callback per event** | Each event triggers exactly one callback. A book snapshot does not trigger `on_trade`. |
| **All strikes in every callback** | `snapshots` dict always contains all active strikes, not just the one that changed |

### 7.3 Timer Callback

The `on_timer` callback fires at regular intervals (default: every 1 second of simulation time). It is injected into the event stream as a synthetic event and follows the same ordering rules.

**Use cases**:
- Periodic fair value recomputation checks.
- Time-decay spread adjustments (widening spreads as expiry approaches).
- Stale order cancellation (orders resting too long without fills).
- Periodic state logging for debugging.

The timer interval is configurable via `strategy.timer_interval_us` in the config.

### 7.4 DataProvider Access Pattern

Strategies receive a `DataProvider` reference at initialization. They can query it during any callback for additional context:

```python
# Inside a strategy callback:
underlying = self.data_provider.get_underlying_price(timestamp_us)
book = self.data_provider.get_book_snapshot(strike, TokenSide.YES, timestamp_us)
recent_trades = self.data_provider.get_recent_trades(
    strike, TokenSide.YES, since_us=timestamp_us - 60_000_000
)
```

**Important constraint**: DataProvider queries respect the simulation clock. Strategies cannot "see the future" -- queries at time $T$ only return data with timestamps $\leq T$.

### 7.5 FairValueProvider Access Pattern

Strategies can trigger fair value recomputation during any callback:

```python
# Inside a strategy callback:
if self.fv_provider.needs_recompute(underlying_price_cents, timestamp_us):
    chain = self.data_provider.get_options_chain(timestamp_us)
    self.fv_provider.recompute(underlying_price_cents, timestamp_us, chain)
```

The FairValueManager automatically calls `recompute` during step 3 of the event loop (before strategy callbacks), but strategies can also trigger it explicitly for finer control. The provider deduplicates: if already recomputed at this timestamp, the second call is a no-op.

---

## 8. Configuration

### 8.1 TOML Configuration Schema

All strategy and fair value parameters are specified in TOML config files. This enables parameter sweeps without code changes.

```toml
[simulation]
ticker = "NVDA"
date = "2026-04-02"
strikes = [115, 120, 125, 130, 135]
expiry_datetime = "2026-04-02T20:00:00Z"   # 4 PM ET market close

[fair_value]
primary_method = "breeden_litzenberger"     # or "black_scholes"
fallback_method = "black_scholes"

[fair_value.black_scholes]
sigma = 0.50
r = 0.0

[fair_value.breeden_litzenberger]
iv_method = "sabr"                          # or "svi"
grid_points = 400
recompute_interval_s = 300
recompute_move_pct = 0.005
min_strikes = 5
tail_method = "lognormal"

[fair_value.breeden_litzenberger.sabr]
beta = 1.0
use_obloj = true
rho_init = -0.3
nu_init = 0.5
max_iter = 50
atm_weight = 5.0

[fair_value.breeden_litzenberger.svi]
# SVI-specific overrides (when iv_method = "svi")
rho_init = 0.0
m_init = 0.0

[strategy]
name = "probability_quoting"                # or "avellaneda_stoikov", "cross_market_arb"
timer_interval_us = 1_000_000               # 1 second

[strategy.probability_quoting]
half_spread_ticks = 2
min_edge_ticks = 3
max_position_cs = 5000
order_size_cs = 1000

[strategy.avellaneda_stoikov]
gamma = 0.1
kappa = 1.5
sigma_p_default = 0.05
max_position_cs = 5000
order_size_cs = 1000
min_spread_ticks = 1
max_spread_ticks = 10

[strategy.cross_market_arb]
threshold = 0.04
aggress_size_cs = 500
max_position_cs = 3000
cooldown_us = 30_000_000
require_bl = true
```

### 8.2 Parameter Sweeps

For optimization, the engine accepts a sweep specification:

```toml
[sweep]
parameters = [
    { path = "strategy.probability_quoting.half_spread_ticks", values = [1, 2, 3, 4, 5] },
    { path = "strategy.probability_quoting.min_edge_ticks", values = [2, 3, 4, 5] },
]
mode = "grid"     # "grid" (all combinations) or "random" (sample N combos)
max_runs = 100    # Cap for grid mode
```

The sweep runner executes each configuration independently and collects results into a summary DataFrame.

---

## 9. Testing Strategy

### 9.1 B-L Pipeline Validation

**Test 1: Known Analytical Case (Black-Scholes)**

Generate a synthetic options chain from Black-Scholes with known constant IV. Run the B-L pipeline. The extracted probability should match $\Phi(d_2)$ to within the finite-difference discretization error.

```python
def test_bl_recovers_bs():
    """B-L with constant IV should reproduce Black-Scholes."""
    S, K, tau, sigma, r = 100.0, 100.0, 30/365, 0.30, 0.0
    # Generate synthetic chain: 50 strikes, constant IV = 0.30
    chain = generate_bs_chain(S, tau, sigma, r, n_strikes=50)
    bl = BreedenLitzenbergerFairValue(config={"iv_method": "sabr"})
    bl.recompute_from_chain(chain, S, tau)
    p_bl = bl.compute_probability(K)
    p_bs = norm.cdf(d2(S, K, tau, sigma, r))
    assert abs(p_bl - p_bs) < 0.005  # Within 0.5%
```

**Test 2: Known Skew Case**

Generate a chain with a known SABR smile (specified parameters). Run the pipeline. Verify:
- Extracted SABR parameters match input within tolerance.
- Density is non-negative everywhere.
- Density integrates to 1.0 within 1%.
- Mean of density equals forward within 0.5%.

**Test 3: Tail Behavior**

Generate a chain with limited strike range (e.g., 80-120 for $S = 100$). Verify:
- Probability at $K = 80$ is close to 1.0 (deep ITM).
- Probability at $K = 120$ is close to 0.0 (deep OTM).
- Tails are handled gracefully (no NaN, no negative probabilities).

**Test 4: Noise Robustness**

Add Gaussian noise to synthetic option prices (simulating bid-ask noise). Verify:
- SABR fit still converges.
- Extracted probability changes by less than 2% for moderate noise (spread = 5% of mid).
- Pipeline falls back to B-S gracefully when noise is extreme.

**Test 5: Expiry Mismatch**

Generate two synthetic chains at different expiries $T_1$ and $T_2$. Compute the probability at intermediate date $T_P$ via variance-linear interpolation. Compare against the known B-S probability at $T_P$ (if IV is constant, interpolation should be exact).

### 9.2 Strategy Unit Tests with Synthetic Data

**Test 6: ProbabilityQuoting -- Basic Quoting**

```python
def test_prob_quoting_places_symmetric_orders():
    """Strategy should place bid/ask around fair value."""
    snap = make_snapshot(fv_ticks=65, yes_bid=60, yes_ask=70, position=0)
    strategy = ProbabilityQuotingStrategy(half_spread=2, min_edge=3)
    actions = strategy.on_book_update({120: snap})
    bids = [a for a in actions if a.trade_side == TradeSide.BUY]
    asks = [a for a in actions if a.trade_side == TradeSide.SELL]
    assert len(bids) == 1 and bids[0].price_ticks == 63
    assert len(asks) == 1 and asks[0].price_ticks == 67
```

**Test 7: ProbabilityQuoting -- No Orders Below Min Edge**

```python
def test_prob_quoting_no_orders_below_min_edge():
    """Strategy should not quote when edge is below threshold."""
    snap = make_snapshot(fv_ticks=65, yes_bid=64, yes_ask=66, position=0)
    strategy = ProbabilityQuotingStrategy(half_spread=2, min_edge=3)
    actions = strategy.on_book_update({120: snap})
    submits = [a for a in actions if a.action_type == OrderActionType.SUBMIT]
    assert len(submits) == 0
```

**Test 8: AvellanedaStoikov -- Inventory Skew**

```python
def test_as_skews_with_inventory():
    """Long position should shift quotes down."""
    snap_flat = make_snapshot(fv_ticks=55, position=0)
    snap_long = make_snapshot(fv_ticks=55, position=3000)  # 30 shares long

    strategy = AvellanedaStoikovStrategy(gamma=0.1, kappa=1.5)
    actions_flat = strategy.on_book_update({120: snap_flat})
    actions_long = strategy.on_book_update({120: snap_long})

    bid_flat = get_bid_price(actions_flat)
    bid_long = get_bid_price(actions_long)
    assert bid_long < bid_flat  # Long position pushes bid down
```

**Test 9: CrossMarketArb -- Threshold Behavior**

```python
def test_arb_no_trade_within_threshold():
    """No trade when alpha is within threshold."""
    snap = make_snapshot(fv_ticks=65, yes_bid=66, yes_ask=68)  # alpha = +2 ticks
    strategy = CrossMarketArbStrategy(threshold=0.04)  # 4 ticks
    actions = strategy.on_book_update({120: snap})
    submits = [a for a in actions if a.action_type == OrderActionType.SUBMIT]
    assert len(submits) == 0

def test_arb_sells_when_overpriced():
    """Sell YES when Polymarket price exceeds FV by more than threshold."""
    snap = make_snapshot(fv_ticks=60, yes_bid=66, yes_ask=68)  # alpha = +7 ticks
    strategy = CrossMarketArbStrategy(threshold=0.04)
    actions = strategy.on_book_update({120: snap})
    sells = [a for a in actions if a.trade_side == TradeSide.SELL]
    assert len(sells) == 1
```

**Test 10: Position Limit Enforcement**

```python
def test_position_limits_respected():
    """No orders that would exceed position limits."""
    snap = make_snapshot(fv_ticks=65, yes_bid=60, yes_ask=70,
                        position=4900)  # Near limit of 5000
    strategy = ProbabilityQuotingStrategy(max_position_cs=5000,
                                         order_size_cs=1000)
    actions = strategy.on_book_update({120: snap})
    buys = [a for a in actions if a.trade_side == TradeSide.BUY]
    assert all(b.size_centishares <= 100 for b in buys)  # Only 100 cs room
```

### 9.3 Integration Tests

**Test 11: Full Pipeline End-to-End**

Run a 1-hour backtest with synthetic Polymarket book data, synthetic underlying prices, and synthetic options chains. Verify:
- Fair values are computed and cached correctly.
- Strategy callbacks fire in the correct order.
- Orders are generated and filled through the execution simulator.
- Final P&L is deterministic (run twice, compare).

**Test 12: Fallback Chain**

Simulate options data becoming unavailable mid-simulation. Verify:
- B-L provider detects missing data.
- FairValueManager falls back to B-S.
- Strategy continues operating with B-S fair values.
- Audit journal records the fallback event.

---

## 10. Task Breakdown

### Phase 4A: Fair Value Providers (8 tasks)

| # | Task | Depends On | Est. |
|---|------|-----------|------|
| 4.1 | Implement `FairValue` dataclass, `FairValueProvider` protocol, `FairValueMethod` enum | -- | 1h |
| 4.2 | Implement `BlackScholesFairValue` provider (port from POC `fair_value.py`) | 4.1 | 2h |
| 4.3 | Implement SABR calibration module: Hagan formula with Obloj correction, SLSQP optimizer, ATM anchoring | -- | 6h |
| 4.4 | Implement B-L pipeline steps 1-2: data ingestion from DataProvider, cleaning, put-call parity unification | 4.1 | 4h |
| 4.5 | Implement B-L pipeline steps 3-6: IV fitting (calls SABR from 4.3), reprice on grid, finite differences, integration, validation | 4.3, 4.4 | 6h |
| 4.6 | Implement `BreedenLitzenbergerFairValue` provider with caching and recompute policy | 4.5 | 3h |
| 4.7 | Implement variance-linear interpolation for expiry mismatch | 4.6 | 3h |
| 4.8 | Implement `FairValueManager` with monotonicity enforcement, fallback chain, multi-strike orchestration | 4.2, 4.6 | 3h |

### Phase 4B: Strategy Interface (5 tasks)

| # | Task | Depends On | Est. |
|---|------|-----------|------|
| 4.9 | Define `Strategy` ABC, `OrderAction`, `MarketSnapshot`, `TradeEvent`, `FillEvent` dataclasses | -- | 2h |
| 4.10 | Implement engine-strategy integration: callback dispatch, action processing, cancel-before-submit ordering | 4.9 | 4h |
| 4.11 | Implement timer callback injection into event loop | 4.10 | 1h |
| 4.12 | Implement `MicroPriceFairValue` provider | 4.1 | 1h |
| 4.13 | Implement TOML config loader and parameter sweep runner | 4.9 | 3h |

### Phase 4C: Reference Strategies (4 tasks)

| # | Task | Depends On | Est. |
|---|------|-----------|------|
| 4.14 | Implement `ProbabilityQuotingStrategy` | 4.9, 4.8 | 3h |
| 4.15 | Implement `AvellanedaStoikovStrategy` with probability volatility estimation | 4.14 | 4h |
| 4.16 | Implement `CrossMarketArbStrategy` with cooldown and threshold logic | 4.14 | 3h |
| 4.17 | Implement SVI calibration module (second priority fitter) | 4.3 | 4h |

### Phase 4D: Testing and Validation (4 tasks)

| # | Task | Depends On | Est. |
|---|------|-----------|------|
| 4.18 | B-L pipeline unit tests: known analytical cases, noise robustness, tail handling | 4.6 | 4h |
| 4.19 | Strategy unit tests: all 10 test cases from Section 9.2 | 4.14, 4.15, 4.16 | 3h |
| 4.20 | Integration test: full pipeline end-to-end with synthetic data | 4.10, 4.8, 4.14 | 4h |
| 4.21 | Fallback chain test, determinism verification, audit journal validation | 4.20 | 2h |

### Dependency Graph

```
4.1 ──┬── 4.2 ──────────────────┬── 4.8 ──┬── 4.14 ──┬── 4.15
      │                         │          │          ├── 4.16
      ├── 4.4 ──┐               │          │          │
      │         ├── 4.5 ── 4.6 ─┤          │          │
      │    4.3 ─┘          │    │          │          │
      │                    4.7  │          │          │
      └── 4.12                  │          │          │
                                │          │          │
4.9 ── 4.10 ── 4.11            │          │          │
  │                             │          │          │
  └── 4.13                      │          │          │
                                │          │          │
                           4.18 ┘     4.19 ┘     4.20 ── 4.21
                                                   │
                                              4.17 (parallel, lower priority)
```

### Total Estimated Effort

| Sub-phase | Tasks | Hours |
|-----------|-------|-------|
| 4A: Fair Value Providers | 8 | 28h |
| 4B: Strategy Interface | 5 | 11h |
| 4C: Reference Strategies | 4 | 14h |
| 4D: Testing & Validation | 4 | 13h |
| **Total** | **21** | **66h** |

### Critical Path

4.1 -> 4.4 -> 4.3 -> 4.5 -> 4.6 -> 4.8 -> 4.14 -> 4.20 -> 4.21

The SABR calibration module (4.3) is the highest-risk task: it involves numerical optimization with potential convergence issues. Implement it early and test in isolation before integrating into the B-L pipeline.

---

## Related Notes

- [[Breeden-Litzenberger-Pipeline]] -- Full mathematical foundation for probability extraction
- [[Vol-Surface-Fitting]] -- SABR vs SVI comparison, calibration procedures, no-arbitrage conditions
- [[Core-Market-Making-Strategies]] -- All 6 strategies with mathematical formulations
- [[Inventory-and-Risk-Management]] -- Position limits, skewing, hedging framework
- [[Risk-Neutral-vs-Physical-Probabilities]] -- RN vs physical adjustment methods (future enhancement)
- [[Engine-Architecture-Plan]] -- Overall engine architecture, Layer 3 and Layer 5.5 specifications
