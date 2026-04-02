---
title: Order Flow Analysis Strategies
created: 2026-04-02
tags:
  - order-flow
  - VPIN
  - adverse-selection
  - toxicity
  - trades
  - market-making
  - polymarket
sources:
  - https://www.quantresearch.org/VPIN.pdf
  - https://cfr.ivo-welch.info/published/papers/easley-prado-hara.pdf
  - https://www.acsu.buffalo.edu/~keechung/MGF743/Readings/B3%20Glosten%20and%20Harris,%201988%20JFE.pdf
  - https://www.stern.nyu.edu/sites/default/files/assets/documents/con_035928.pdf
  - https://arxiv.org/html/2408.03594v1
  - https://link.springer.com/article/10.1007/s42786-024-00049-8
  - https://arxiv.org/pdf/1502.04592
  - https://www.sciencedirect.com/science/article/abs/pii/S0304405X19302272
  - https://www.mdpi.com/1099-4300/24/2/214
  - https://medium.com/@kryptonlabs/vpin-the-coolest-market-metric-youve-never-heard-of-e7b3d6cbacf1
---

# Order Flow Analysis Strategies

Order flow analysis and toxicity detection for Polymarket binary market making on stock/index events ("Will NVDA close above $120 on April 2?"). The [[NVDA-POC-Results]] demonstrated that **adverse selection is the dominant challenge**: the L2 backtest lost $18.10 while a naive midpoint-only simulation showed +$620.70 profit. The $638.80 gap is almost entirely adverse selection cost -- we get filled when the market moves against us and miss fills when it moves in our favor.

This note catalogs strategies for measuring, detecting, and adapting to toxic order flow using the data available to us: tick-level trades and L2 orderbook from Telonex, onchain fills with maker/taker wallet addresses from Telonex and Polymarket subgraphs, and real-time WebSocket streams from Polymarket's CLOB.

**Related notes:** [[Polymarket-Data-API]], [[Polymarket-CLOB-Mechanics]], [[Inventory-and-Risk-Management]], [[NVDA-POC-Results]], [[Core-Market-Making-Strategies]], [[Fill-Simulation-Research]]

---

## 1. VPIN for Polymarket

### Concept

Volume-Synchronized Probability of Informed Trading (VPIN) is a real-time estimator of order flow toxicity developed by Easley, Lopez de Prado, and O'Hara (2012). Unlike the original PIN model that requires maximum likelihood estimation, VPIN uses volume bucketing to synchronize sampling with market activity. The key insight is that information arrival correlates with volume, not clock time -- so measuring order imbalance in volume-time produces a more accurate toxicity signal.

VPIN famously produced a warning signal hours before the 2010 Flash Crash.

### Mathematical Framework

#### Step 1: Volume Bucketing

Divide the trade stream into equal-volume buckets of size $V_B$. Each bucket $n$ contains trades whose cumulative volume sums to exactly $V_B$ USDC. A single trade may be split across two buckets if it straddles a boundary.

For Polymarket binary markets:
- **Recommended $V_B$**: 50-200 USDC (markets are thin; typical daily volume for an NVDA strike is $5K-$50K)
- **Calibration**: Target 30-100 buckets per trading session (9:30 AM - 4:00 PM ET for stock binary markets)

#### Step 2: Buy/Sell Classification (Bulk Volume Classification)

For each trade, classify the volume as buy or sell. On Polymarket, the classification is straightforward from the trade data:

| Trade Action | Classification | Rationale |
|---|---|---|
| Buy YES token | **Bullish** (buy volume) | Trader expects price to go up (event more likely) |
| Sell YES token | **Bearish** (sell volume) | Trader expects price to go down (event less likely) |
| Buy NO token | **Bearish** (sell volume) | Equivalent to selling YES |
| Sell NO token | **Bullish** (buy volume) | Equivalent to buying YES |

The Polymarket `last_trade_price` WebSocket event includes `side` (BUY/SELL) and the Telonex `trades` channel includes side, so classification is direct -- no need for the Bulk Volume Classification (BVC) approximation used in traditional markets where trade direction is ambiguous.

> [!note] Advantage Over Traditional Markets
> In equity markets, VPIN implementations must infer trade direction using the Lee-Ready algorithm or BVC. On Polymarket, the CLOB provides explicit side information per trade, eliminating classification noise.

#### Step 3: Order Imbalance Per Bucket

For each volume bucket $n$:

$$
OI_n = |V_n^{\text{buy}} - V_n^{\text{sell}}|
$$

where $V_n^{\text{buy}}$ and $V_n^{\text{sell}}$ are the total bullish and bearish volumes in bucket $n$, with $V_n^{\text{buy}} + V_n^{\text{sell}} = V_B$.

#### Step 4: VPIN Calculation

Compute VPIN as the rolling average of normalized order imbalance over the last $N$ buckets:

$$
\text{VPIN} = \frac{1}{N} \sum_{n=1}^{N} \frac{OI_n}{V_B}
$$

VPIN ranges from 0 (perfectly balanced flow) to 1 (completely one-sided flow).

**Recommended parameters for Polymarket:**

| Parameter | Value | Rationale |
|---|---|---|
| $V_B$ (bucket size) | 100 USDC | ~50-100 buckets per active session on ATM strikes |
| $N$ (lookback) | 20-30 buckets | Shorter than equity markets (50) due to thinner books and shorter-lived markets |
| Update frequency | Every new bucket | Volume-clock, not wall-clock |

### Spread Adjustment Protocol

Use VPIN to dynamically widen spreads when informed trading is detected:

| VPIN Range | Toxicity Level | Spread Adjustment | Action |
|---|---|---|---|
| $< 0.3$ | Low | Base spread ($\delta$) | Normal quoting |
| $0.3 - 0.5$ | Moderate | $1.25 \times \delta$ (+25%) | Widen slightly, maintain presence |
| $0.5 - 0.7$ | High | $1.50 \times \delta$ (+50%) | Significant widening |
| $> 0.7$ | Extreme | **Pull quotes** | Exit market, wait for VPIN to decay |

