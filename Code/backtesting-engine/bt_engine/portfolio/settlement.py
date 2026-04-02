"""Settlement engine: resolve markets and verify portfolio invariants."""

from bt_engine.portfolio.positions import Portfolio


class SettlementEngine:
    """Handles market resolution and position settlement."""

    @staticmethod
    def settle(portfolio: Portfolio, resolutions: dict[int, bool | None]) -> dict[int, int]:
        """Settle all resolved markets. Returns {strike: settlement_pnl_tc}.

        Strikes with resolution=None are skipped (unresolved).
        """
        results: dict[int, int] = {}
        for strike, resolved in resolutions.items():
            if resolved is None:
                continue
            if strike not in portfolio.positions:
                continue
            pnl_tc = portfolio.settle(strike, resolved)
            results[strike] = pnl_tc
        return results

    @staticmethod
    def check_reconciliation(portfolio: Portfolio) -> bool:
        """Verify portfolio invariants hold. Returns True if consistent.

        After full settlement all positions should be zero and all
        reservations should be zero. Cash consistency is always checked.
        """
        all_positions_zero = all(
            p.yes_position_cs == 0 and p.no_position_cs == 0
            for p in portfolio.positions.values()
        )
        all_reservations_zero = all(
            p.reserved_cash_tc == 0
            for p in portfolio.positions.values()
        )
        cash_non_negative = portfolio.cash_tc >= 0
        return all_positions_zero and all_reservations_zero and cash_non_negative
