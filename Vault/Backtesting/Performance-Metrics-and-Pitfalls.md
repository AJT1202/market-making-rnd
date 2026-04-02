---
tags:
  - backtesting
  - metrics
  - pitfalls
  - market-making
  - polymarket
  - statistical-analysis
created: 2026-03-31
---

# Performance Metrics and Pitfalls

Evaluation methodology for market making backtests on Polymarket binary event markets. Covers PnL decomposition, risk metrics, fill quality analysis, statistical rigor, and the critical pitfalls that cause backtests to overstate profitability.

See [[Backtesting-Architecture]] for the engine design and [[Backtesting-Plan]] for phased implementation.

---

## 1. PnL Decomposition

Total PnL for a market making strategy decomposes into three independent sources. Understanding this decomposition is essential for diagnosing whether a strategy is genuinely profitable or benefiting from unrealistic assumptions.

### 1.1 The Three Components

```
Total PnL = Spread Capture + Inventory PnL + Adverse Selection Cost

Where:
  Spread Capture    = Σ (half_spread × fill_size) for each fill
  Inventory PnL     = Σ (position × price_change) over holding periods
  Adverse Selection = Σ (loss from fills where price moved against us immediately)
```

A healthy market making strategy has:
- **Positive spread capture** (the primary revenue source)
- **Near-zero inventory PnL** (inventory should not accumulate directional exposure)
- **Negative adverse selection cost** (always negative; the question is how large)
- **Spread capture > |Adverse selection cost|** (this is the profitability condition)

### 1.2 Implementation

```python
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import List

@dataclass
class PnLDecomposition:
    """
    Decompose market making PnL into spread capture, inventory PnL,
    and adverse selection costs.
    """

    spread_capture: float = 0.0
    inventory_pnl: float = 0.0
    adverse_selection_cost: float = 0.0
    fees_paid: float = 0.0
    resolution_pnl: float = 0.0  # Binary outcome at expiry

    # Per-fill tracking
    fill_records: List[dict] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return (self.spread_capture + self.inventory_pnl +
                self.adverse_selection_cost - self.fees_paid +
                self.resolution_pnl)

    @property
    def gross_spread_capture(self) -> float:
        """Spread capture before adverse selection."""
        return self.spread_capture

    @property
    def net_spread_capture(self) -> float:
        """Spread capture after adverse selection — the true edge."""
        return self.spread_capture + self.adverse_selection_cost

    def record_fill(self, fill: dict, fair_value: float,
                     midpoint_before: float, midpoint_after_5min: float):
        """
        Record a fill and decompose its PnL contribution.

        Parameters:
            fill: {side, price, size, timestamp}
            fair_value: B-L probability at time of fill
            midpoint_before: Polymarket midpoint at fill time
            midpoint_after_5min: Polymarket midpoint 5 minutes later
        """
        if fill["side"] == "buy":
            # Bought at bid — spread capture = midpoint - fill_price
            spread_component = (midpoint_before - fill["price"]) * fill["size"]
            # Adverse selection = how much mid moved against us (down for buys)
            adverse_component = (midpoint_after_5min - midpoint_before) * fill["size"]
            # If midpoint_after < midpoint_before, adverse_component is negative (bad for buyer)
        else:  # sell
            spread_component = (fill["price"] - midpoint_before) * fill["size"]
            adverse_component = (midpoint_before - midpoint_after_5min) * fill["size"]

        self.spread_capture += spread_component
        self.adverse_selection_cost += min(0, adverse_component)  # Only count losses

        self.fill_records.append({
            **fill,
            "spread_component": spread_component,
            "adverse_component": adverse_component,
            "fair_value_at_fill": fair_value,
            "edge_at_fill": abs(fair_value - midpoint_before),
        })

    def compute_inventory_pnl(self, position_history: List[tuple]):
        """
        Compute inventory PnL from position × price changes over time.
        position_history: [(timestamp, position, midpoint), ...]
        """
        inv_pnl = 0.0
        for i in range(1, len(position_history)):
            _, pos_prev, _ = position_history[i-1]
            _, _, mid_curr = position_history[i]
            _, _, mid_prev = position_history[i-1]
            inv_pnl += pos_prev * (mid_curr - mid_prev)
        self.inventory_pnl = inv_pnl

    def summary(self) -> dict:
        return {
            "total_pnl": self.total_pnl,
            "spread_capture": self.spread_capture,
            "inventory_pnl": self.inventory_pnl,
            "adverse_selection_cost": self.adverse_selection_cost,
            "fees_paid": self.fees_paid,
            "resolution_pnl": self.resolution_pnl,
            "net_spread_capture": self.net_spread_capture,
            "num_fills": len(self.fill_records),
            "avg_spread_per_fill": self.spread_capture / max(1, len(self.fill_records)),
            "avg_adverse_per_fill": self.adverse_selection_cost / max(1, len(self.fill_records)),
        }
```