**Asymmetric adjustment** (optional enhancement): If $V_n^{\text{buy}} \gg V_n^{\text{sell}}$, widen the ask more than the bid (informed buying lifts the true price, so our ask is more likely to be stale):

$$
\delta_{\text{ask}} = \delta \times (1 + \alpha \cdot \text{VPIN} \cdot \text{sign}(\text{flow}))
$$
$$
\delta_{\text{bid}} = \delta \times (1 + \alpha \cdot \text{VPIN} \cdot (1 - \text{sign}(\text{flow}})))
$$

where $\text{sign}(\text{flow}) = 1$ if net buy pressure, $0$ if net sell pressure, and $\alpha \in [0.5, 1.5]$ is a tuning parameter.

### Data Requirements

| Data Source | Fields Used | Availability |
|---|---|---|
| Telonex `trades` | timestamp, price, size, side | Historical (from 2025-10-11) |
| Polymarket WS `last_trade_price` | price, side, size, timestamp | Real-time |
| Telonex `onchain_fills` | price, size, maker/taker addresses | Historical (from 2022-11-21) |

### Testability in Backtesting Engine

**Fully testable.** The backtesting engine can compute VPIN from Telonex `trades` data, then evaluate the spread adjustment protocol against L2 fill simulation. Test plan:

1. Compute VPIN time series for historical NVDA markets using Telonex trades
2. Run the existing probability-based quoting strategy with VPIN-adjusted spreads
3. Compare P&L vs the static-spread baseline from [[NVDA-POC-Results]]
4. Measure: does VPIN-adjusted quoting reduce adverse selection losses on the $165 ATM strike?

### VPIN Implementation Specification

```python
"""
VPIN Implementation for Polymarket Binary Markets
Uses Telonex trades data (timestamp, price, size, side)
"""
from dataclasses import dataclass, field
from collections import deque
import numpy as np


@dataclass
class VPINConfig:
    bucket_size: float = 100.0      # V_B in USDC
    lookback_buckets: int = 25      # N rolling window
    # Spread adjustment thresholds
    low_threshold: float = 0.3
    moderate_threshold: float = 0.5
    high_threshold: float = 0.7


@dataclass
class VolumeBucket:
    buy_volume: float = 0.0
    sell_volume: float = 0.0

    @property
    def total_volume(self) -> float:
        return self.buy_volume + self.sell_volume

    @property
    def order_imbalance(self) -> float:
        return abs(self.buy_volume - self.sell_volume)

    @property
    def net_direction(self) -> int:
        """Returns +1 if net buying, -1 if net selling, 0 if balanced."""
        if self.buy_volume > self.sell_volume:
            return 1
        elif self.sell_volume > self.buy_volume:
            return -1
        return 0


class VPINCalculator:
    """
    Real-time VPIN calculator for a single Polymarket binary market.

    Usage:
        vpin = VPINCalculator(VPINConfig(bucket_size=100, lookback_buckets=25))

        for trade in trade_stream:
            is_buy = classify_trade(trade)
            vpin.update(trade.size_usdc, is_buy)

            if vpin.is_ready():
                current_vpin = vpin.value()
                spread_mult = vpin.spread_multiplier()
    """

    def __init__(self, config: VPINConfig):
        self.config = config
        self.completed_buckets: deque[VolumeBucket] = deque(
            maxlen=config.lookback_buckets
        )
        self.current_bucket = VolumeBucket()
        self._vpin_value: float | None = None

    def classify_trade(self, side: str, outcome: str) -> bool:
        """
        Classify a Polymarket trade as bullish (True) or bearish (False).

        Args:
            side: "BUY" or "SELL" from the trade data
            outcome: "Yes" or "No" token being traded

        Returns:
            True if the trade is bullish (buy pressure), False if bearish
        """
        if outcome.upper() in ("YES", "Y"):
            return side.upper() == "BUY"
        else:  # NO token
            return side.upper() == "SELL"

    def update(self, volume_usdc: float, is_bullish: bool) -> None:
        """
        Process a new trade. May complete one or more volume buckets.

        Args:
            volume_usdc: Trade size in USDC (price * shares)
            is_bullish: True if classified as buy pressure
        """
        remaining = volume_usdc

        while remaining > 0:
            space_in_bucket = self.config.bucket_size - self.current_bucket.total_volume
            fill_amount = min(remaining, space_in_bucket)

            if is_bullish:
                self.current_bucket.buy_volume += fill_amount
            else:
                self.current_bucket.sell_volume += fill_amount

            remaining -= fill_amount

            # Bucket is full -- finalize it
            if self.current_bucket.total_volume >= self.config.bucket_size - 1e-9:
                self.completed_buckets.append(self.current_bucket)
                self.current_bucket = VolumeBucket()
                self._recompute_vpin()

    def _recompute_vpin(self) -> None:
        """Recompute VPIN from completed buckets."""
        if len(self.completed_buckets) < self.config.lookback_buckets:
            self._vpin_value = None
            return

        total_oi = sum(b.order_imbalance for b in self.completed_buckets)
        n = len(self.completed_buckets)
        self._vpin_value = total_oi / (n * self.config.bucket_size)

    def is_ready(self) -> bool:
        """Returns True when enough buckets have been accumulated."""
        return self._vpin_value is not None

    def value(self) -> float:
        """Current VPIN value in [0, 1]. Returns 0.5 if not ready."""
        return self._vpin_value if self._vpin_value is not None else 0.5

    def net_flow_direction(self) -> int:
        """
        Net flow direction over the lookback window.
        +1 = net buying pressure, -1 = net selling pressure.
        """
        if not self.completed_buckets:
            return 0
        total_buy = sum(b.buy_volume for b in self.completed_buckets)
        total_sell = sum(b.sell_volume for b in self.completed_buckets)
        return 1 if total_buy > total_sell else -1

    def spread_multiplier(self) -> float:
        """
        Returns the spread multiplier based on VPIN thresholds.

        Returns:
            float: Multiplier for the base spread.
                   Returns float('inf') if quotes should be pulled.
        """
        v = self.value()

        if v < self.config.low_threshold:
            return 1.0              # Base spread
        elif v < self.config.moderate_threshold:
            return 1.25             # +25%
        elif v < self.config.high_threshold:
            return 1.50             # +50%
        else:
            return float('inf')     # Pull quotes

    def asymmetric_spreads(
        self, base_half_spread: float, alpha: float = 1.0
    ) -> tuple[float, float]:
        """
        Compute asymmetric bid/ask half-spreads based on VPIN and flow direction.

        Args:
            base_half_spread: The base half-spread (e.g., 0.02)
            alpha: Asymmetry strength parameter [0.5, 1.5]

        Returns:
            (bid_half_spread, ask_half_spread)
        """
        v = self.value()

        if v >= self.config.high_threshold:
            return (float('inf'), float('inf'))  # Pull quotes

        direction = self.net_flow_direction()

        if direction >= 0:
            # Net buying: widen ask more (informed buyers lifting price)
            ask_mult = 1.0 + alpha * v
            bid_mult = 1.0 + alpha * v * 0.5
        else:
            # Net selling: widen bid more (informed sellers pushing price down)
            bid_mult = 1.0 + alpha * v
            ask_mult = 1.0 + alpha * v * 0.5

        return (base_half_spread * bid_mult, base_half_spread * ask_mult)

    def reset(self) -> None:
        """Reset state for a new market/session."""
        self.completed_buckets.clear()
        self.current_bucket = VolumeBucket()
        self._vpin_value = None
```

