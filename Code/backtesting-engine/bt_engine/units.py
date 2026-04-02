"""Integer conversion boundary.

ALL float-to-int conversion happens through these functions, called once
at data load time. Internal engine arithmetic is 100% integer.

Polymarket price grid: $0.01 to $0.99 in 1-cent increments.
  - 1 tick = $0.01
  - Price range: 1..99 ticks

Sizes in Telonex data have 2 decimal places.
  - 1 centishare = 0.01 shares
  - 10.00 shares = 1000 centishares

Cash is tracked in tick-centishares (tc):
  - $1.00 = 100 ticks
  - Buying 10 shares at $0.50 = 50 ticks * 1000 cs = 50_000 tc
"""

import math


# --- Price conversions (ticks, 1 tick = $0.01) ---

def price_str_to_ticks(s: str) -> int:
    """Convert a string price like "0.48" to integer ticks (48)."""
    return int(round(float(s) * 100))


def price_float_to_ticks(f: float) -> int:
    """Convert a float price like 0.48 to integer ticks (48)."""
    return int(round(f * 100))


def ticks_to_price(t: int) -> float:
    """Convert ticks back to float price. For display only."""
    return t / 100.0


# --- Size conversions (centishares, 1 cs = 0.01 shares) ---

def size_str_to_cs(s: str) -> int:
    """Convert a string size like "219.22" to centishares (21922)."""
    return int(round(float(s) * 100))


def size_float_to_cs(f: float) -> int:
    """Convert a float size to centishares."""
    return int(round(f * 100))


def cs_to_shares(cs: int) -> float:
    """Convert centishares to shares. For display only."""
    return cs / 100.0


# --- Underlying price conversions (cents) ---

def underlying_to_cents(f: float) -> int:
    """Convert underlying price like 165.06 to cents (16506)."""
    return int(round(f * 100))


def cents_to_price(c: int) -> float:
    """Convert cents to price. For display only."""
    return c / 100.0


# --- Cash (tick-centishares) ---

def tc_to_dollars(tc: int) -> float:
    """Convert tick-centishares to dollars. For display only.

    1 tick = $0.01, 1 centishare = 0.01 shares
    So tc = ticks * centishares, and dollars = tc / 10000
    Example: buy 10 shares ($0.50) = 50 * 1000 = 50000 tc = $5.00
    """
    return tc / 10_000.0


def dollars_to_tc(d: float) -> int:
    """Convert dollars to tick-centishares."""
    return int(round(d * 10_000))


# --- Fair value (basis points, 0-10000) ---

def probability_to_bps(p: float) -> int:
    """Convert probability [0.0, 1.0] to basis points [0, 10000]."""
    return int(round(p * 10_000))


def bps_to_probability(bps: int) -> float:
    """Convert basis points to probability. For display only."""
    return bps / 10_000.0


def bps_to_ticks(bps: int) -> int:
    """Convert basis points to ticks (divide by 100, round)."""
    return int(round(bps / 100.0))


def ticks_to_bps(ticks: int) -> int:
    """Convert ticks to basis points (multiply by 100)."""
    return ticks * 100
