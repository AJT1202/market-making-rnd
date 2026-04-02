"""Fair value manager: computes and enforces monotonicity across strikes."""

from bt_engine.fair_value.pricer import FairValuePricer


class FairValueManager:
    """Manages fair value computation across multiple strikes.

    Responsibilities:
    - Compute FV for all strikes given current underlying price and time
    - Enforce monotonicity: P(S > K1) >= P(S > K2) for K1 < K2
    - Derive NO token FV: FV_NO = 10000 - FV_YES (in bps)
    """

    def __init__(self, pricer: FairValuePricer, strikes: list[int], expiry_utc_us: int) -> None:
        self.pricer = pricer
        self.strikes = sorted(strikes)
        self.expiry_utc_us = expiry_utc_us

    def compute_all(self, underlying_price_cents: int, current_time_us: int) -> dict[int, int]:
        """Compute YES token fair values for all strikes. Returns {strike: bps}."""
        tau_seconds = (self.expiry_utc_us - current_time_us) / 1_000_000
        values = {
            strike: self.pricer.compute(underlying_price_cents, strike, tau_seconds)
            for strike in self.strikes
        }
        return self.enforce_monotonicity(values)

    def enforce_monotonicity(self, values: dict[int, int]) -> dict[int, int]:
        """Enforce P(S > K1) >= P(S > K2) for K1 < K2.

        If violated, average the violating pair (integer averaging).
        Iterates until no violations remain.
        """
        result = {k: values[k] for k in self.strikes}
        changed = True
        while changed:
            changed = False
            for i in range(1, len(self.strikes)):
                k_prev = self.strikes[i - 1]
                k_curr = self.strikes[i]
                if result[k_curr] > result[k_prev]:
                    avg = (result[k_prev] + result[k_curr]) // 2
                    result[k_prev] = avg
                    result[k_curr] = avg
                    changed = True
        return result

    @staticmethod
    def yes_to_no_bps(yes_bps: int) -> int:
        """Return NO token fair value as complement of YES in basis points."""
        return 10000 - yes_bps