### 1.3 Binary Market-Specific PnL Considerations

Unlike continuous markets, Polymarket binary events have a **terminal payoff** that dominates the PnL profile:

| Component | Continuous Market | Binary Event Market |
|-----------|------------------|---------------------|
| Spread capture | Primary revenue | Secondary — small spreads on $0–$1 range |
| Inventory PnL | Mark-to-market only | **Terminal: position resolves to $0 or $1** |
| Time horizon | Continuous | Bounded by resolution date |
| Capital lockup | None (can exit) | Capital locked until resolution |

**Implication:** In binary markets, the resolution PnL often dwarfs the spread capture. A strategy that accumulates net inventory is effectively taking a directional bet on the outcome, which may swamp any spread-capture edge.

---

## 2. Risk-Adjusted Return Metrics

### 2.1 Core Metrics

```python
class BacktestMetrics:
    """Compute standard and market-making-specific performance metrics."""

    @staticmethod
    def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.05,
                      periods_per_year: int = 252) -> float:
        """
        Annualized Sharpe ratio.
        For market making, use intraday return intervals (e.g., hourly).
        Sharpe > 2.0 is strong; Sharpe > 3.0 is exceptional (or suspicious).
        """
        excess_returns = returns - risk_free_rate / periods_per_year
        if excess_returns.std() == 0:
            return 0.0
        return np.sqrt(periods_per_year) * excess_returns.mean() / excess_returns.std()

    @staticmethod
    def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.05,
                       periods_per_year: int = 252) -> float:
        """
        Like Sharpe but only penalizes downside volatility.
        More appropriate for asymmetric return distributions (binary payoffs).
        """
        excess_returns = returns - risk_free_rate / periods_per_year
        downside = excess_returns[excess_returns < 0]
        if len(downside) == 0 or downside.std() == 0:
            return float('inf') if excess_returns.mean() > 0 else 0.0
        return np.sqrt(periods_per_year) * excess_returns.mean() / downside.std()

    @staticmethod
    def max_drawdown(equity_curve: pd.Series) -> float:
        """Maximum peak-to-trough decline as a fraction."""
        peak = equity_curve.expanding().max()
        drawdown = (equity_curve - peak) / peak
        return drawdown.min()

    @staticmethod
    def calmar_ratio(returns: pd.Series, equity_curve: pd.Series,
                      periods_per_year: int = 252) -> float:
        """Annualized return / max drawdown. Higher is better."""
        annual_return = returns.mean() * periods_per_year
        mdd = abs(BacktestMetrics.max_drawdown(equity_curve))
        if mdd == 0:
            return float('inf') if annual_return > 0 else 0.0
        return annual_return / mdd

    @staticmethod
    def profit_factor(pnl_series: pd.Series) -> float:
        """Gross profit / gross loss. Above 1.5 is good; above 2.0 is strong."""
        gross_profit = pnl_series[pnl_series > 0].sum()
        gross_loss = abs(pnl_series[pnl_series < 0].sum())
        if gross_loss == 0:
            return float('inf') if gross_profit > 0 else 0.0
        return gross_profit / gross_loss
```

### 2.2 Market Making-Specific Metrics

