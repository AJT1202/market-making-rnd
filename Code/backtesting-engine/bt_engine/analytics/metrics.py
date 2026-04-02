"""Backtest metrics computation and reporting.

All monetary values are in tick-centishares (tc).
Display helpers convert to dollars via tc_to_dollars().
"""

from dataclasses import dataclass, field

from bt_engine.portfolio.positions import Portfolio
from bt_engine.units import tc_to_dollars, cs_to_shares, ticks_to_price


@dataclass
class StrikeMetrics:
    """Per-strike fill and PnL summary."""

    strike: int
    # Fill counts
    total_fills: int = 0
    buy_fills: int = 0
    sell_fills: int = 0
    # Volume
    buy_volume_cs: int = 0
    sell_volume_cs: int = 0
    # Cash flows from trading
    total_bought_cost_tc: int = 0       # sum of price_ticks * filled_cs for all buys
    total_sold_revenue_tc: int = 0      # sum of price_ticks * filled_cs for all sells
    # Final state
    final_position_yes_cs: int = 0
    final_position_no_cs: int = 0
    settlement_pnl_tc: int = 0
    # Derived
    cash_flow_tc: int = 0               # sold_revenue - bought_cost (realised trading cash flow)
    total_pnl_tc: int = 0               # cash_flow_tc + settlement_pnl_tc
    spread_capture_tc: int = 0          # spread earned on matched round-trip volume


@dataclass
class BacktestMetrics:
    """Aggregated metrics across all strikes."""

    per_strike: dict[int, StrikeMetrics] = field(default_factory=dict)

    # Totals
    total_fills: int = 0
    total_buy_volume_cs: int = 0
    total_sell_volume_cs: int = 0
    total_pnl_tc: int = 0

    # PnL decomposition
    spread_capture_tc: int = 0          # (ask-bid)/2 earned on round-trip volume
    inventory_pnl_tc: int = 0          # PnL attributable to net inventory exposure
    settlement_pnl_tc: int = 0         # Proceeds from market resolution

    # Efficiency ratios (computed after aggregation)
    fill_rate_pct: float = 0.0         # fills / orders submitted (requires order count input)
    avg_spread_captured_tc: float = 0.0 # spread_capture_tc / total round-trip fills


