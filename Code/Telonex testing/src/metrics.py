"""
Performance metrics computation for backtesting results.

Computes P&L decomposition, fill metrics, inventory metrics,
and per-strike breakdowns.
"""

from dataclasses import dataclass, field

from src.fill_simulator import Fill, Side


# Market resolutions: strike -> True if YES, False if NO
RESOLUTIONS = {
    160: True,   # NVDA > 160 -> YES
    165: True,   # NVDA > 165 -> YES
    170: False,  # NVDA < 170 -> NO
    175: False,  # NVDA < 175 -> NO
    180: False,  # NVDA < 180 -> NO
}

STRIKES = [160, 165, 170, 175, 180]


@dataclass
class StrikeMetrics:
    """Metrics for a single strike."""
    strike: int
    total_fills: int = 0
    buy_fills: int = 0
    sell_fills: int = 0
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    total_bought_cost: float = 0.0    # sum of price * size for buys
    total_sold_revenue: float = 0.0   # sum of price * size for sells
    final_position: float = 0.0
    max_position: float = 0.0
    min_position: float = 0.0
    settlement_value: float = 0.0     # value of position at resolution
    cash_flow: float = 0.0           # net cash from trading
    total_pnl: float = 0.0


@dataclass
class BacktestMetrics:
    """Aggregate metrics for the entire backtest."""
    simulator_name: str = ""
    per_strike: dict[int, StrikeMetrics] = field(default_factory=dict)

    # Aggregate metrics
    total_fills: int = 0
    total_buy_fills: int = 0
    total_sell_fills: int = 0
    total_volume: float = 0.0

    # P&L
    total_spread_capture: float = 0.0
    total_inventory_pnl: float = 0.0
    total_pnl: float = 0.0

    # Inventory
    max_abs_position: float = 0.0
    avg_abs_position: float = 0.0


def compute_metrics(
    fills: list[Fill],
    position_history: list[dict],
    simulator_name: str = "",
) -> BacktestMetrics:
    """
    Compute comprehensive backtest metrics from fill and position history.

    Parameters
    ----------
    fills : list[Fill]
        All fills during the backtest.
    position_history : list[dict]
        List of position snapshots: {strike: position, ...} at each step.
    simulator_name : str
        Name of the fill simulator used.

    Returns
    -------
    BacktestMetrics
    """
    metrics = BacktestMetrics(simulator_name=simulator_name)

    # Initialize per-strike metrics
    for strike in STRIKES:
        metrics.per_strike[strike] = StrikeMetrics(strike=strike)

    # Process fills
    for fill in fills:
        sm = metrics.per_strike[fill.strike]
        sm.total_fills += 1

        if fill.side == Side.BUY:
            sm.buy_fills += 1
            sm.buy_volume += fill.size
            sm.total_bought_cost += fill.price * fill.size
            sm.cash_flow -= fill.price * fill.size  # cash out
        else:
            sm.sell_fills += 1
            sm.sell_volume += fill.size
            sm.total_sold_revenue += fill.price * fill.size
            sm.cash_flow += fill.price * fill.size  # cash in

    # Compute final positions and settlement
    if position_history:
        final_positions = position_history[-1]
    else:
        final_positions = {s: 0.0 for s in STRIKES}

    for strike in STRIKES:
        sm = metrics.per_strike[strike]
        sm.final_position = final_positions.get(strike, 0.0)

        # Settlement: YES token pays $1 if resolved YES, $0 if NO
        resolved_yes = RESOLUTIONS[strike]
        settlement_price = 1.0 if resolved_yes else 0.0
        sm.settlement_value = sm.final_position * settlement_price

        # Total P&L = cash_flow + settlement_value
        sm.total_pnl = sm.cash_flow + sm.settlement_value

    # Track position extremes from history
    for snapshot in position_history:
        for strike in STRIKES:
            pos = snapshot.get(strike, 0.0)
            sm = metrics.per_strike[strike]
            sm.max_position = max(sm.max_position, pos)
            sm.min_position = min(sm.min_position, pos)

    # Compute spread capture estimate
    # Match buys and sells per strike to estimate round-trip profit
    for strike in STRIKES:
        sm = metrics.per_strike[strike]
        strike_fills = [f for f in fills if f.strike == strike]
        buy_fills = sorted(
            [f for f in strike_fills if f.side == Side.BUY],
            key=lambda f: f.fill_timestamp_us,
        )
        sell_fills = sorted(
            [f for f in strike_fills if f.side == Side.SELL],
            key=lambda f: f.fill_timestamp_us,
        )

        # FIFO matching for spread capture
        spread_capture = 0.0
        bi, si = 0, 0
        remaining_buy = 0.0
        remaining_sell = 0.0
        buy_price = 0.0
        sell_price = 0.0

        while bi < len(buy_fills) or si < len(sell_fills):
            # Refill buy side
            if remaining_buy <= 0 and bi < len(buy_fills):
                remaining_buy = buy_fills[bi].size
                buy_price = buy_fills[bi].price
                bi += 1
            # Refill sell side
            if remaining_sell <= 0 and si < len(sell_fills):
                remaining_sell = sell_fills[si].size
                sell_price = sell_fills[si].price
                si += 1

            if remaining_buy <= 0 or remaining_sell <= 0:
                break

            matched = min(remaining_buy, remaining_sell)
            spread_capture += matched * (sell_price - buy_price)
            remaining_buy -= matched
            remaining_sell -= matched

        metrics.total_spread_capture += spread_capture

    # Aggregate metrics
    total_abs_positions = []
    for snapshot in position_history:
        total_abs = sum(abs(snapshot.get(s, 0.0)) for s in STRIKES)
        total_abs_positions.append(total_abs)

    for strike in STRIKES:
        sm = metrics.per_strike[strike]
        metrics.total_fills += sm.total_fills
        metrics.total_buy_fills += sm.buy_fills
        metrics.total_sell_fills += sm.sell_fills
        metrics.total_volume += sm.buy_volume + sm.sell_volume
        metrics.total_pnl += sm.total_pnl
        metrics.total_inventory_pnl += sm.settlement_value + sm.cash_flow - metrics.total_spread_capture

        abs_pos = max(abs(sm.max_position), abs(sm.min_position))
        metrics.max_abs_position = max(metrics.max_abs_position, abs_pos)

    # Recompute inventory P&L correctly
    metrics.total_inventory_pnl = metrics.total_pnl - metrics.total_spread_capture

    if total_abs_positions:
        metrics.avg_abs_position = sum(total_abs_positions) / len(total_abs_positions)

    return metrics