```python
class MarketMakingMetrics:
    """Metrics specific to market making strategy evaluation."""

    @staticmethod
    def fill_rate(total_quotes: int, total_fills: int) -> float:
        """
        Fraction of quotes that result in fills.
        Typical range: 5-30% for market making on thin books.
        Too high → quotes may be too aggressive (adverse selection risk).
        Too low → not capturing enough spread.
        """
        if total_quotes == 0:
            return 0.0
        return total_fills / total_quotes

    @staticmethod
    def quote_to_trade_ratio(total_quote_updates: int,
                               total_fills: int) -> float:
        """
        How many quote updates per fill. High ratios indicate active quoting.
        Typical: 10-100x for active market makers.
        """
        if total_fills == 0:
            return float('inf')
        return total_quote_updates / total_fills

    @staticmethod
    def inventory_turnover(total_volume_traded: float,
                            avg_absolute_inventory: float) -> float:
        """
        How quickly inventory cycles. Higher = healthier market making.
        Turnover = total_volume / avg(|inventory|)
        """
        if avg_absolute_inventory == 0:
            return float('inf')
        return total_volume_traded / avg_absolute_inventory

    @staticmethod
    def inventory_half_life(inventory_series: pd.Series) -> float:
        """
        Average time for inventory to decay to half its peak.
        Shorter half-life → better inventory management.
        Uses autocorrelation to estimate mean-reversion speed.
        """
        autocorr = inventory_series.autocorr(lag=1)
        if autocorr <= 0 or autocorr >= 1:
            return float('inf')
        # Half-life from AR(1): t_half = -ln(2) / ln(autocorr)
        return -np.log(2) / np.log(autocorr)

    @staticmethod
    def adverse_selection_ratio(adverse_fills: int,
                                  total_fills: int) -> float:
        """
        Fraction of fills that are adverse (price moves against us post-fill).
        Empirical benchmarks: 65-89% in liquid markets (ES, NQ, CL, ZN).
        If backtest shows < 50%, fill simulation is likely unrealistic.
        """
        if total_fills == 0:
            return 0.0
        return adverse_fills / total_fills

    @staticmethod
    def realized_spread(fills: List[dict], midpoints: pd.DataFrame,
                          horizon_minutes: int = 5) -> float:
        """
        Average spread actually captured after accounting for price movement.

        Realized spread = 2 × sign(side) × (fill_price - midpoint_{t+horizon})

        Positive = capturing spread successfully.
        Negative = being adversely selected.
        """
        realized_spreads = []
        for fill in fills:
            future_mid = get_midpoint_at(midpoints,
                                          fill["timestamp"] + pd.Timedelta(minutes=horizon_minutes))
            if future_mid is None:
                continue

            if fill["side"] == "buy":
                rs = future_mid - fill["price"]  # Profit if mid goes up
            else:
                rs = fill["price"] - future_mid  # Profit if mid goes down

            realized_spreads.append(rs)

        return np.mean(realized_spreads) if realized_spreads else 0.0

    @staticmethod
    def pnl_per_market(results: List[dict]) -> pd.DataFrame:
        """
        Break down PnL by market (ticker/strike/expiry combination).
        Identifies which markets are profitable vs losers.
        """
        df = pd.DataFrame(results)
        return df.groupby(["ticker", "strike", "expiry"]).agg({
            "total_pnl": "sum",
            "spread_capture": "sum",
            "adverse_selection": "sum",
            "num_fills": "sum",
            "outcome": "first",  # YES or NO
        }).sort_values("total_pnl", ascending=False)
```

### 2.3 Binary-Specific Metrics