def compute_metrics(
    fills: list,                         # list[Fill] from execution.order
    portfolio: Portfolio,
    resolutions: dict[int, bool | None],
    settlement_results: dict[int, int] | None = None,
) -> BacktestMetrics:
    """Compute metrics from fills and final portfolio state.

    Parameters
    ----------
    fills:
        All Fill objects produced during the backtest.
    portfolio:
        Portfolio in its final settled state.
    resolutions:
        {strike: True/False/None} resolution outcomes.
    settlement_results:
        Optional {strike: settlement_pnl_tc} from SettlementEngine.settle().
        If None, settlement PnL is inferred from portfolio positions (before
        settlement) which may be less accurate if called post-settlement.
    """
    from bt_engine.types import Side, TokenSide

    per_strike: dict[int, StrikeMetrics] = {
        s: StrikeMetrics(strike=s) for s in portfolio.positions
    }

    # --- Pass 1: aggregate fills per strike ---
    for fill in fills:
        strike = fill.strike
        if strike not in per_strike:
            per_strike[strike] = StrikeMetrics(strike=strike)
        sm = per_strike[strike]

        sm.total_fills += 1
        cost_tc = fill.price_ticks * fill.filled_cs

        if fill.side == Side.BUY:
            sm.buy_fills += 1
            sm.buy_volume_cs += fill.filled_cs
            sm.total_bought_cost_tc += cost_tc
        else:
            sm.sell_fills += 1
            sm.sell_volume_cs += fill.filled_cs
            sm.total_sold_revenue_tc += cost_tc

    # --- Pass 2: final positions and settlement ---
    for strike, pos in portfolio.positions.items():
        if strike not in per_strike:
            per_strike[strike] = StrikeMetrics(strike=strike)
        sm = per_strike[strike]
        sm.final_position_yes_cs = pos.yes_position_cs
        sm.final_position_no_cs = pos.no_position_cs

    if settlement_results:
        for strike, pnl_tc in settlement_results.items():
            if strike in per_strike:
                per_strike[strike].settlement_pnl_tc = pnl_tc

    # --- Pass 3: derived per-strike PnL ---
    for sm in per_strike.values():
        sm.cash_flow_tc = sm.total_sold_revenue_tc - sm.total_bought_cost_tc
        sm.total_pnl_tc = sm.cash_flow_tc + sm.settlement_pnl_tc

    # --- Aggregate totals ---
    metrics = BacktestMetrics(per_strike=per_strike)

    for sm in per_strike.values():
        metrics.total_fills += sm.total_fills
        metrics.total_buy_volume_cs += sm.buy_volume_cs
        metrics.total_sell_volume_cs += sm.sell_volume_cs
        metrics.total_pnl_tc += sm.total_pnl_tc
        metrics.settlement_pnl_tc += sm.settlement_pnl_tc

    # --- Spread capture: total sold revenue on matched volume - total bought cost on matched volume ---
    for sm in per_strike.values():
        matched_cs = min(sm.buy_volume_cs, sm.sell_volume_cs)
        if matched_cs > 0 and sm.buy_volume_cs > 0 and sm.sell_volume_cs > 0:
            # Pro-rata: what fraction of buys/sells were matched?
            matched_buy_cost_tc = sm.total_bought_cost_tc * matched_cs // sm.buy_volume_cs
            matched_sell_rev_tc = sm.total_sold_revenue_tc * matched_cs // sm.sell_volume_cs
            spread_capture_tc = matched_sell_rev_tc - matched_buy_cost_tc
        else:
            spread_capture_tc = 0
        sm.spread_capture_tc = spread_capture_tc
        metrics.spread_capture_tc += spread_capture_tc

    # Inventory PnL = total PnL - spread capture - settlement PnL
    metrics.inventory_pnl_tc = (
        metrics.total_pnl_tc - metrics.spread_capture_tc - metrics.settlement_pnl_tc
    )

    # Efficiency ratios
    if metrics.total_fills > 0:
        metrics.avg_spread_captured_tc = metrics.spread_capture_tc / metrics.total_fills

    return metrics


def print_metrics(metrics: BacktestMetrics) -> None:
    """Print a human-readable metrics report to stdout."""
    print("=" * 60)
    print("BACKTEST METRICS")
    print("=" * 60)
    print(f"Total fills:          {metrics.total_fills:>10,}")
    print(f"Buy volume:           {cs_to_shares(metrics.total_buy_volume_cs):>10.2f} shares")
    print(f"Sell volume:          {cs_to_shares(metrics.total_sell_volume_cs):>10.2f} shares")
    print(f"Total PnL:            ${tc_to_dollars(metrics.total_pnl_tc):>10.4f}")
    print(f"  Spread capture:     ${tc_to_dollars(metrics.spread_capture_tc):>10.4f}")
    print(f"  Inventory PnL:      ${tc_to_dollars(metrics.inventory_pnl_tc):>10.4f}")
    print(f"  Settlement PnL:     ${tc_to_dollars(metrics.settlement_pnl_tc):>10.4f}")
    print()

    if metrics.per_strike:
        print(f"{'Strike':>7}  {'Fills':>6}  {'BuyVol':>8}  {'SellVol':>8}  {'CashFlow':>10}  {'SettlePnL':>10}  {'TotalPnL':>10}")
        print("-" * 75)
        for strike in sorted(metrics.per_strike):
            sm = metrics.per_strike[strike]
            if sm.total_fills == 0 and sm.settlement_pnl_tc == 0:
                continue
            print(
                f"{strike:>7}  "
                f"{sm.total_fills:>6,}  "
                f"{cs_to_shares(sm.buy_volume_cs):>8.2f}  "
                f"{cs_to_shares(sm.sell_volume_cs):>8.2f}  "
                f"${tc_to_dollars(sm.cash_flow_tc):>9.4f}  "
                f"${tc_to_dollars(sm.settlement_pnl_tc):>9.4f}  "
                f"${tc_to_dollars(sm.total_pnl_tc):>9.4f}"
            )
    print("=" * 60)
