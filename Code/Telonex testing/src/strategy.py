"""
Probability-based quoting strategy for Polymarket binary event markets.

Quotes around the Black-Scholes fair value with configurable spread,
position limits, and minimum edge requirements.
"""

from dataclasses import dataclass, field

from src.fill_simulator import Order, Side


@dataclass
class StrategyParams:
    """Strategy configuration parameters."""
    half_spread: float = 0.02       # Quote 2 cents around fair value
    max_position: int = 50          # Max position per market (long or short)
    min_edge: float = 0.03          # Minimum |FV - polymarket_mid| to quote
    order_size: float = 10.0        # Shares per order
    sigma: float = 0.50             # Implied vol for Black-Scholes


@dataclass
class StrategyState:
    """Mutable state tracked by the strategy."""
    next_order_id: int = 1

    def get_order_id(self) -> int:
        oid = self.next_order_id
        self.next_order_id += 1
        return oid


class MarketMakingStrategy:
    """
    Probability-based market making strategy.

    Logic:
    1. Compute Polymarket mid from BBO
    2. If |fair_value - polymarket_mid| < min_edge -> no orders
    3. Otherwise: bid at FV - half_spread, ask at FV + half_spread
    4. Clamp prices to [0.01, 0.99]
    5. Respect position limits
    6. Cancel and replace all resting orders each update
    """

    def __init__(self, params: StrategyParams | None = None):
        self.params = params or StrategyParams()
        self.state = StrategyState()

    def generate_orders(
        self,
        strike: int,
        fair_value: float,
        book_row: dict,
        position: float,
        timestamp_us: int,
    ) -> list[Order]:
        """
        Generate new orders for a single strike.

        Parameters
        ----------
        strike : int
            The strike price for this market.
        fair_value : float
            Black-Scholes fair value for YES token.
        book_row : dict
            Current orderbook snapshot.
        position : float
            Current position in this market (positive = long YES).
        timestamp_us : int
            Current timestamp.

        Returns
        -------
        list[Order]
            New orders to place (old orders are cancelled first by the engine).
        """
        best_bid = book_row.get("best_bid", 0)
        best_ask = book_row.get("best_ask", 0)

        # Need valid BBO to compute mid
        if best_bid <= 0 or best_ask <= 0 or best_ask <= best_bid:
            return []

        poly_mid = (best_bid + best_ask) / 2.0
        edge = abs(fair_value - poly_mid)

        # Not enough edge to justify quoting
        if edge < self.params.min_edge:
            return []

        orders = []
        p = self.params

        # Compute bid and ask prices
        bid_price = fair_value - p.half_spread
        ask_price = fair_value + p.half_spread

        # Clamp to valid Polymarket range
        bid_price = max(0.01, min(0.99, round(bid_price, 2)))
        ask_price = max(0.01, min(0.99, round(ask_price, 2)))

        # Ensure bid < ask
        if bid_price >= ask_price:
            return []

        # Place bid if position allows
        if position < p.max_position:
            orders.append(
                Order(
                    order_id=self.state.get_order_id(),
                    strike=strike,
                    side=Side.BUY,
                    price=bid_price,
                    size=min(p.order_size, p.max_position - position),
                    timestamp_us=timestamp_us,
                )
            )

        # Place ask if position allows
        if position > -p.max_position:
            orders.append(
                Order(
                    order_id=self.state.get_order_id(),
                    strike=strike,
                    side=Side.SELL,
                    price=ask_price,
                    size=min(p.order_size, p.max_position + position),
                    timestamp_us=timestamp_us,
                )
            )

        return orders

    def generate_all_orders(
        self,
        fair_values: dict[int, float],
        book_rows: dict[int, dict],
        positions: dict[int, float],
        timestamp_us: int,
    ) -> list[Order]:
        """
        Generate orders for all strikes, enforcing monotonicity.

        Parameters
        ----------
        fair_values : dict[int, float]
            Fair values per strike (already monotonicity-enforced).
        book_rows : dict[int, dict]
            Current book snapshots per strike.
        positions : dict[int, float]
            Current positions per strike.
        timestamp_us : int
            Current timestamp.

        Returns
        -------
        list[Order]
            All new orders across all strikes.
        """
        all_orders = []
        for strike, fv in sorted(fair_values.items()):
            if strike not in book_rows:
                continue
            pos = positions.get(strike, 0.0)
            orders = self.generate_orders(
                strike, fv, book_rows[strike], pos, timestamp_us
            )
            all_orders.extend(orders)

        return all_orders