```python
class BinaryMarketMetrics:
    """Metrics specifically for binary event market making."""

    @staticmethod
    def brier_score(predicted_probs: np.ndarray,
                      outcomes: np.ndarray) -> float:
        """
        Brier score for probability accuracy: mean((p - o)^2).
        Lower is better. 0 = perfect, 0.25 = coin flip.

        Use to evaluate the Breeden-Litzenberger probability extraction
        against actual binary outcomes.
        """
        return np.mean((predicted_probs - outcomes) ** 2)

    @staticmethod
    def calibration_curve(predicted_probs: np.ndarray,
                            outcomes: np.ndarray,
                            n_bins: int = 10) -> pd.DataFrame:
        """
        Calibration analysis: when we predict P=0.7, does the event
        occur ~70% of the time?

        Well-calibrated probabilities are essential for market making —
        systematic miscalibration means our fair values are biased.
        """
        bins = np.linspace(0, 1, n_bins + 1)
        results = []

        for i in range(n_bins):
            mask = (predicted_probs >= bins[i]) & (predicted_probs < bins[i+1])
            if mask.sum() == 0:
                continue
            results.append({
                "bin_center": (bins[i] + bins[i+1]) / 2,
                "predicted_mean": predicted_probs[mask].mean(),
                "observed_frequency": outcomes[mask].mean(),
                "count": mask.sum(),
                "gap": abs(predicted_probs[mask].mean() - outcomes[mask].mean()),
            })

        return pd.DataFrame(results)

    @staticmethod
    def edge_decay_analysis(fills: List[dict],
                              midpoints: pd.DataFrame) -> pd.DataFrame:
        """
        How quickly does the edge at fill time decay?

        For each fill, measure the midpoint at t, t+1m, t+5m, t+15m, t+30m, t+1h.
        If midpoint moves toward our fill price, we're capturing real edge.
        If midpoint moves away, we're getting adversely selected.
        """
        horizons = [1, 5, 15, 30, 60]  # minutes
        results = []

        for fill in fills:
            row = {"fill_price": fill["price"], "side": fill["side"]}
            mid_at_fill = get_midpoint_at(midpoints, fill["timestamp"])
            row["edge_at_fill"] = abs(fill["price"] - mid_at_fill)

            for h in horizons:
                future_mid = get_midpoint_at(
                    midpoints, fill["timestamp"] + pd.Timedelta(minutes=h)
                )
                if future_mid is not None:
                    if fill["side"] == "buy":
                        row[f"pnl_{h}m"] = future_mid - fill["price"]
                    else:
                        row[f"pnl_{h}m"] = fill["price"] - future_mid

            results.append(row)

        return pd.DataFrame(results)

    @staticmethod
    def capital_efficiency(total_pnl: float, max_capital_deployed: float,
                             holding_period_days: float) -> dict:
        """
        Capital efficiency metrics for binary markets where capital is locked.
        """
        return {
            "return_on_capital": total_pnl / max_capital_deployed if max_capital_deployed > 0 else 0,
            "annualized_return": (total_pnl / max_capital_deployed) * (365 / holding_period_days) if max_capital_deployed > 0 and holding_period_days > 0 else 0,
            "capital_utilization": max_capital_deployed,  # How much capital was tied up
        }
```

---

## 3. Backtesting Pitfalls

### 3.1 The Five Fatal Pitfalls for Market Making Backtests

#### Pitfall 1: Unrealistic Fill Assumptions (Severity: Critical)

**The single most common reason market making backtests overstate profitability.**

| Bad Assumption | Reality | Impact |
|----------------|---------|--------|
| Fill at midpoint when price touches order | Queue position matters — you may never get filled | Overstates fill rate 5-10x |
| 100% fill rate for resting orders | Typical fill rates: 5-30% | Overstates revenue by 3-20x |
| Symmetric fill probability | 65-89% of fills are adverse | Understates adverse selection |
| Fills independent of market direction | You get filled more when you're wrong | Misses key cost |
| No partial fills | Thin books mean frequent partial fills | Overstates size |

**Mitigation:**
- Use the adverse selection-aware fill model from [[Backtesting-Architecture]]
- Calibrate fill rates from historical Polymarket trade data
- Run sensitivity analysis: vary fill rate from 10% to 50% and check if strategy survives
- Verify backtest adverse selection rate is 50-80% (if below 50%, model is too optimistic)

#### Pitfall 2: Look-Ahead Bias in Probability Extraction (Severity: High)

