"""Portfolio: cash and position tracking across all strikes.

Cash is in tick-centishares (tc). All arithmetic is integer.
1 tc = 1 tick * 1 centishare = $0.0001
"""

from dataclasses import dataclass, field

from bt_engine.types import PositionMode, Side, TokenSide


@dataclass
class StrikePosition:
    """Position for one strike, tracking YES and NO tokens separately."""

    strike: int
    yes_position_cs: int = 0    # positive = long YES
    no_position_cs: int = 0     # positive = long NO
    reserved_cash_tc: int = 0   # cash reserved for pending orders


class Portfolio:
    """Manages cash and positions across all strikes.

    Cash is in tick-centishares (tc). All arithmetic is integer.

    For COLLATERAL_BACKED mode:
    - Buying YES:   costs price_ticks * size_cs tc
    - Selling YES (short): locks (100 - price_ticks) * size_cs tc as collateral
    - Buying NO:    costs price_ticks * size_cs tc
    - Selling NO (short):  locks (100 - price_ticks) * size_cs tc as collateral
    - Settlement:   YES resolves to 100 ticks/cs if YES wins, 0 if NO wins
                    NO  resolves to 100 ticks/cs if NO  wins, 0 if YES wins
    """

    def __init__(self, initial_cash_tc: int, strikes: list[int], mode: PositionMode) -> None:
        self.cash_tc: int = initial_cash_tc
        self.initial_cash_tc: int = initial_cash_tc
        self.positions: dict[int, StrikePosition] = {s: StrikePosition(strike=s) for s in strikes}
        self.mode = mode
        # Track total realised PnL for reconciliation
        self._realized_pnl_tc: int = 0

    # ------------------------------------------------------------------
    # Cash helpers
    # ------------------------------------------------------------------

    def available_cash_tc(self) -> int:
        """Cash minus all reservations."""
        total_reserved = sum(p.reserved_cash_tc for p in self.positions.values())
        return self.cash_tc - total_reserved

    # ------------------------------------------------------------------
    # Order reservation
    # ------------------------------------------------------------------

    def reserve_for_order(
        self,
        strike: int,
        token_side: TokenSide,
        side: Side,
        price_ticks: int,
        size_cs: int,
    ) -> bool:
        """Reserve cash for a pending order. Returns False if insufficient.

        BUY: reserve price_ticks * size_cs (cost to purchase)
        SELL with inventory: no reservation (selling what we own)
        SELL without inventory (short, COLLATERAL_BACKED): reserve (100 - price_ticks) * size_cs
        """
        pos = self.positions[strike]

        if side == Side.BUY:
            required_tc = price_ticks * size_cs
        else:
            # SELL: check if we have enough inventory to cover
            if token_side == TokenSide.YES:
                inventory_cs = pos.yes_position_cs
            else:
                inventory_cs = pos.no_position_cs

            if inventory_cs >= size_cs:
                # Selling from inventory — no cash reservation needed
                return True

            if self.mode == PositionMode.INVENTORY_BACKED:
                # Cannot short without inventory in this mode
                if inventory_cs < size_cs:
                    return False
                required_tc = 0
            else:
                # COLLATERAL_BACKED: short portion needs collateral
                short_cs = size_cs - max(0, inventory_cs)
                required_tc = (100 - price_ticks) * short_cs

        if required_tc > self.available_cash_tc():
            return False

        pos.reserved_cash_tc += required_tc
        return True

    def release_reservation(self, strike: int, amount_tc: int) -> None:
        """Release reservation when order is cancelled or filled."""
        pos = self.positions[strike]
        pos.reserved_cash_tc = max(0, pos.reserved_cash_tc - amount_tc)

    # ------------------------------------------------------------------
    # Fill application
    # ------------------------------------------------------------------

    def apply_fill(
        self,
        strike: int,
        token_side: TokenSide,
        side: Side,
        price_ticks: int,
        size_cs: int,
    ) -> None:
        """Apply a fill to positions and cash.

        BUY:  cash -= price_ticks * size_cs;  position += size_cs
        SELL: cash += price_ticks * size_cs;  position -= size_cs
        Releases the reservation that was set when the order was submitted.
        """
        pos = self.positions[strike]
        cost_tc = price_ticks * size_cs

        if side == Side.BUY:
            self.cash_tc -= cost_tc
            if self.cash_tc < 0:
                import warnings
                warnings.warn(f"Portfolio cash went negative: {self.cash_tc} tc", stacklevel=2)
            # Release the reservation we made at order submit time
            pos.reserved_cash_tc = max(0, pos.reserved_cash_tc - cost_tc)
            if token_side == TokenSide.YES:
                pos.yes_position_cs += size_cs
            else:
                pos.no_position_cs += size_cs
        else:
            # SELL
            self.cash_tc += cost_tc
            # Release collateral reservation for short (if any)
            if token_side == TokenSide.YES:
                short_cs = max(0, size_cs - max(0, pos.yes_position_cs))
                pos.yes_position_cs -= size_cs
            else:
                short_cs = max(0, size_cs - max(0, pos.no_position_cs))
                pos.no_position_cs -= size_cs

            collateral_released_tc = (100 - price_ticks) * short_cs
            pos.reserved_cash_tc = max(0, pos.reserved_cash_tc - collateral_released_tc)

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    def settle(self, strike: int, resolved_yes: bool) -> int:
        """Settle a strike at resolution. Returns settlement PnL in tc.

        YES position:
          - resolved YES  -> receives 100 ticks per cs
          - resolved NO   -> receives 0 ticks per cs
        NO position:
          - resolved NO   -> receives 100 ticks per cs
          - resolved YES  -> receives 0 ticks per cs

        Short positions (negative) represent liabilities:
          - short YES, resolved YES -> pay out 100 * |pos| tc (negative PnL)
          - short YES, resolved NO  -> collateral released, 0 liability
          - short NO,  resolved NO  -> pay out 100 * |pos| tc (negative PnL)
          - short NO,  resolved YES -> collateral released, 0 liability
        """
        pos = self.positions[strike]
        pnl_tc = 0

        # YES token settlement
        if pos.yes_position_cs != 0:
            if resolved_yes:
                pnl_tc += pos.yes_position_cs * 100  # long: profit; short: loss
            # else: YES resolves 0, no cash movement for long; collateral freed for short
            self.cash_tc += pos.yes_position_cs * 100 if resolved_yes else 0
            pos.yes_position_cs = 0

        # NO token settlement
        if pos.no_position_cs != 0:
            if not resolved_yes:
                pnl_tc += pos.no_position_cs * 100
                self.cash_tc += pos.no_position_cs * 100
            pos.no_position_cs = 0

        # Release any remaining reserved cash for this strike (collateral freed)
        self.cash_tc += pos.reserved_cash_tc
        pos.reserved_cash_tc = 0

        self._realized_pnl_tc += pnl_tc
        return pnl_tc

    def settle_all(self, resolutions: dict[int, bool]) -> int:
        """Settle all strikes. Returns total settlement PnL in tc."""
        total_pnl_tc = 0
        for strike, resolved_yes in resolutions.items():
            if strike in self.positions:
                total_pnl_tc += self.settle(strike, resolved_yes)
        return total_pnl_tc
