"""Order lifecycle management: SimOrder, Fill, and OrderManager."""

from dataclasses import dataclass, field

from bt_engine.config import LatencyConfig
from bt_engine.execution.latency import LatencyModel
from bt_engine.types import OrderStatus, Side, TokenSide


@dataclass
class SimOrder:
    """Mutable representation of an order through its lifecycle."""

    order_id: str                   # "ord_000042"
    strike: int
    token_side: TokenSide
    side: Side                      # BUY or SELL
    price_ticks: int                # 1..99
    size_cs: int                    # centishares, original size
    remaining_cs: int               # decreases on partial fills
    status: OrderStatus             # lifecycle state
    decision_ts_us: int             # when strategy decided
    submit_ts_us: int               # when order becomes ACTIVE
    visible_ts_us: int              # when order becomes visible in book
    cancel_request_ts_us: int = 0   # when cancel was requested
    cancel_effective_ts_us: int = 0 # when cancel becomes effective
    queue_ahead_cs: int = 0         # centishares ahead in queue
    reserved_tc: int = 0            # actual cash reserved for this order (for correct cancel release)

    @property
    def is_live(self) -> bool:
        return self.status in (
            OrderStatus.ACTIVE,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING_CANCEL,
        )

    @property
    def is_visible(self) -> bool:
        """Whether the order would be visible in the orderbook."""
        return self.is_live and self.visible_ts_us > 0


@dataclass(frozen=True)
class Fill:
    """A completed fill."""

    order_id: str
    strike: int
    token_side: TokenSide
    side: Side
    price_ticks: int
    filled_cs: int
    timestamp_us: int
    queue_ahead_at_fill: int  # for analysis
    is_aggressive: bool       # crossed spread vs passive fill


class OrderManager:
    """Manages order lifecycle and generates internal events."""

    def __init__(self, latency: LatencyModel):
        self._orders: dict[str, SimOrder] = {}
        self._next_id = 1
        self._latency = latency

    def submit_order(
        self,
        strike: int,
        token_side: TokenSide,
        side: Side,
        price_ticks: int,
        size_cs: int,
        decision_ts_us: int,
    ) -> SimOrder:
        """Create a new order in PENDING_SUBMIT state. Returns the order."""
        order_id = f"ord_{self._next_id:06d}"
        self._next_id += 1

        submit_ts_us = decision_ts_us + self._latency.submit_us
        visible_ts_us = submit_ts_us + self._latency.visible_us

        order = SimOrder(
            order_id=order_id,
            strike=strike,
            token_side=token_side,
            side=side,
            price_ticks=price_ticks,
            size_cs=size_cs,
            remaining_cs=size_cs,
            status=OrderStatus.PENDING_SUBMIT,
            decision_ts_us=decision_ts_us,
            submit_ts_us=submit_ts_us,
            visible_ts_us=visible_ts_us,
        )
        # With zero submit latency, activate immediately
        if self._latency.submit_us == 0:
            order.status = OrderStatus.ACTIVE
        self._orders[order_id] = order
        return order

    def request_cancel(self, order_id: str, request_ts_us: int) -> int:
        """Request cancellation. Returns cancel_effective_ts_us."""
        order = self._orders[order_id]
        if not order.is_live:
            return 0
        effective_ts = request_ts_us + self._latency.cancel_us
        order.cancel_request_ts_us = request_ts_us
        order.cancel_effective_ts_us = effective_ts
        order.status = OrderStatus.PENDING_CANCEL
        return effective_ts

    def activate(self, order_id: str) -> None:
        """Transition PENDING_SUBMIT -> ACTIVE."""
        order = self._orders[order_id]
        if order.status == OrderStatus.PENDING_SUBMIT:
            order.status = OrderStatus.ACTIVE

    def apply_fill(self, order_id: str, filled_cs: int, timestamp_us: int, is_aggressive: bool = False) -> Fill:
        """Apply a fill to an order. May fully fill or partially fill."""
        order = self._orders[order_id]

        # Clamp to remaining
        filled_cs = min(filled_cs, order.remaining_cs)
        queue_ahead_at_fill = order.queue_ahead_cs

        fill = Fill(
            order_id=order_id,
            strike=order.strike,
            token_side=order.token_side,
            side=order.side,
            price_ticks=order.price_ticks,
            filled_cs=filled_cs,
            timestamp_us=timestamp_us,
            queue_ahead_at_fill=queue_ahead_at_fill,
            is_aggressive=is_aggressive,
        )

        order.remaining_cs -= filled_cs
        if order.remaining_cs == 0:
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        return fill

    def cancel_effective(self, order_id: str) -> None:
        """Transition PENDING_CANCEL -> CANCELLED."""
        order = self._orders[order_id]
        if order.status == OrderStatus.PENDING_CANCEL:
            order.status = OrderStatus.CANCELLED

    def get_resting_orders(self, strike: int, token_side: TokenSide) -> list[SimOrder]:
        """Get all live orders for a strike+token."""
        return [
            o for o in self._orders.values()
            if o.is_live and o.strike == strike and o.token_side == token_side
        ]

    def get_order(self, order_id: str) -> SimOrder | None:
        """Get an order by ID, or None if not found."""
        return self._orders.get(order_id)

    def get_all_live_orders(self) -> list[SimOrder]:
        return [o for o in self._orders.values() if o.is_live]