Using options data that was not available at the time the trading decision was made.

| Source of Bias | Example | Fix |
|----------------|---------|-----|
| Using closing IV for intraday decisions | Options chain at 15:59 used for 10:00 AM trade | Use latest available snapshot before each decision point |
| Using all strikes including illiquid ones | Deep OTM option with stale quote skews the vol smile | Filter by minimum volume, bid-ask spread, and open interest |
| Forward-filling options data incorrectly | Overnight: using next morning's options for overnight Polymarket trades | Use previous close's options data until market reopens |
| Recalculating B-L probabilities with future data | Refitting SABR parameters using full-day data | Fit only on data available up to the current timestamp |

**Mitigation:**
- Strict point-in-time data alignment (see `TimeAligner` in [[Backtesting-Architecture]])
- Options data forward-filled only from past observations
- Log the exact options snapshot used for each probability calculation
- Re-run backtest with 15-minute, 30-minute, and 60-minute stale options data to test sensitivity

#### Pitfall 3: Overfitting to Historical Mispricings (Severity: High)

Optimizing strategy parameters to perfectly exploit past mispricings that will not recur.

| Symptom | Diagnosis | Treatment |
|---------|-----------|-----------|
| Perfect parameter values for each ticker | Overfit to ticker-specific patterns | Use same parameters across all tickers |
| Strategy only works for a specific date range | Period-specific anomaly | Walk-forward validation across multiple periods |
| Very high Sharpe (>5) in backtest | Too good to be true | Apply Monte Carlo stress testing |
| Strategy requires precise spread width per market | Curve-fitted to historical data | Use a single parameterization or small set |

**Mitigation:**
- Walk-forward analysis with rolling optimization windows
- Out-of-sample testing: never optimize on data you report results for
- Keep strategy parameters minimal (ideally 3-5, never more than 10)
- Require statistical significance at 95% confidence level

#### Pitfall 4: Survivorship Bias in Market Selection (Severity: Medium)

Only backtesting markets that resolved in a way that makes the strategy look good.

| Bias | Example | Fix |
|------|---------|-----|
| Only testing markets where options pricing was accurate | Ignoring markets around earnings, after major news | Include all market types in backtest universe |
| Dropping markets with thin data | These are often the most difficult to trade | Include them with appropriate position sizing |
| Selecting date ranges with clear mispricings | Cherry-picking favorable periods | Test across full available history |

**Mitigation:**
- Backtest across ALL markets for each ticker, not a curated subset
- Include earnings periods, high-volatility events, and market stress periods
- Report results for the full universe and for subsets separately

#### Pitfall 5: Ignoring Transaction Costs and Slippage (Severity: Medium)

| Cost | Polymarket Specifics | How to Model |
|------|---------------------|--------------|
| Maker fees | **Zero** on Polymarket | Free — a structural advantage |
| Taker fees | ~2% of notional | Apply to any aggressive orders or cancellation-replacement crosses |
| Gas fees | Variable (Polygon L2, typically <$0.01) | Add flat fee per transaction |
| Spread cost | The bid-ask spread *is* the cost | Already captured if using midpoint correctly |
| Slippage | 1-2 ticks ($0.01-$0.02) on thin books | Add 1 tick minimum to market/aggressive orders |
| Capital opportunity cost | Funds locked until resolution | Discount PnL by risk-free rate over holding period |

### 3.2 Backtesting Red Flags

If any of these appear in backtest results, investigate before trusting the numbers:

| Red Flag | Likely Cause |
|----------|-------------|
| Sharpe ratio > 5 | Unrealistic fill assumptions or look-ahead bias |
| Fill rate > 50% | Fill simulation too generous |
| Adverse selection ratio < 40% | Fill model not accounting for adverse selection |
| Zero losing markets | Survivorship bias or overfitting |
| PnL dominated by resolution (not spread capture) | Strategy is taking directional bets, not market making |
| Steady upward PnL curve with no drawdowns | Too good to be true; likely simulation artifact |
| Dramatically different results per ticker | Possible overfitting to ticker-specific patterns |

---