def print_metrics(metrics: BacktestMetrics) -> None:
    """Print a formatted summary of backtest metrics."""
    print("=" * 70)
    print(f"  BACKTEST RESULTS — {metrics.simulator_name} Fill Simulator")
    print("=" * 70)
    print()

    # Overall P&L
    print("  P&L Summary")
    print("  " + "-" * 40)
    print(f"    Total P&L:          ${metrics.total_pnl:>10.2f}")
    print(f"    Spread Capture:     ${metrics.total_spread_capture:>10.2f}")
    print(f"    Inventory P&L:      ${metrics.total_inventory_pnl:>10.2f}")
    print()

    # Fill metrics
    print("  Fill Metrics")
    print("  " + "-" * 40)
    print(f"    Total Fills:        {metrics.total_fills:>10}")
    print(f"    Buy Fills:          {metrics.total_buy_fills:>10}")
    print(f"    Sell Fills:         {metrics.total_sell_fills:>10}")
    print(f"    Total Volume:       {metrics.total_volume:>10.1f}")
    print()

    # Inventory metrics
    print("  Inventory Metrics")
    print("  " + "-" * 40)
    print(f"    Max |Position|:     {metrics.max_abs_position:>10.1f}")
    print(f"    Avg |Position|:     {metrics.avg_abs_position:>10.1f}")
    print()

    # Per-strike breakdown
    print("  Per-Strike Breakdown")
    print("  " + "-" * 66)
    header = (
        f"  {'Strike':>8} | {'Fills':>6} | {'Buy':>5} | {'Sell':>5} | "
        f"{'FinalPos':>8} | {'Cash':>9} | {'Settle':>8} | {'P&L':>9}"
    )
    print(header)
    print("  " + "-" * 66)

    for strike in STRIKES:
        sm = metrics.per_strike[strike]
        resolved = "YES" if RESOLUTIONS[strike] else "NO"
        print(
            f"  {strike:>6}{resolved:>2} | {sm.total_fills:>6} | {sm.buy_fills:>5} | "
            f"{sm.sell_fills:>5} | {sm.final_position:>8.1f} | "
            f"${sm.cash_flow:>8.2f} | ${sm.settlement_value:>7.2f} | "
            f"${sm.total_pnl:>8.2f}"
        )

    print("  " + "-" * 66)
    print()


def compare_simulators(m1: BacktestMetrics, m2: BacktestMetrics) -> None:
    """Print a side-by-side comparison of two simulator results."""
    print("=" * 70)
    print("  SIMULATOR COMPARISON")
    print("=" * 70)
    print()

    print(f"  {'Metric':<25} | {m1.simulator_name:>15} | {m2.simulator_name:>15} | {'Delta':>10}")
    print("  " + "-" * 72)

    rows = [
        ("Total P&L", f"${m1.total_pnl:.2f}", f"${m2.total_pnl:.2f}",
         f"${m2.total_pnl - m1.total_pnl:.2f}"),
        ("Spread Capture", f"${m1.total_spread_capture:.2f}",
         f"${m2.total_spread_capture:.2f}",
         f"${m2.total_spread_capture - m1.total_spread_capture:.2f}"),
        ("Inventory P&L", f"${m1.total_inventory_pnl:.2f}",
         f"${m2.total_inventory_pnl:.2f}",
         f"${m2.total_inventory_pnl - m1.total_inventory_pnl:.2f}"),
        ("Total Fills", str(m1.total_fills), str(m2.total_fills),
         str(m2.total_fills - m1.total_fills)),
        ("Buy Fills", str(m1.total_buy_fills), str(m2.total_buy_fills),
         str(m2.total_buy_fills - m1.total_buy_fills)),
        ("Sell Fills", str(m1.total_sell_fills), str(m2.total_sell_fills),
         str(m2.total_sell_fills - m1.total_sell_fills)),
        ("Total Volume", f"{m1.total_volume:.1f}", f"{m2.total_volume:.1f}",
         f"{m2.total_volume - m1.total_volume:.1f}"),
        ("Max |Position|", f"{m1.max_abs_position:.1f}",
         f"{m2.max_abs_position:.1f}",
         f"{m2.max_abs_position - m1.max_abs_position:.1f}"),
    ]

    for label, v1, v2, delta in rows:
        print(f"  {label:<25} | {v1:>15} | {v2:>15} | {delta:>10}")

    print("  " + "-" * 72)
    print()
