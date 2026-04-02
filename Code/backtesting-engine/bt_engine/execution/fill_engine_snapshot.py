"""Snapshot-only fill engine (no trades channel)."""

from bt_engine.data.schema import BookSnapshot
from bt_engine.execution.order import Fill, SimOrder
from bt_engine.types import Side


# (best_bid_ticks, best_ask_ticks, best_bid_size_cs, best_ask_size_cs)
_BboState = tuple[int, int, int, int]


class SnapshotFillEngine:
    """Fill engine for snapshot-only data (no trades channel).

    Fill triggers:
    1. Aggressive: order price crosses current BBO.
    2. BBO movement: price moved through our order since last snapshot.
    3. Queue at BBO: depth decreased at our price level (with cancel_discount).
    """

    def __init__(self, cancel_discount: float = 0.5):
        self.cancel_discount = cancel_discount
        # Maps (strike, token_side) -> (best_bid_ticks, best_ask_ticks, bid_size_cs, ask_size_cs)
        self._prev_bbo: dict[tuple, _BboState] = {}

    def check_fills(self, orders: list[SimOrder], snapshot: BookSnapshot) -> list[Fill]:
        """Check fills for all resting orders against a new book snapshot."""
        if not snapshot.is_valid:
            return []

        key = (snapshot.strike, snapshot.token_side)
        prev = self._prev_bbo.get(key)

        fills: list[Fill] = []
        for order in orders:
            if not order.is_live:
                continue
            if order.strike != snapshot.strike or order.token_side != snapshot.token_side:
                continue
            # Order must be visible (submit latency passed)
            if order.visible_ts_us > snapshot.timestamp_us:
                continue

            if order.side == Side.BUY:
                fill = self._check_buy_fill(order, snapshot, prev)
            else:
                fill = self._check_sell_fill(order, snapshot, prev)

            if fill is not None:
                fills.append(fill)

        # Update previous BBO state
        self._prev_bbo[key] = (
            snapshot.best_bid_ticks,
            snapshot.best_ask_ticks,
            snapshot.best_bid_size_cs,
            snapshot.best_ask_size_cs,
        )

        return fills

    def _check_buy_fill(
        self,
        order: SimOrder,
        snapshot: BookSnapshot,
        prev: _BboState | None,
    ) -> Fill | None:
        """Check if a BUY order fills. Three paths: aggressive, BBO movement, queue."""
        best_ask = snapshot.best_ask_ticks
        best_bid = snapshot.best_bid_ticks

        # Path 1: Aggressive — order price >= best_ask (crosses the spread)
        if order.price_ticks >= best_ask:
            available_cs = snapshot.best_ask_size_cs
            if available_cs <= 0:
                return None
            filled_cs = min(order.remaining_cs, available_cs)
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                token_side=order.token_side,
                side=order.side,
                price_ticks=order.price_ticks,
                filled_cs=filled_cs,
                timestamp_us=snapshot.timestamp_us,
                queue_ahead_at_fill=order.queue_ahead_cs,
                is_aggressive=True,
            )

        if prev is None:
            return None

        prev_bid, prev_ask, prev_bid_size, prev_ask_size = prev

        # Path 2: BBO movement — ask dropped through our price since last snapshot
        # prev_ask was above our price, now best_ask <= our price
        if prev_ask > order.price_ticks >= best_ask:
            available_cs = snapshot.depth_at_price(order.price_ticks)
            if available_cs <= 0:
                return None
            filled_cs = min(order.remaining_cs, available_cs)
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                token_side=order.token_side,
                side=order.side,
                price_ticks=order.price_ticks,
                filled_cs=filled_cs,
                timestamp_us=snapshot.timestamp_us,
                queue_ahead_at_fill=order.queue_ahead_cs,
                is_aggressive=False,
            )

        # Path 3: Resting at BBO bid — depth decreased
        if order.price_ticks == best_bid and order.price_ticks == prev_bid:
            curr_bid_size = snapshot.best_bid_size_cs
            if prev_bid_size > 0 and curr_bid_size < prev_bid_size:
                consumed = prev_bid_size - curr_bid_size
                # Apply cancel discount: a fraction of consumed may be cancels, not trades
                trade_proxy = int(consumed * (1.0 - self.cancel_discount))
                # queue_ahead_cs was assigned at visibility; drain by trade proxy.
                # Always persist the updated queue position (M1 fix).
                effective_queue = max(0, order.queue_ahead_cs - trade_proxy)
                order.queue_ahead_cs = effective_queue
                if effective_queue == 0:
                    available_cs = trade_proxy
                    if available_cs <= 0:
                        return None
                    filled_cs = min(order.remaining_cs, available_cs)
                    return Fill(
                        order_id=order.order_id,
                        strike=order.strike,
                        token_side=order.token_side,
                        side=order.side,
                        price_ticks=order.price_ticks,
                        filled_cs=filled_cs,
                        timestamp_us=snapshot.timestamp_us,
                        queue_ahead_at_fill=0,
                        is_aggressive=False,
                    )

        return None

    def _check_sell_fill(
        self,
        order: SimOrder,
        snapshot: BookSnapshot,
        prev: _BboState | None,
    ) -> Fill | None:
        """Check if a SELL order fills. Three paths: aggressive, BBO movement, queue."""
        best_bid = snapshot.best_bid_ticks
        best_ask = snapshot.best_ask_ticks

        # Path 1: Aggressive — order price <= best_bid (crosses the spread)
        if order.price_ticks <= best_bid:
            available_cs = snapshot.best_bid_size_cs
            if available_cs <= 0:
                return None
            filled_cs = min(order.remaining_cs, available_cs)
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                token_side=order.token_side,
                side=order.side,
                price_ticks=order.price_ticks,
                filled_cs=filled_cs,
                timestamp_us=snapshot.timestamp_us,
                queue_ahead_at_fill=order.queue_ahead_cs,
                is_aggressive=True,
            )

        if prev is None:
            return None

        prev_bid, prev_ask, prev_bid_size, prev_ask_size = prev

        # Path 2: BBO movement — bid rose through our price since last snapshot
        # prev_bid was below our price, now best_bid >= our price
        if prev_bid < order.price_ticks <= best_bid:
            available_cs = snapshot.depth_at_price(order.price_ticks)
            if available_cs <= 0:
                return None
            filled_cs = min(order.remaining_cs, available_cs)
            return Fill(
                order_id=order.order_id,
                strike=order.strike,
                token_side=order.token_side,
                side=order.side,
                price_ticks=order.price_ticks,
                filled_cs=filled_cs,
                timestamp_us=snapshot.timestamp_us,
                queue_ahead_at_fill=order.queue_ahead_cs,
                is_aggressive=False,
            )

        # Path 3: Resting at BBO ask — depth decreased
        if order.price_ticks == best_ask and order.price_ticks == prev_ask:
            curr_ask_size = snapshot.best_ask_size_cs
            if prev_ask_size > 0 and curr_ask_size < prev_ask_size:
                consumed = prev_ask_size - curr_ask_size
                trade_proxy = int(consumed * (1.0 - self.cancel_discount))
                # Always persist the updated queue position (M1 fix).
                effective_queue = max(0, order.queue_ahead_cs - trade_proxy)
                order.queue_ahead_cs = effective_queue
                if effective_queue == 0:
                    available_cs = trade_proxy
                    if available_cs <= 0:
                        return None
                    filled_cs = min(order.remaining_cs, available_cs)
                    return Fill(
                        order_id=order.order_id,
                        strike=order.strike,
                        token_side=order.token_side,
                        side=order.side,
                        price_ticks=order.price_ticks,
                        filled_cs=filled_cs,
                        timestamp_us=snapshot.timestamp_us,
                        queue_ahead_at_fill=0,
                        is_aggressive=False,
                    )

        return None