## 4. Statistical Rigor

### 4.1 Walk-Forward Validation

The gold standard for validating trading strategies. Train on one period, test on the next, repeat.

```python
class WalkForwardValidator:
    """
    Walk-forward analysis for market making backtests.

    Splits the history into rolling windows:
    - In-sample: optimize strategy parameters
    - Out-of-sample: test with those parameters
    - Roll forward and repeat
    """

    def __init__(self, in_sample_days: int = 30,
                 out_of_sample_days: int = 7,
                 step_days: int = 7):
        self.is_days = in_sample_days
        self.oos_days = out_of_sample_days
        self.step_days = step_days

    def generate_windows(self, start_date, end_date) -> List[dict]:
        windows = []
        current = start_date

        while current + pd.Timedelta(days=self.is_days + self.oos_days) <= end_date:
            windows.append({
                "is_start": current,
                "is_end": current + pd.Timedelta(days=self.is_days),
                "oos_start": current + pd.Timedelta(days=self.is_days),
                "oos_end": current + pd.Timedelta(days=self.is_days + self.oos_days),
            })
            current += pd.Timedelta(days=self.step_days)

        return windows

    def run(self, strategy_class, data, param_grid) -> pd.DataFrame:
        """
        Run walk-forward optimization.

        For each window:
        1. Optimize params on in-sample data
        2. Run backtest with those params on out-of-sample data
        3. Record OOS performance
        """
        results = []

        for window in self.generate_windows(data.index.min(), data.index.max()):
            # In-sample optimization
            is_data = data[window["is_start"]:window["is_end"]]
            best_params = self._optimize(strategy_class, is_data, param_grid)

            # Out-of-sample test
            oos_data = data[window["oos_start"]:window["oos_end"]]
            oos_result = self._backtest(strategy_class, oos_data, best_params)

            results.append({
                "window_start": window["oos_start"],
                "window_end": window["oos_end"],
                "best_params": best_params,
                "oos_pnl": oos_result["total_pnl"],
                "oos_sharpe": oos_result["sharpe"],
                "oos_fill_rate": oos_result["fill_rate"],
                "oos_adverse_rate": oos_result["adverse_rate"],
            })

        return pd.DataFrame(results)
```

### 4.2 Monte Carlo Simulation

Stress-test results by randomizing trade sequences and parameters.

```python
class MonteCarloAnalyzer:
    """
    Monte Carlo analysis for market making backtest results.

    Two modes:
    1. Trade-shuffle: Randomize the order of fills to test sensitivity
    2. Parameter perturbation: Jitter strategy params to test robustness
    """

    def trade_shuffle_analysis(self, fills: List[dict],
                                  n_simulations: int = 10000) -> dict:
        """
        Shuffle the sequence of fills and compute PnL distribution.
        If original PnL is well within the shuffled distribution,
        the result may be due to luck rather than skill.
        """
        original_pnl = sum(f["pnl"] for f in fills)
        simulated_pnls = []

        for _ in range(n_simulations):
            shuffled = fills.copy()
            np.random.shuffle(shuffled)

            # Recompute PnL with inventory effects
            sim_pnl = self._simulate_sequential_pnl(shuffled)
            simulated_pnls.append(sim_pnl)

        simulated_pnls = np.array(simulated_pnls)

        return {
            "original_pnl": original_pnl,
            "sim_mean": np.mean(simulated_pnls),
            "sim_std": np.std(simulated_pnls),
            "sim_5th_percentile": np.percentile(simulated_pnls, 5),
            "sim_95th_percentile": np.percentile(simulated_pnls, 95),
            "percentile_rank": (simulated_pnls < original_pnl).mean() * 100,
            "z_score": (original_pnl - np.mean(simulated_pnls)) / max(np.std(simulated_pnls), 1e-10),
            "is_significant_95": original_pnl > np.percentile(simulated_pnls, 95),
        }

    def parameter_sensitivity(self, strategy_class, data,
                                 base_params: dict,
                                 perturbation_pct: float = 0.2,
                                 n_simulations: int = 1000) -> dict:
        """
        Perturb each parameter by +/- perturbation_pct and check result stability.
        Robust strategies should be profitable across parameter perturbations.
        """
        results = []

        for _ in range(n_simulations):
            perturbed_params = {}
            for key, value in base_params.items():
                if isinstance(value, (int, float)):
                    noise = np.random.uniform(1 - perturbation_pct,
                                                1 + perturbation_pct)
                    perturbed_params[key] = value * noise
                else:
                    perturbed_params[key] = value

            result = self._run_backtest(strategy_class, data, perturbed_params)
            results.append(result["total_pnl"])

        results = np.array(results)
        return {
            "base_pnl": self._run_backtest(strategy_class, data, base_params)["total_pnl"],
            "perturbed_mean": np.mean(results),
            "perturbed_std": np.std(results),
            "pct_profitable": (results > 0).mean() * 100,
            "worst_case": np.min(results),
            "best_case": np.max(results),
        }
```

