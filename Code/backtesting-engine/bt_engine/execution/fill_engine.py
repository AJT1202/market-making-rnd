"""Trade-driven fill engine (production mode)."""

from bt_engine.data.schema import BookSnapshot, TradeEvent
from bt_engine.execution.order import Fill, SimOrder
from bt_engine.execution.queue_position import QueuePositionModel
from bt_engine.types import Side


class TradeDrivenFillEngine:
    """Fill engine using actual trade events (production mode).

    7-condition model:
    1. Order must be live (ACTIVE, PARTIALLY_FILLED, or PENDING_CANCEL).
    2. Trade timestamp >= order visible_ts_us.
    3. Cancel not yet effective (trade.timestamp_us < cancel_effective_ts_us or no cancel).
    4. Trade price == order price.
    5. Trade side compatible: taker BUY fills resting SELL, taker SELL fills resting BUY.
    6. Queue drain: queue_ahead -= min(queue_ahead, trade_size).
    7. Fill if queue_ahead == 0 and remaining passthrough > 0.

    Two phases per trade:
    Phase 1: Queue reduction — reduce queue_ahead for all eligible orders.
    Phase 2: Fill allocation — orders with queue_ahead=0 fill from shared pool.
    """

    def __init__(self, queue_model: QueuePositionModel):
        self.queue_model = queue_model

    def check_fills_on_trade(self, orders: list[SimOrder], trade: TradeEvent) -> list[Fill]:
        """Process a trade against all resting orders."""
        eligible: list[SimOrder] = []
        for order in orders:
            if not order.is_live:
                continue
            # Condition 2: order must be visible
            if trade.timestamp_us < order.visible_ts_us:
                continue
            # Condition 3: cancel not yet effective
            if order.cancel_effective_ts_us > 0 and trade.timestamp_us >= order.cancel_effective_ts_us:
                continue
            # Condition 4: price match
            if order.price_ticks != trade.price_ticks:
                continue
            # Condition 5: side compatibility
            # Taker BUY lifts the ask -> fills resting SELL orders
            # Taker SELL hits the bid -> fills resting BUY orders
            if trade.taker_side == Side.BUY and order.side != Side.SELL:
                continue
            if trade.taker_side == Side.SELL and order.side != Side.BUY:
                continue
            eligible.append(order)

        if not eligible:
            return []

        # Phase 1: Queue reduction — drain queue_ahead for all eligible orders.
        # The trade volume is a shared pool: each order drains only what it can
        # absorb from the remaining pool, and that amount is deducted from the pool.
        remaining_trade_size = trade.size_cs
        for order in eligible:
            if remaining_trade_size <= 0:
                break
            before = order.queue_ahead_cs
            self.queue_model.drain_queue(order, remaining_trade_size)
            drained = before - order.queue_ahead_cs
            remaining_trade_size -= drained

        # Phase 2: Fill allocation — orders with queue_ahead == 0 share the pool
        # Remaining pool after queue ahead orders absorbed their portion.
        # Compute how much trade volume is left for our orders.
        # Multiple orders at same price level share the remaining trade volume.
        ready = [o for o in eligible if o.queue_ahead_cs == 0]
        if not ready:
            return []

        fills: list[Fill] = []
        pool_cs = remaining_trade_size

        for order in ready:
            if pool_cs <= 0:
                break
            fill_cs = min(order.remaining_cs, pool_cs)
            fill = Fill(
                order_id=order.order_id,
                strike=order.strike,
                token_side=order.token_side,
                side=order.side,
                price_ticks=order.price_ticks,
                filled_cs=fill_cs,
                timestamp_us=trade.timestamp_us,
                queue_ahead_at_fill=0,
                is_aggressive=False,
            )
            fills.append(fill)
            pool_cs -= fill_cs

        return fills

    def check_aggressive_fill(self, order: SimOrder, snapshot: BookSnapshot) -> Fill | None:
        """Check if an order crossing the spread should fill immediately at visibility.

        Called when an order becomes visible and its price already crosses the BBO.
        """
        if not snapshot.is_valid:
            return None

        best_bid = snapshot.best_bid_ticks
        best_ask = snapshot.best_ask_ticks

        is_aggressive = False
        if order.side == Side.BUY and order.price_ticks >= best_ask:
            is_aggressive = True
        elif order.side == Side.SELL and order.price_ticks <= best_bid:
            is_aggressive = True

        if not is_aggressive:
            return None

        return Fill(
            order_id=order.order_id,
            strike=order.strike,
            token_side=order.token_side,
            side=order.side,
            price_ticks=order.price_ticks,
            filled_cs=order.remaining_cs,
            timestamp_us=snapshot.timestamp_us,
            queue_ahead_at_fill=order.queue_ahead_cs,
            is_aggressive=True,
        )
