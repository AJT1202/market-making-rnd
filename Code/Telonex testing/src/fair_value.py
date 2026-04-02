"""
Black-Scholes binary call pricing for Polymarket YES tokens.

V_YES = Phi(d2)
d2 = [ln(S/K) + (r - 0.5*sigma^2)*tau] / (sigma*sqrt(tau))

where:
  S     = current NVDA price
  K     = strike price
  tau   = time to expiry in years
  sigma = annualized implied volatility
  r     = risk-free rate (0 for short-dated)
  Phi   = standard normal CDF
"""

import datetime

import numpy as np
from scipy.stats import norm


# Market close on March 30, 2026 = 4:00 PM ET = 20:00 UTC
EXPIRY_UTC = datetime.datetime(2026, 3, 30, 20, 0, 0, tzinfo=datetime.timezone.utc)
EXPIRY_US = int(EXPIRY_UTC.timestamp() * 1_000_000)

# Seconds per year for tau conversion
SECONDS_PER_YEAR = 365.25 * 24 * 3600


def compute_tau(timestamp_us: int) -> float:
    """
    Compute time to expiry in years from a UTC microsecond timestamp.
    Returns 0.0 if past expiry.
    """
    seconds_to_expiry = (EXPIRY_US - timestamp_us) / 1_000_000
    if seconds_to_expiry <= 0:
        return 0.0
    return seconds_to_expiry / SECONDS_PER_YEAR


def binary_call_price(
    S: float,
    K: float,
    tau: float,
    sigma: float = 0.50,
    r: float = 0.0,
) -> float:
    """
    Price a binary (digital) call option: P(S_T > K).

    Parameters
    ----------
    S : float
        Current underlying price.
    K : float
        Strike price.
    tau : float
        Time to expiry in years. Must be > 0.
    sigma : float
        Annualized implied volatility (default 0.50 = 50%).
    r : float
        Risk-free rate (default 0.0).

    Returns
    -------
    float
        Probability that S_T > K, i.e., fair value of YES token.
    """
    if tau <= 0:
        # At or past expiry: return intrinsic value
        return 1.0 if S > K else 0.0

    if S <= 0 or K <= 0 or sigma <= 0:
        return 0.0

    sqrt_tau = np.sqrt(tau)
    d2 = (np.log(S / K) + (r - 0.5 * sigma**2) * tau) / (sigma * sqrt_tau)
    return float(norm.cdf(d2))


def compute_fair_values(
    nvda_price: float,
    timestamp_us: int,
    strikes: list[int],
    sigma: float = 0.50,
) -> dict[int, float]:
    """
    Compute fair values for YES tokens across all strikes.

    Returns dict mapping strike -> fair_value.
    """
    tau = compute_tau(timestamp_us)
    values = {}
    for K in strikes:
        values[K] = binary_call_price(nvda_price, K, tau, sigma)
    return values


def enforce_monotonicity(fair_values: dict[int, float]) -> dict[int, float]:
    """
    Enforce monotonicity constraint: P(S > K1) >= P(S > K2) for K1 < K2.

    If violations exist, average the violating pair and re-check.
    In practice, Black-Scholes already satisfies this, but this is a safety net.
    """
    strikes = sorted(fair_values.keys())
    values = {k: fair_values[k] for k in strikes}

    # Simple enforcement: clamp each value to be <= the previous
    for i in range(1, len(strikes)):
        k_prev = strikes[i - 1]
        k_curr = strikes[i]
        if values[k_curr] > values[k_prev]:
            # Average the two
            avg = (values[k_prev] + values[k_curr]) / 2.0
            values[k_prev] = avg
            values[k_curr] = avg

    return values


if __name__ == "__main__":
    # Example: NVDA at $165.06, mid-day on March 30
    import datetime as dt

    test_time = dt.datetime(2026, 3, 30, 17, 0, 0, tzinfo=dt.timezone.utc)
    ts_us = int(test_time.timestamp() * 1_000_000)
    tau = compute_tau(ts_us)
    print(f"Time: {test_time}")
    print(f"Tau: {tau:.8f} years ({tau * 365.25 * 24:.2f} hours)")
    print()

    strikes = [160, 165, 170, 175, 180]
    fv = compute_fair_values(165.06, ts_us, strikes, sigma=0.50)
    for k, v in sorted(fv.items()):
        print(f"  Strike {k}: FV = {v:.4f}")