### 4.3 Statistical Significance Testing

```python
def test_backtest_significance(oos_returns: pd.Series,
                                  n_bootstrap: int = 10000) -> dict:
    """
    Test whether backtest returns are statistically significant.

    Uses bootstrap confidence intervals for the mean return
    and a t-test against the null hypothesis of zero mean.
    """
    from scipy import stats

    # T-test: is mean return significantly different from zero?
    t_stat, p_value = stats.ttest_1samp(oos_returns, 0)

    # Bootstrap confidence interval for mean return
    boot_means = []
    n = len(oos_returns)
    for _ in range(n_bootstrap):
        sample = np.random.choice(oos_returns, size=n, replace=True)
        boot_means.append(np.mean(sample))

    boot_means = np.array(boot_means)

    # Minimum sample requirements
    # Rule of thumb: need at least 30 independent observations (markets/days)
    sufficient_data = len(oos_returns) >= 30

    return {
        "mean_return": oos_returns.mean(),
        "t_statistic": t_stat,
        "p_value": p_value,
        "significant_at_95": p_value < 0.05,
        "significant_at_99": p_value < 0.01,
        "ci_95_lower": np.percentile(boot_means, 2.5),
        "ci_95_upper": np.percentile(boot_means, 97.5),
        "sufficient_data": sufficient_data,
        "n_observations": len(oos_returns),
    }
```

### 4.4 Minimum Sample Sizes

| What You're Testing | Minimum Sample | Rationale |
|--------------------|--------------:|-----------|
| Strategy profitability | 30+ independent market days | Central limit theorem |
| Fill rate estimation | 100+ quote-periods with fills | Binomial proportion CI |
| Adverse selection ratio | 50+ fills | Need enough fills to estimate ratio |
| Walk-forward stability | 8+ out-of-sample windows | Enough windows to assess consistency |
| Per-ticker profitability | 20+ markets per ticker | Ticker-specific effects |
| Probability calibration | 100+ markets with outcomes | Brier score reliability |

---

## 5. Visualization and Reporting

### 5.1 Essential Plots

Every backtest report should include these visualizations:

1. **Equity Curve** — Cumulative PnL over time with drawdown overlay
2. **PnL Decomposition** — Stacked area: spread capture, inventory PnL, adverse selection
3. **Inventory Over Time** — Net position with highlight bands for high-inventory periods
4. **Fill Analysis** — Scatter plot of fills on price chart; color by adverse/non-adverse
5. **Probability Calibration** — Predicted vs observed frequency (45-degree line = perfect)
6. **Edge Decay** — Average PnL per fill at 1m, 5m, 15m, 30m, 60m horizons
7. **PnL by Market** — Bar chart of PnL per ticker/strike/expiry combination
8. **Parameter Sensitivity** — Heatmap of Sharpe ratio across parameter grid
9. **Walk-Forward Results** — OOS Sharpe per window over time
10. **Monte Carlo Distribution** — Histogram of simulated PnLs with observed PnL marked

### 5.2 Reporting Template

