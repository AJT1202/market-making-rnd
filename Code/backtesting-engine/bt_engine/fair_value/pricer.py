"""Black-Scholes binary call pricer returning integer basis points."""

import math
from typing import Protocol

from scipy.stats import norm

from bt_engine.units import probability_to_bps


class FairValuePricer(Protocol):
    """Protocol for fair value computation."""

    def compute(self, underlying_price_cents: int, strike_dollars: int, tau_seconds: float) -> int:
        """Return fair value of YES token in basis points (0-10000)."""
        ...


class BlackScholesPricer:
    """Black-Scholes binary call pricer.

    V_YES = Phi(d2)
    d2 = [ln(S/K) + (r - 0.5*sigma^2)*tau] / (sigma*sqrt(tau))

    Takes underlying_price_cents (int), strike_dollars (int), tau_seconds (float).
    Returns basis points (int, 0-10000).
    """

    SECONDS_PER_YEAR: float = 365.25 * 24 * 3600

    def __init__(self, sigma: float = 0.50, r: float = 0.0) -> None:
        self.sigma = sigma
        self.r = r

    def compute(self, underlying_price_cents: int, strike_dollars: int, tau_seconds: float) -> int:
        """Price a binary call: P(S_T > K), returned as basis points.

        At or past expiry (tau_seconds <= 0): 10000 if S > K else 0.
        Edge cases (S <= 0, K <= 0, sigma <= 0): returns 0.
        """
        S = underlying_price_cents / 100.0
        K = float(strike_dollars)

        if tau_seconds <= 0.0:
            return 10000 if S > K else 0

        if S <= 0.0 or K <= 0.0 or self.sigma <= 0.0:
            return 0

        tau = tau_seconds / self.SECONDS_PER_YEAR
        sqrt_tau = math.sqrt(tau)
        d2 = (math.log(S / K) + (self.r - 0.5 * self.sigma ** 2) * tau) / (self.sigma * sqrt_tau)
        probability = float(norm.cdf(d2))
        return probability_to_bps(probability)