### Citation

> Easley, D., Lopez de Prado, M., & O'Hara, M. (2012). "Flow Toxicity and Liquidity in a High Frequency World." *Review of Financial Studies*, 25(5), 1457-1493. [PDF](https://www.quantresearch.org/VPIN.pdf)

---

## 2. Adverse Selection Decomposition

### Concept

The bid-ask spread earned by a market maker can be decomposed into a **realized profit** component and an **adverse selection cost** component. The realized spread is what the market maker actually keeps; the adverse selection component is what informed traders extract. If adverse selection exceeds the spread capture, the strategy is losing money to informed flow.

This decomposition, originating with Glosten & Harris (1988) and extended by Huang & Stoll (1996), is the most direct measurement of the problem identified in the [[NVDA-POC-Results]].

### Mathematical Framework

#### Effective Spread

The effective spread is the round-trip cost of a trade relative to the midpoint:

$$
\text{EffectiveSpread}_i = 2 \cdot D_i \cdot (P_i^{\text{fill}} - M_i)
$$

where:
- $P_i^{\text{fill}}$ = execution price of trade $i$
- $M_i$ = mid-price at the time of trade $i$
- $D_i$ = trade direction indicator: $+1$ for buys, $-1$ for sells

#### Realized Spread

The realized spread measures the market maker's actual profit, evaluated at a future horizon $\tau$:

$$
\text{RealizedSpread}_i(\tau) = 2 \cdot D_i \cdot (P_i^{\text{fill}} - M_{i+\tau})
$$

where $M_{i+\tau}$ is the mid-price at time $\tau$ after the fill. This captures how much the market maker keeps after the price has moved.

#### Adverse Selection Component

The adverse selection component is the price impact -- how much the mid-price moved against the market maker:

$$
\text{AdverseSelection}_i(\tau) = 2 \cdot D_i \cdot (M_{i+\tau} - M_i)
$$

#### Decomposition Identity

The effective spread decomposes exactly:

$$
\text{EffectiveSpread}_i = \text{RealizedSpread}_i(\tau) + \text{AdverseSelection}_i(\tau)
$$

This can be verified by expanding the right side:

$$
2D(P - M_{t+\tau}) + 2D(M_{t+\tau} - M_t) = 2D(P - M_t)
$$

#### Choosing the Horizon $\tau$

The choice of $\tau$ is critical. Too short, and the price impact has not been fully realized. Too long, and noise dominates.

**Recommended horizons for Polymarket binary markets:**

| Horizon $\tau$ | Purpose | Rationale |
|---|---|---|
| 1 second | Immediate adverse selection | Captures HFT-style toxic flow |
| 10 seconds | Short-term price impact | Primary measurement horizon for thin Polymarket books |
| 60 seconds | Medium-term information | Captures most informed flow on event markets |
| 5 minutes | Full information incorporation | Upper bound; beyond this, new information confounds the signal |

Research on traditional equity markets suggests that the majority of price impact occurs within 15 seconds for large caps and 60 seconds for small caps (Hendershott & Menkveld, 2014). Polymarket's thin books likely behave more like small-cap equities: use $\tau = 60s$ as the primary measurement horizon.

### Interpretation Rules

| Condition | Interpretation | Action |
|---|---|---|
| $\text{AS}(\tau) < 0.5 \times \text{EffSpread}$ | Low adverse selection | Strategy is capturing most of the spread |
| $\text{AS}(\tau) \approx \text{EffSpread}$ | Breakeven | Spread capture equals adverse selection cost |
| $\text{AS}(\tau) > \text{EffSpread}$ | Losing to informed flow | Widen spreads, reduce size, or exit market |
| $\text{AS}(1s) \gg \text{AS}(60s)$ | Fast information -- possible HFT | Increase quote update frequency |
| $\text{AS}(60s) \gg \text{AS}(1s)$ | Slow information -- fundamental flow | VPIN/flow detection more useful |

### Adverse Selection Decomposition Measurement Specification