```python
def generate_backtest_report(results: dict) -> str:
    """Generate a markdown report for a backtest run."""

    report = f"""# Backtest Report: {results['strategy_name']}

## Summary
- **Period:** {results['start_date']} to {results['end_date']}
- **Markets traded:** {results['n_markets']}
- **Tickers:** {', '.join(results['tickers'])}

## Performance
| Metric | Value |
|--------|-------|
| Total PnL | ${results['total_pnl']:.2f} |
| Sharpe Ratio | {results['sharpe']:.2f} |
| Sortino Ratio | {results['sortino']:.2f} |
| Max Drawdown | {results['max_drawdown']:.1%} |
| Profit Factor | {results['profit_factor']:.2f} |

## PnL Decomposition
| Component | Value | % of Gross |
|-----------|-------|-----------|
| Spread Capture | ${results['spread_capture']:.2f} | {results['spread_pct']:.1%} |
| Inventory PnL | ${results['inventory_pnl']:.2f} | {results['inv_pct']:.1%} |
| Adverse Selection | ${results['adverse_selection']:.2f} | {results['adverse_pct']:.1%} |
| Fees | ${results['fees']:.2f} | {results['fees_pct']:.1%} |
| Resolution PnL | ${results['resolution_pnl']:.2f} | {results['resolution_pct']:.1%} |

## Execution Quality
| Metric | Value |
|--------|-------|
| Total Fills | {results['total_fills']} |
| Fill Rate | {results['fill_rate']:.1%} |
| Adverse Selection Rate | {results['adverse_rate']:.1%} |
| Avg Realized Spread | ${results['avg_realized_spread']:.4f} |
| Inventory Turnover | {results['inventory_turnover']:.1f}x |

## Statistical Significance
| Test | Result |
|------|--------|
| p-value | {results['p_value']:.4f} |
| Significant at 95%? | {'Yes' if results['significant_95'] else 'No'} |
| Monte Carlo percentile | {results['mc_percentile']:.1f}th |
| Walk-forward consistency | {results['wf_pct_profitable']:.0f}% windows profitable |
"""
    return report
```

---

## 6. Benchmarks and Targets

### 6.1 What "Good" Looks Like

Based on the research literature and empirical studies of market making strategies:

| Metric | Poor | Acceptable | Good | Suspicious |
|--------|------|-----------|------|------------|
| Sharpe Ratio (OOS) | < 0.5 | 0.5-1.5 | 1.5-3.0 | > 5.0 |
| Fill Rate | < 5% | 5-15% | 15-30% | > 50% |
| Adverse Selection Rate | < 30% | 50-70% | 70-85% | < 20% |
| Profit Factor | < 1.0 | 1.0-1.3 | 1.3-2.0 | > 3.0 |
| Net Spread Capture (per fill) | Negative | $0.001-0.005 | $0.005-0.02 | > $0.05 |
| Max Drawdown | > 30% | 15-30% | 5-15% | 0% |
| Walk-Forward Win Rate | < 40% | 40-60% | 60-80% | 100% |
| Monte Carlo p-value | > 0.10 | 0.05-0.10 | < 0.05 | — |

### 6.2 Comparison Benchmarks

| Benchmark | Description | Expected Sharpe |
|-----------|-------------|----------------|
| Random quoting | Quote at random prices around mid | ~0 (slightly negative after fees) |
| Fixed-spread quoting | Quote at mid +/- 2% | 0.3-0.8 depending on fill model |
| B-L fair value quoting | Quote around options-derived fair value | Target: 1.0-2.0 |
| Inventory-aware quoting | Avellaneda-Stoikov with B-L fair value | Target: 1.5-3.0 |

---

## References

- [[Backtesting-Architecture]] — Engine design and fill simulation
- [[Backtesting-Plan]] — Implementation plan
- [[Breeden-Litzenberger-Pipeline]] — Probability extraction methodology
- [[Core-Market-Making-Strategies]] — Strategy formulations
- [[Inventory-and-Risk-Management]] — Inventory management framework
- [[Risk-Neutral-vs-Physical-Probabilities]] — Risk premium considerations
