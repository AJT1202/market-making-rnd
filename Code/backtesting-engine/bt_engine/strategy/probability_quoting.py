"""Probability-based quoting strategy adapted from the POC.

Quotes YES tokens around the Black-Scholes fair value with configurable
spread, position limits, and minimum edge requirement. Uses integer ticks
throughout.
"""

from bt_engine.types import Side, TokenSide
from bt_engine.units import bps_to_ticks
from bt_engine.strategy.interface import StrategyAction, StrategyUpdate


class ProbabilityQuotingStrategy:
    """Quote around fair value with configurable spread and position limits.

    Logic per market update:
    1. Cancel all resting orders for this strike (emit CANCEL actions).
    2. Compute fv_ticks = fair_value_bps / 100 (rounded).
    3. Compute poly_mid_ticks = mid_ticks_x2 / 2 (rounded).
    4. edge_ticks = |fv_ticks - poly_mid_ticks|
    5. If edge_ticks < min_edge_ticks: stop (no new orders).
    6. bid = fv_ticks - half_spread_ticks, ask = fv_ticks + half_spread_ticks.
    7. Clamp both to [1, 99].
    8. Skip bid if would exceed max long; skip ask if would exceed max short.
    9. Place orders sized to min(order_size_cs, room_to_limit).
    """

    def __init__(
        self,
        half_spread_ticks: int = 2,
        max_position_cs: int = 5000,
        min_edge_ticks: int = 3,
        order_size_cs: int = 1000,
    ) -> None:
        self.half_spread_ticks = half_spread_ticks
        self.max_position_cs = max_position_cs
        self.min_edge_ticks = min_edge_ticks
        self.order_size_cs = order_size_cs

        # strike -> list of active order_ids we placed (YES token only for now)
        self._resting_order_ids: dict[int, list[str]] = {}

    def on_market_update(self, update: StrategyUpdate) -> list[StrategyAction]:
        """Cancel existing quotes, then place new ones if edge is sufficient."""
        actions: list[StrategyAction] = []

        # --- 1. Cancel all resting orders for this strike ---
        existing = self._resting_order_ids.pop(update.strike, [])
        for oid in existing:
            actions.append(StrategyAction(kind="CANCEL", strike=update.strike, order_id=oid))

        # --- 2. Validate inputs ---
        if update.best_bid_ticks <= 0 or update.best_ask_ticks <= 0:
            return actions
        if update.best_ask_ticks <= update.best_bid_ticks:
            return actions
        if update.fair_value_bps <= 0:
            return actions

        # --- 3. Compute fair value in ticks (round half-up via integer arithmetic) ---
        # fair_value_bps is 0-10000; divide by 100 to get ticks 0-100
        fv_ticks = (update.fair_value_bps + 50) // 100

        # --- 4. Compute poly mid in ticks (mid_ticks_x2 is bid+ask, so /2) ---
        poly_mid_ticks = update.mid_ticks_x2 // 2

        # --- 5. Edge check ---
        edge_ticks = abs(fv_ticks - poly_mid_ticks)
        if edge_ticks < self.min_edge_ticks:
            return actions

        # --- 6. Compute raw bid/ask ---
        bid_ticks = fv_ticks - self.half_spread_ticks
        ask_ticks = fv_ticks + self.half_spread_ticks

        # --- 7. Clamp to valid Polymarket range ---
        bid_ticks = max(1, min(99, bid_ticks))
        ask_ticks = max(1, min(99, ask_ticks))

        if bid_ticks >= ask_ticks:
            return actions

        new_order_ids: list[str] = []

        # --- 8a. Place BUY (bid) if position allows ---
        yes_pos = update.position_yes_cs
        if yes_pos < self.max_position_cs:
            buy_size = min(self.order_size_cs, self.max_position_cs - yes_pos)
            if buy_size > 0:
                # Generate a deterministic placeholder order_id; the engine will
                # assign the real ID and pass it back via on_fill. We use a
                # string tag so the engine can correlate.
                oid = f"pq_{update.strike}_{update.timestamp_us}_bid"
                actions.append(StrategyAction(
                    kind="PLACE",
                    strike=update.strike,
                    token_side=TokenSide.YES,
                    side=Side.BUY,
                    price_ticks=bid_ticks,
                    size_cs=buy_size,
                    order_id=oid,
                ))
                new_order_ids.append(oid)

        # --- 8b. Place SELL (ask) if position allows ---
        # Positive yes_pos = long; allow up to -max_position_cs net (max short)
        if yes_pos > -self.max_position_cs:
            sell_size = min(self.order_size_cs, self.max_position_cs + yes_pos)
            if sell_size > 0:
                oid = f"pq_{update.strike}_{update.timestamp_us}_ask"
                actions.append(StrategyAction(
                    kind="PLACE",
                    strike=update.strike,
                    token_side=TokenSide.YES,
                    side=Side.SELL,
                    price_ticks=ask_ticks,
                    size_cs=sell_size,
                    order_id=oid,
                ))
                new_order_ids.append(oid)

        # Store new resting order ids for cancellation on next update
        if new_order_ids:
            self._resting_order_ids[update.strike] = new_order_ids

        return actions

    def on_fill(
        self,
        strike: int,
        token_side: TokenSide,
        side: Side,
        price_ticks: int,
        size_cs: int,
    ) -> list[StrategyAction]:
        """No reactive fills for this simple strategy."""
        return []

    def notify_order_id(self, strategy_oid: str, engine_oid: str) -> None:
        """Called by the engine to map strategy-generated IDs to real order IDs.

        The engine should call this after accepting a PLACE action so that
        subsequent CANCEL actions use the real engine order ID.
        """
        for strike, oids in self._resting_order_ids.items():
            for i, oid in enumerate(oids):
                if oid == strategy_oid:
                    oids[i] = engine_oid
                    return