```python
"""
Adverse Selection Decomposition for the Backtesting Engine.

Integrates with bt_engine to measure realized spread and adverse
selection component per fill, using Telonex quotes (BBO) data to
reconstruct mid-prices at each horizon.
"""
from dataclasses import dataclass
import numpy as np
from typing import Sequence


@dataclass
class Fill:
    """A single fill from the backtesting engine."""
    timestamp_ms: int       # Fill timestamp in milliseconds
    price: float            # Execution price
    side: int               # +1 for buy, -1 for sell (from our perspective as MM)
    size: float             # Number of shares
    market_id: str          # Condition ID


@dataclass
class MidPriceSnapshot:
    """Mid-price at a point in time, from Telonex quotes."""
    timestamp_ms: int
    mid_price: float


@dataclass
class ASDecomposition:
    """Adverse selection decomposition for a single fill at a given horizon."""
    fill_timestamp_ms: int
    fill_price: float
    mid_at_fill: float
    mid_at_horizon: float
    horizon_seconds: float
    trade_direction: int        # +1 buy, -1 sell (taker side)
    effective_spread: float
    realized_spread: float
    adverse_selection: float
    size: float


class AdverseSelectionAnalyzer:
    """
    Computes spread decomposition for all fills against historical mid-prices.

    Usage:
        analyzer = AdverseSelectionAnalyzer(
            horizons_sec=[1, 10, 60, 300],
            mid_prices=load_telonex_quotes(market_id, date)
        )

        for fill in backtest_fills:
            decompositions = analyzer.decompose(fill)
            for d in decompositions:
                print(f"tau={d.horizon_seconds}s: "
                      f"eff={d.effective_spread:.4f} "
                      f"real={d.realized_spread:.4f} "
                      f"AS={d.adverse_selection:.4f}")

        summary = analyzer.summary()
    """

    def __init__(
        self,
        horizons_sec: list[float],
        mid_prices: list[MidPriceSnapshot],
    ):
        self.horizons_sec = horizons_sec
        # Build sorted arrays for fast lookup
        self._mid_timestamps = np.array(
            [m.timestamp_ms for m in mid_prices], dtype=np.int64
        )
        self._mid_values = np.array(
            [m.mid_price for m in mid_prices], dtype=np.float64
        )
        self._results: list[ASDecomposition] = []

    def _get_mid_at_time(self, timestamp_ms: int) -> float | None:
        """Find the mid-price at or just before the given timestamp."""
        idx = np.searchsorted(self._mid_timestamps, timestamp_ms, side="right") - 1
        if idx < 0:
            return None
        return float(self._mid_values[idx])

    def decompose(self, fill: Fill) -> list[ASDecomposition]:
        """
        Decompose a fill into effective spread, realized spread, and
        adverse selection at each measurement horizon.

        The fill's side represents the TAKER direction (the counterparty).
        As the market maker, we are on the opposite side:
        - Taker buys (D=+1) means we sold (we are short)
        - Taker sells (D=-1) means we bought (we are long)

        Args:
            fill: A Fill object from the backtesting engine

        Returns:
            List of ASDecomposition objects, one per horizon
        """
        # D is the taker direction (not ours)
        D = fill.side
        mid_at_fill = self._get_mid_at_time(fill.timestamp_ms)
        if mid_at_fill is None:
            return []

        results = []
        for tau in self.horizons_sec:
            horizon_ms = fill.timestamp_ms + int(tau * 1000)
            mid_at_horizon = self._get_mid_at_time(horizon_ms)
            if mid_at_horizon is None:
                continue

            eff_spread = 2 * D * (fill.price - mid_at_fill)
            real_spread = 2 * D * (fill.price - mid_at_horizon)
            as_component = 2 * D * (mid_at_horizon - mid_at_fill)

            decomp = ASDecomposition(
                fill_timestamp_ms=fill.timestamp_ms,
                fill_price=fill.price,
                mid_at_fill=mid_at_fill,
                mid_at_horizon=mid_at_horizon,
                horizon_seconds=tau,
                trade_direction=D,
                effective_spread=eff_spread,
                realized_spread=real_spread,
                adverse_selection=as_component,
                size=fill.size,
            )
            results.append(decomp)
            self._results.append(decomp)

        return results

    def summary(self) -> dict[float, dict[str, float]]:
        """
        Compute volume-weighted averages per horizon.

        Returns:
            {horizon_sec: {
                "avg_effective_spread": ...,
                "avg_realized_spread": ...,
                "avg_adverse_selection": ...,
                "as_fraction": ...,       # AS / Effective Spread
                "n_fills": ...,
            }}
        """
        summary = {}
        for tau in self.horizons_sec:
            horizon_results = [r for r in self._results if r.horizon_seconds == tau]
            if not horizon_results:
                continue

            total_size = sum(r.size for r in horizon_results)
            avg_eff = sum(r.effective_spread * r.size for r in horizon_results) / total_size
            avg_real = sum(r.realized_spread * r.size for r in horizon_results) / total_size
            avg_as = sum(r.adverse_selection * r.size for r in horizon_results) / total_size

            summary[tau] = {
                "avg_effective_spread": avg_eff,
                "avg_realized_spread": avg_real,
                "avg_adverse_selection": avg_as,
                "as_fraction": avg_as / avg_eff if abs(avg_eff) > 1e-9 else float('nan'),
                "n_fills": len(horizon_results),
            }

        return summary
```

### Application to NVDA POC

From the [[NVDA-POC-Results]], the L2 backtest on the $165 strike had 163 fills with a net P&L of -$28.60. Using this decomposition:

1. Load the Telonex `quotes` data for the NVDA $165 market on March 30, 2026
2. For each of the 163 fills, compute the decomposition at $\tau \in \{1, 10, 60, 300\}$ seconds
3. If $\text{AS}(60s) > \text{EffSpread}$, confirms the strategy is systematically losing to informed traders
4. The ratio $\text{AS}(60s) / \text{EffSpread}$ tells us how much wider the spread needs to be to break even

### Citation

> Glosten, L.R. & Harris, L.E. (1988). "Estimating the Components of the Bid/Ask Spread." *Journal of Financial Economics*, 21(1), 123-142. [PDF](https://www.acsu.buffalo.edu/~keechung/MGF743/Readings/B3%20Glosten%20and%20Harris,%201988%20JFE.pdf)

> Huang, R.D. & Stoll, H.R. (1996). "Dealer versus Auction Markets: A Paired Comparison of Execution Costs on NASDAQ and the NYSE." *Journal of Financial Economics*, 41(3), 313-357.

---

## 3. Trade Flow Imbalance (TFI)

### Concept

Trade Flow Imbalance measures the directional pressure in the order flow over short time windows. Unlike VPIN (which uses volume buckets), TFI operates in clock time and is designed for burst detection -- identifying sudden shifts in flow that predict near-term price moves.

### Metrics

#### Signed Volume

Aggregate net buying/selling pressure over a rolling window $w$:

$$
\text{SV}(t, w) = \sum_{i: t_i \in [t-w, t]} D_i \cdot q_i
$$

where $D_i = +1$ for buys, $-1$ for sells, and $q_i$ is the USDC volume of trade $i$.

#### Net Order Flow (NOF)

Count-based metric ignoring trade size:

$$
\text{NOF}(t, w) = \sum_{i: t_i \in [t-w, t]} D_i
$$

Useful when large trades are split into many smaller fills (common on Polymarket where traders often execute in 10-50 share increments).

#### Trade Arrival Rate Asymmetry (TARA)

Ratio of buy arrival rate to sell arrival rate:

$$
\text{TARA}(t, w) = \frac{\lambda_{\text{buy}}(t, w)}{\lambda_{\text{sell}}(t, w)} = \frac{N_{\text{buy}}(t, w)}{N_{\text{sell}}(t, w)}
$$

TARA $> 1.5$ or $< 0.67$ indicates significant directional pressure.

#### Burst Detection

Define a flow burst as a period where:

$$
|\text{SV}(t, w)| > k \cdot \sigma_{\text{SV}}
$$

where $\sigma_{\text{SV}}$ is the rolling standard deviation of signed volume and $k \in [2, 3]$ is a sensitivity parameter.

**Recommended window sizes for Polymarket:**

| Window $w$ | Purpose |
|---|---|
| 30 seconds | Micro-burst detection (immediate quote adjustment) |
| 2 minutes | Short-term flow regime |
| 10 minutes | Medium-term directional assessment |

### Quoting Response

When a burst is detected:

1. **Fade the flow** (contrarian): Temporarily widen the spread on the side being hit. If buy burst, widen the ask by an additional $\delta_{\text{burst}}$.
2. **Follow the flow** (momentum): If the burst aligns with a fair value shift (e.g., underlying stock moving), shift the mid in the direction of flow.
3. **Hybrid**: Widen the spread AND shift the mid slightly in the direction of flow.

The correct response depends on whether the flow is **informed** (follow) or **noise** (fade). Cross-reference with Section 6 (Cross-Platform Flow) to distinguish.

### Data Requirements

| Data Source | Fields | Use |
|---|---|---|
| Telonex `trades` | timestamp, price, size, side | Historical TFI computation |
| Polymarket WS `last_trade_price` | price, side, size, timestamp | Real-time burst detection |
| Telonex `quotes` | timestamp, best_bid, best_ask | Mid-price for validating burst predictiveness |

### Testability

**Fully testable.** Compute TFI metrics from historical trades. Measure predictive power: after a burst at time $t$, does the mid-price move in the burst direction at $t + 30s$, $t + 60s$, $t + 5m$? If predictive, integrate as a quoting signal.

---

## 4. Informed Trader Detection

### Concept

Identify specific wallet addresses and behavioral patterns associated with informed trading. On Polymarket, onchain settlement means every trade is traceable to a wallet, providing a level of counterparty transparency impossible in traditional markets.

### Detection Signals

#### Trade Size Clustering

Informed traders often trade in distinctive size patterns:

$$
\text{SizeEntropy}(w) = -\sum_{s \in \text{sizes}} p(s) \log p(s)
$$

where $p(s)$ is the frequency of each trade size in a wallet's recent history. Low entropy = consistent sizing = likely algorithmic/informed. High entropy = varied sizing = likely retail/noise.

**Polymarket-specific pattern:** Retail traders tend to use round numbers ($10, $25, $50, $100). Algorithmic traders use precise sizes ($23.47, $41.89) to optimize for queue position or fee thresholds.

#### Aggressive Spread Crossing

Informed traders disproportionately take liquidity (cross the spread) rather than providing it:

$$
\text{AggressionRatio}(w) = \frac{N_{\text{taker}}}{N_{\text{taker}} + N_{\text{maker}}}
$$

An aggression ratio $> 0.8$ over a sustained window suggests informed flow from that wallet.

#### Time-of-Day Patterns

For stock/index binary markets:
- **Informed flow concentrates around market events**: earnings, economic releases, option expiry
- **Pre-market (4:00 AM - 9:30 AM ET)**: Options market not yet liquid; Polymarket flow may lead
- **Market hours (9:30 AM - 4:00 PM ET)**: Options market provides continuous fair value; Polymarket flow that deviates from options-implied probability is potentially informed about something the options market hasn't priced
- **After hours**: Thin liquidity; any flow is potentially high-impact

#### Wallet-Level Analysis via Polymarket Subgraph

The Polymarket Orders subgraph (`orderbook-subgraph`) provides `orderFilledEvents` with maker and taker addresses:

**Subgraph endpoint:**
```
https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn
```

**Query for trade events with addresses:**
```graphql
query InformedFlowAnalysis($market: String!, $minSize: BigDecimal!) {
  orderFilledEvents(
    where: { market: $market, size_gt: $minSize }
    orderBy: timestamp
    orderDirection: desc
    first: 1000
  ) {
    id
    maker
    taker
    makerAssetId
    takerAssetId
    makerAmountFilled
    takerAmountFilled
    fee
    timestamp
    transactionHash
  }
}
```

Telonex `onchain_fills` provides the same data in Parquet format with additional fields (price, `mirrored` flag). Filter `mirrored=false` to avoid double-counting.

**Wallet profiling pipeline:**

1. Collect all `onchain_fills` for target markets (NVDA binary strikes)
2. Group by taker address (the initiating side)
3. For each wallet, compute:
   - Total volume, trade count, average size
   - Aggression ratio (taker vs maker appearances)
   - Win rate (trades where the post-trade mid moved in their favor)
   - Timing: concentration around market events
4. Flag wallets with win rate $> 60\%$ and volume $> \$1000$ as potentially informed
5. In real-time: if a flagged wallet trades, immediately widen or pull quotes

### PIN Model (Easley & O'Hara)

The Probability of INformed Trading (PIN) is the original structural model:

$$
\text{PIN} = \frac{\alpha \mu}{\alpha \mu + 2\epsilon}
$$

where:
- $\alpha$ = probability of an information event
- $\mu$ = arrival rate of informed traders
- $\epsilon$ = arrival rate of uninformed traders

Estimated via MLE on daily buy/sell counts. For Polymarket:
- Use trading sessions as the unit (one session = market hours for a given day)
- Count buy and sell trades per session
- Estimate $(\alpha, \delta, \mu, \epsilon)$ by maximizing the mixed Poisson likelihood

**Limitation:** PIN requires many sessions for reliable estimation. With daily stock binary markets that live only 1-2 days, each market has too few sessions. Aggregate across strikes within the same underlying (e.g., all NVDA markets on a given day) for sufficient sample size.

### Data Requirements

| Data Source | Fields | Use |
|---|---|---|
| Telonex `onchain_fills` | maker, taker, price, size, timestamp | Wallet profiling |
| Polymarket Orders subgraph | orderFilledEvents with addresses | Real-time wallet lookup |
| Telonex `trades` | timestamp, side, size | Trade counting for PIN model |

### Testability

**Testable with historical data.** Profile wallets from past NVDA markets. Measure: do historically flagged wallets predict adverse price moves? Back-test a "fade flagged wallets" overlay on the existing strategy.

---

## 5. Hawkes Process for Trade Arrival

### Concept

A Hawkes process is a self-exciting point process where each event (trade) increases the probability of subsequent events. This captures the empirical observation that trades cluster in time -- one trade triggers more trades. The key application for market making is distinguishing between two regimes:

1. **Noise regime**: Low-intensity, balanced buy/sell flow -- safe to quote tight
2. **Informed burst regime**: High-intensity, one-sided flow -- widen or pull quotes

### Mathematical Framework

#### Univariate Hawkes Process

The intensity (arrival rate) of trades at time $t$:

$$
\lambda(t) = \mu + \sum_{t_i < t} \phi(t - t_i)
$$

where:
- $\mu$ = baseline arrival rate
- $\phi(t - t_i)$ = kernel function measuring how past event $t_i$ excites future arrivals
- Common kernel: exponential $\phi(\Delta t) = \alpha \cdot e^{-\beta \Delta t}$

Parameters:
- $\mu$: background trade rate (trades/second)
- $\alpha$: jump in intensity per trade (excitation magnitude)
- $\beta$: decay rate of excitation (how fast clustering fades)
- $\alpha / \beta$: branching ratio -- fraction of trades that are "triggered" vs "spontaneous". Must be $< 1$ for stationarity.

#### Bivariate Hawkes Process

Model buy and sell arrivals as two coupled processes:

$$
\lambda_{\text{buy}}(t) = \mu_b + \alpha_{bb} \sum_{t_i^b < t} e^{-\beta_{bb}(t - t_i^b)} + \alpha_{sb} \sum_{t_j^s < t} e^{-\beta_{sb}(t - t_j^s)}
$$

$$
\lambda_{\text{sell}}(t) = \mu_s + \alpha_{ss} \sum_{t_j^s < t} e^{-\beta_{ss}(t - t_j^s)} + \alpha_{bs} \sum_{t_i^b < t} e^{-\beta_{bs}(t - t_i^b)}
$$

The cross-excitation terms ($\alpha_{sb}$, $\alpha_{bs}$) capture how buys trigger sells and vice versa:
- **High self-excitation ($\alpha_{bb}, \alpha_{ss}$)**: Momentum traders, stop-loss cascades, or informed traders splitting orders
- **High cross-excitation ($\alpha_{sb}, \alpha_{bs}$)**: Market makers rebalancing, or noise traders on both sides

#### Regime Detection

Define the **instantaneous imbalance** from the bivariate Hawkes:

$$
\text{Imbalance}(t) = \frac{\lambda_{\text{buy}}(t) - \lambda_{\text{sell}}(t)}{\lambda_{\text{buy}}(t) + \lambda_{\text{sell}}(t)}
$$

| Regime | Signature | Action |
|---|---|---|
| Noise | $|\text{Imbalance}| < 0.2$, moderate $\lambda$ | Quote tight, capture spread |
| Informed burst | $|\text{Imbalance}| > 0.5$, high $\lambda$ | Widen or pull quotes |
| Post-event decay | High $\lambda$, declining, balanced | Quote normally, expect mean-reversion |

#### Parameter Estimation

Estimate $(\mu, \alpha, \beta)$ via maximum likelihood on trade timestamps. The log-likelihood for a univariate Hawkes process on interval $[0, T]$ with events $\{t_1, \ldots, t_n\}$:

$$
\log L = \sum_{i=1}^{n} \log \lambda(t_i) - \int_0^T \lambda(t) dt
$$

$$
= \sum_{i=1}^{n} \log \left( \mu + \alpha \sum_{j < i} e^{-\beta(t_i - t_j)} \right) - \mu T - \frac{\alpha}{\beta} \sum_{i=1}^{n} \left(1 - e^{-\beta(T - t_i)}\right)
$$

Use L-BFGS-B or similar optimizer. Libraries: `tick` (Python), `hawkeslib`, or custom implementation.

### Data Requirements

| Data Source | Fields | Use |
|---|---|---|
| Telonex `trades` | timestamp (ms precision), side | Event timestamps for Hawkes estimation |
| Polymarket WS `last_trade_price` | timestamp, side | Real-time intensity tracking |

### Testability

**Testable.** Estimate Hawkes parameters from historical NVDA trade data. Then evaluate:
1. Does the branching ratio $\alpha/\beta$ differ between ATM and OTM strikes?
2. Does high $\lambda_{\text{buy}} / \lambda_{\text{sell}}$ imbalance predict mid-price moves?
3. Back-test a Hawkes-based regime detector as a quoting overlay.

### Citation

> Bacry, E., Mastromatteo, I., & Muzy, J.F. (2015). "Hawkes Processes in Finance." *Market Microstructure and Liquidity*, 1(1). [arXiv:1502.04592](https://arxiv.org/pdf/1502.04592)

> Morariu-Patrichi, M. & Pakkanen, M.S. (2024). "Deep Hawkes Process for High-Frequency Market Making." *Journal of Banking and Financial Technology*. [Springer](https://link.springer.com/article/10.1007/s42786-024-00049-8)

---

## 6. Cross-Platform Flow Analysis

### Concept

Polymarket stock/index binary markets derive their fundamental value from the options market. When the options market shows unusual activity near a binary strike, information may propagate to Polymarket with some latency. Detecting this latency creates a window to adjust quotes preemptively.

### Information Flow Model

```
Options Market Activity          Polymarket Flow
        |                              |
   Unusual volume near K        [Latency gap: seconds to minutes]
        |                              |
   Options IV shift                    |
        |                              |
   Fair value change            Informed flow arrives
        |                              |
   Our model detects             Our quotes are stale
        |                              |
   Adjust quotes preemptively   Avoid adverse selection
```

### Signals to Monitor

#### Options Unusual Activity

Using ThetaData (see [[ThetaData-Options-API]]):

1. **Volume surge**: If call volume at strike $K$ exceeds $3\times$ the 20-day average, something is happening near that strike
2. **IV shift**: Implied volatility change $> 2$ vol points in the options nearest to the binary strike
3. **Skew movement**: Rapid change in the put-call skew near the binary strike

#### Cross-Market Lead-Lag

Measure the lead-lag relationship:

$$
\rho(\tau) = \text{Corr}(\Delta P_{\text{options}}(t), \Delta P_{\text{polymarket}}(t + \tau))
$$

If $\rho(\tau)$ peaks at $\tau > 0$, options lead Polymarket by $\tau$ seconds. This is the exploitable information gap.

For the NVDA binary markets:
- Options update every ~100ms during market hours
- Polymarket mid-price updates less frequently (seconds between trades on active strikes)
- **Hypothesis:** options lead Polymarket by 5-60 seconds on fair value shifts

#### Flow Concordance

When both markets show directional flow simultaneously, the probability of informed trading is higher:

$$
\text{Concordance}(t, w) = \text{sign}(\text{SV}_{\text{poly}}(t, w)) \cdot \text{sign}(\text{SV}_{\text{options}}(t, w))
$$

If concordance is $+1$ (both markets agree on direction), widen spreads. If $-1$ (disagreement), the Polymarket flow may be noise -- opportunity to capture spread.

### Data Requirements

| Data Source | Fields | Use |
|---|---|---|
| ThetaData | Strike-level volume, IV, greeks | Options unusual activity detection |
| Telonex `trades` | timestamp, side, size | Polymarket flow measurement |
| Telonex `quotes` | timestamp, best_bid, best_ask | Polymarket mid-price for lead-lag |
| Breeden-Litzenberger pipeline | Risk-neutral density | Fair value adjustment |

### Testability

**Testable with historical data alignment.** Requires synchronizing ThetaData option timestamps with Telonex Polymarket timestamps. Measure:
1. Lead-lag correlation at various $\tau$ values
2. Does unusual options volume predict Polymarket mid-price moves?
3. Does concordance filtering improve the quoting strategy's realized spread?

---

## 7. Market Impact Estimation

### Concept

Estimate how much a given order moves the Polymarket mid-price. Critical for:
- Sizing orders to minimize self-impact
- Predicting the impact of observed flow (from other traders)
- Calibrating the fill simulator in the backtesting engine

### Temporary vs Permanent Impact

Market impact decomposes into:

$$
\Delta M = \underbrace{\Delta M^{\text{perm}}}_{\text{permanent}} + \underbrace{\Delta M^{\text{temp}}}_{\text{temporary}}
$$

- **Permanent impact**: The portion of the price move that persists -- reflects genuine information
- **Temporary impact**: The portion that reverts -- reflects liquidity displacement

### Kyle's Lambda for Binary Markets

Kyle (1985) models permanent price impact as linear in signed order flow:

$$
\Delta M_t = \lambda \cdot x_t + \epsilon_t
$$

where:
- $\Delta M_t$ = change in mid-price over interval $t$
- $x_t$ = net signed order flow (buy volume minus sell volume) over interval $t$
- $\lambda$ = Kyle's lambda (price impact per unit of order flow)
- $\epsilon_t$ = noise

**Estimation:** OLS regression of mid-price changes on signed order flow using Telonex data:

$$
\hat{\lambda} = \frac{\text{Cov}(\Delta M, x)}{\text{Var}(x)}
$$

**Interpretation for Polymarket:**
- Higher $\lambda$ = thinner book, more toxic flow, more impact per dollar
- $\lambda$ varies by strike (ATM > OTM), time of day (pre-market > market hours), and market maturity
- Expected range: $\lambda \approx 0.001$ to $0.01$ (1-10 cents of mid-price move per $100 of net flow)

### Empirical Measurement Protocol

Using Telonex `trades` and `quotes`:

1. **Time intervals**: Aggregate into 30-second or 1-minute bins
2. **Signed order flow**: $x_t = \sum_{i \in \text{bin}} D_i \cdot q_i$ (USDC)
3. **Mid-price change**: $\Delta M_t = M_{t+1} - M_t$ from quotes
4. **Regression**: $\Delta M_t = \alpha + \lambda \cdot x_t + \epsilon_t$
5. **Decompose**: Run same regression at different horizons to separate permanent from temporary

| Horizon | Measures |
|---|---|
| $\Delta M_{t \to t+30s}$ vs $x_t$ | Total impact |
| $\Delta M_{t \to t+5m}$ vs $x_t$ | Permanent impact |
| $(\Delta M_{t \to t+30s}) - (\Delta M_{t \to t+5m})$ | Temporary impact (mean-reverting) |

### Square-Root Impact Model

For larger orders, impact is typically concave (square-root):

$$
\Delta M = \lambda \cdot \text{sign}(x) \cdot |x|^{0.5}
$$

Test both linear and square-root specifications. For Polymarket's thin books, the linear model may suffice given typical order sizes of $10-$100 USDC.

### Application to Order Sizing

Given estimated $\lambda$, the optimal order size that limits self-impact to fraction $f$ of the half-spread:

$$
q_{\text{max}} = \frac{f \cdot \delta}{\lambda}
$$

For example, with $\delta = 0.02$, $\lambda = 0.005$, $f = 0.25$:

$$
q_{\text{max}} = \frac{0.25 \times 0.02}{0.005} = 1.0 \text{ (= \$100 USDC of signed flow)}
$$

### Data Requirements

| Data Source | Fields | Use |
|---|---|---|
| Telonex `trades` | timestamp, side, size, price | Signed order flow construction |
| Telonex `quotes` | timestamp, best_bid, best_ask | Mid-price change measurement |
| Telonex `book_snapshot_5` | depth at top 5 levels | Depth-weighted impact models |

### Testability

**Fully testable.** Estimate $\lambda$ per market/strike from historical data. Validate: does the estimated $\lambda$ predict mid-price changes out-of-sample? Use for optimal order sizing in the backtesting engine.

### Citation

> Kyle, A.S. (1985). "Continuous Auctions and Insider Trading." *Econometrica*, 53(6), 1315-1335.

---

## Integration Roadmap

### Priority Order for Implementation

| Priority | Strategy | Complexity | Expected Impact | Dependencies |
|---|---|---|---|---|
| **P0** | Adverse Selection Decomposition (Section 2) | Low | Diagnostic -- quantifies the problem | Telonex quotes + existing backtest fills |
| **P0** | VPIN (Section 1) | Medium | High -- direct spread adjustment | Telonex trades |
| **P1** | Trade Flow Imbalance (Section 3) | Low | Medium -- burst detection | Telonex trades |
| **P1** | Market Impact (Section 7) | Medium | Medium -- order sizing | Telonex trades + quotes |
| **P2** | Informed Trader Detection (Section 4) | High | High -- wallet-level signal | Telonex onchain_fills or subgraph |
| **P2** | Hawkes Process (Section 5) | High | Medium -- regime detection | Telonex trades (ms timestamps) |
| **P3** | Cross-Platform Flow (Section 6) | High | Potentially high -- preemptive signal | ThetaData + Telonex (time-sync) |

### Phase 1: Diagnostic (Immediate)

Run the Adverse Selection Decomposition on the existing NVDA POC fills to quantify how much of the -$18.10 loss is adverse selection vs. spread leakage vs. inventory. This requires no new infrastructure -- just the existing fills + Telonex quotes.

### Phase 2: VPIN Integration (Next Backtest)

Implement `VPINCalculator` and integrate it into the backtesting engine's quoting logic. Run the NVDA backtest with VPIN-adjusted spreads. Target: reduce adverse selection losses on the $165 ATM strike without sacrificing too many fills on the $170 OTM strike.

### Phase 3: Real-Time Flow Monitoring

For live trading: combine VPIN + TFI burst detection + Hawkes regime detection into a unified flow monitor that feeds spread adjustments to the quoting engine. This is the runtime version of what the backtester validates historically.

### Phase 4: Wallet Intelligence

Build the wallet profiling pipeline from onchain_fills. Flag informed wallets. In live trading, if a flagged wallet hits our quotes, immediately widen or pull. This requires the Polymarket subgraph for real-time wallet identification.

---

## Appendix A: Data Source Summary

| Source | Channel | Key Fields for Flow Analysis | Latency | Cost |
|---|---|---|---|---|
| Telonex | `trades` | timestamp, price, size, side | Historical (T+1 day) | Paid plan |
| Telonex | `quotes` | timestamp, best_bid, best_ask, sizes | Historical (T+1 day) | Paid plan |
| Telonex | `book_snapshot_5` | 5-level depth per side | Historical (T+1 day) | Paid plan |
| Telonex | `onchain_fills` | maker, taker, price, size, tx_hash | Historical (from 2022) | Paid plan |
| Polymarket WS | `last_trade_price` | price, side, size, timestamp, fee_rate_bps | Real-time | Free |
| Polymarket WS | `book` | Full L2 snapshot | Real-time (on subscribe + trade) | Free |
| Polymarket WS | `best_bid_ask` | BBO + spread | Real-time (on change) | Free |
| Polymarket Subgraph | `orderFilledEvents` | maker, taker, amounts, tx_hash | Near real-time (indexed) | Free |
| Polymarket Data API | `GET /trades` | side, size, price, timestamp, proxyWallet | REST polling | Free |
| ThetaData | Options chains | strike, IV, volume, greeks | Real-time + historical | Paid plan |

## Appendix B: Key Formulas Reference

| Metric | Formula | Interpretation |
|---|---|---|
| VPIN | $\frac{1}{N} \sum_{n=1}^{N} \frac{|V_n^b - V_n^s|}{V_B}$ | 0 = balanced, 1 = fully toxic |
| Effective Spread | $2 D (P^{\text{fill}} - M_t)$ | Round-trip cost |
| Realized Spread | $2 D (P^{\text{fill}} - M_{t+\tau})$ | MM's actual profit |
| Adverse Selection | $2 D (M_{t+\tau} - M_t)$ | Cost of informed flow |
| PIN | $\frac{\alpha \mu}{\alpha \mu + 2\epsilon}$ | Structural informed probability |
| Kyle's Lambda | $\frac{\text{Cov}(\Delta M, x)}{\text{Var}(x)}$ | Price impact per unit flow |
| Hawkes Intensity | $\mu + \sum_{t_i < t} \alpha e^{-\beta(t - t_i)}$ | Self-exciting trade rate |
| Branching Ratio | $\alpha / \beta$ | Fraction of triggered trades |
