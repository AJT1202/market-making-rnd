"""
Breeden-Litzenberger Granularity Comparison
============================================
Compares B-L probabilities computed from options IV data at different
granularities (tick, 1s, 1m) to assess whether coarser intervals
produce materially different risk-neutral probabilities.

Input: Parquet files from ThetaData IV endpoint at tick/1s/1m resolution.
Output: Probability timeseries, diff plots, summary stats.

Usage:
    python scripts/bl_granularity_test.py
    python scripts/bl_granularity_test.py --data-dir D:/data/thetadata/granularity_test
"""

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # non-interactive backend for saving plots
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import polars as pl
from scipy.interpolate import UnivariateSpline, interp1d
from scipy.stats import norm


# ─── Constants ────────────────────────────────────────────────────────────────

TARGET_STRIKES = [155, 160, 162.5, 165, 167.5, 170, 175]
INTERVALS = ["tick", "1s", "1m"]
RISK_FREE_RATE = 0.045  # approximate SOFR
N_GRID = 400  # fine grid points for B-S repricing
GRID_EXTENSION = 0.05  # extend grid 5% beyond strike range
SMOOTHING_MULT = 0.001  # spline smoothing = len(strikes) * this
SPREAD_FILTER = 0.50  # max (ask-bid)/mid spread ratio
MIN_STRIKES = 5  # minimum valid strikes to run pipeline

# File mapping: interval -> filename
FILE_MAP = {
    "tick": "nvda_20260401_20260330_tick.parquet",
    "1s": "nvda_20260401_20260330_1s.parquet",
    "1m": "nvda_20260401_20260330_1m.parquet",
}

# Expiry: April 1, 2026 (from filenames: nvda_20260401_*)
EXPIRY_DATE = datetime(2026, 4, 1)
# Trade date: March 30, 2026
TRADE_DATE = datetime(2026, 3, 30)


# ─── B-L Pipeline ────────────────────────────────────────────────────────────


def bs_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes call price."""
    if sigma <= 0 or T <= 0:
        return max(S - K, 0.0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    return float(S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2))


def extract_chain_snapshot(df: pl.DataFrame, timestamp_us: int) -> pl.DataFrame:
    """Get latest IV row per (strike, right) at or before timestamp_us."""
    return (
        df.filter(pl.col("timestamp_us") <= timestamp_us)
        .sort("timestamp_us")
        .group_by(["strike", "right"])
        .last()
    )


def compute_otm_ivs(
    snapshot: pl.DataFrame,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float] | None:
    """
    Clean snapshot and compute OTM IVs.

    Returns (strikes, ivs, weights, forward, spot) or None if insufficient data.
    """
    if snapshot.height == 0:
        return None

    # Get spot price from underlying_price
    spot = snapshot.select(pl.col("underlying_price").drop_nulls().first()).item()
    if spot is None or spot <= 0:
        return None

    # Split into calls and puts
    calls = snapshot.filter(pl.col("right").str.to_uppercase().is_in(["C", "CALL"])).sort("strike")
    puts = snapshot.filter(pl.col("right").str.to_uppercase().is_in(["P", "PUT"])).sort("strike")

    if calls.height == 0 or puts.height == 0:
        return None

    # Compute implied forward: find K* with smallest |C_mid - P_mid|
    # Join calls and puts on strike
    joined = calls.select(
        pl.col("strike"),
        pl.col("midpoint").alias("call_mid"),
        pl.col("bid").alias("call_bid"),
        pl.col("ask").alias("call_ask"),
    ).join(
        puts.select(
            pl.col("strike"),
            pl.col("midpoint").alias("put_mid"),
            pl.col("bid").alias("put_bid"),
            pl.col("ask").alias("put_ask"),
        ),
        on="strike",
        how="inner",
    )

    if joined.height == 0:
        return None

    # Find strike with smallest |C_mid - P_mid|
    joined = joined.with_columns(
        (pl.col("call_mid") - pl.col("put_mid")).abs().alias("cp_diff")
    )
    atm_row = joined.sort("cp_diff").head(1)
    K_star = atm_row["strike"][0]
    C_star = atm_row["call_mid"][0]
    P_star = atm_row["put_mid"][0]

    # Days to expiry
    # T computed from trade date to expiry
    days_to_expiry = (EXPIRY_DATE - TRADE_DATE).days
    T = days_to_expiry / 365.0
    r = RISK_FREE_RATE

    # Implied forward: F = K* + e^(rT) * (C(K*) - P(K*))
    forward = K_star + np.exp(r * T) * (C_star - P_star)
    if forward <= 0:
        forward = spot  # fallback

    # Determine strike width for "near forward" zone
    all_strikes = snapshot.select("strike").unique().sort("strike")["strike"].to_numpy()
    if len(all_strikes) < 2:
        return None
    strike_width = float(np.median(np.diff(all_strikes)))

    # Build OTM IV table
    otm_strikes = []
    otm_ivs = []
    otm_weights = []

    # Process each unique strike
    for strike_val in all_strikes:
        strike_f = float(strike_val)

        if strike_f < forward - strike_width:
            # OTM put
            row = puts.filter(pl.col("strike") == strike_val)
            if row.height == 0:
                continue
            iv = row["implied_vol"][0]
            bid = row["bid"][0]
            ask = row["ask"][0]
            mid = row["midpoint"][0]
            bid_iv = row["bid_implied_vol"][0]
            ask_iv = row["ask_implied_vol"][0]
        elif strike_f > forward + strike_width:
            # OTM call
            row = calls.filter(pl.col("strike") == strike_val)
            if row.height == 0:
                continue
            iv = row["implied_vol"][0]
            bid = row["bid"][0]
            ask = row["ask"][0]
            mid = row["midpoint"][0]
            bid_iv = row["bid_implied_vol"][0]
            ask_iv = row["ask_implied_vol"][0]
        else:
            # Near forward: average call and put mid IV
            c_row = calls.filter(pl.col("strike") == strike_val)
            p_row = puts.filter(pl.col("strike") == strike_val)
            if c_row.height == 0 and p_row.height == 0:
                continue
            elif c_row.height == 0:
                iv = p_row["implied_vol"][0]
                bid = p_row["bid"][0]
                ask = p_row["ask"][0]
                mid = p_row["midpoint"][0]
                bid_iv = p_row["bid_implied_vol"][0]
                ask_iv = p_row["ask_implied_vol"][0]
            elif p_row.height == 0:
                iv = c_row["implied_vol"][0]
                bid = c_row["bid"][0]
                ask = c_row["ask"][0]
                mid = c_row["midpoint"][0]
                bid_iv = c_row["bid_implied_vol"][0]
                ask_iv = c_row["ask_implied_vol"][0]
            else:
                iv = (c_row["implied_vol"][0] + p_row["implied_vol"][0]) / 2.0
                bid = (c_row["bid"][0] + p_row["bid"][0]) / 2.0
                ask = (c_row["ask"][0] + p_row["ask"][0]) / 2.0
                mid = (c_row["midpoint"][0] + p_row["midpoint"][0]) / 2.0
                bid_iv = (c_row["bid_implied_vol"][0] + p_row["bid_implied_vol"][0]) / 2.0
                ask_iv = (c_row["ask_implied_vol"][0] + p_row["ask_implied_vol"][0]) / 2.0

        # Apply filters
        if bid is None or bid <= 0:
            continue
        if iv is None or iv <= 0:
            continue
        if mid is not None and mid > 0 and (ask - bid) / mid > SPREAD_FILTER:
            continue

        # Weight: 1 / (ask_iv - bid_iv) if spread > 0, else 1
        iv_spread = 0.0
        if ask_iv is not None and bid_iv is not None:
            iv_spread = ask_iv - bid_iv
        w = 1.0 / iv_spread if iv_spread > 0 else 1.0

        otm_strikes.append(strike_f)
        otm_ivs.append(float(iv))
        otm_weights.append(w)

    if len(otm_strikes) < MIN_STRIKES:
        return None

    strikes_arr = np.array(otm_strikes)
    ivs_arr = np.array(otm_ivs)
    weights_arr = np.array(otm_weights)

    # Sort by strike
    sort_idx = np.argsort(strikes_arr)
    strikes_arr = strikes_arr[sort_idx]
    ivs_arr = ivs_arr[sort_idx]
    weights_arr = weights_arr[sort_idx]

    return strikes_arr, ivs_arr, weights_arr, forward, spot


def fit_iv_smile(
    strikes: np.ndarray, ivs: np.ndarray, weights: np.ndarray
) -> UnivariateSpline | None:
    """Fit IV smile with smoothing spline."""
    try:
        s_param = len(strikes) * SMOOTHING_MULT
        spl = UnivariateSpline(strikes, ivs, w=weights, s=s_param)
        return spl
    except Exception:
        return None


def run_bl_pipeline(
    snapshot: pl.DataFrame, target_strikes: list[float]
) -> dict | None:
    """
    Run simplified B-L pipeline on a chain snapshot.

    Returns dict with per-strike probabilities and metadata, or None on failure.
    """
    # Step 2: Clean and compute OTM IVs
    result = compute_otm_ivs(snapshot)
    if result is None:
        return None
    strikes, ivs, weights, forward, spot = result

    # Step 3: Fit IV smile
    spl = fit_iv_smile(strikes, ivs, weights)
    if spl is None:
        return None

    # Compute fit RMSE
    fitted_ivs = spl(strikes)
    fit_rmse = float(np.sqrt(np.mean((ivs - fitted_ivs) ** 2)))

    # Step 4: Reprice on fine grid
    min_strike = strikes[0]
    max_strike = strikes[-1]
    K_grid = np.linspace(
        min_strike * (1 - GRID_EXTENSION),
        max_strike * (1 + GRID_EXTENSION),
        N_GRID,
    )
    iv_grid = spl(K_grid)
    iv_grid = np.clip(iv_grid, 0.01, 5.0)  # safety bounds

    days_to_expiry = (EXPIRY_DATE - TRADE_DATE).days
    T = days_to_expiry / 365.0
    r = RISK_FREE_RATE

    C_grid = np.array([bs_call(forward, K, T, r, iv) for K, iv in zip(K_grid, iv_grid)])

    # Step 5: Extract P(S_T > K) via first derivative
    dK = K_grid[1] - K_grid[0]
    dCdK = np.gradient(C_grid, dK)
    prob_above = -np.exp(r * T) * dCdK
    prob_above = np.clip(prob_above, 0, 1)

    # Step 6: Validation
    density = np.gradient(prob_above, dK)
    pct_negative = float((density < -1e-6).mean())
    integral = float(np.trapezoid(-dCdK * np.exp(r * T), K_grid))
    validation_passed = pct_negative < 0.10 and 0.8 < integral < 1.2

    # Interpolate to target strikes
    prob_func = interp1d(K_grid, prob_above, kind="linear", fill_value="extrapolate")
    prob_dict = {}
    for ks in target_strikes:
        if min_strike * (1 - GRID_EXTENSION) <= ks <= max_strike * (1 + GRID_EXTENSION):
            prob_dict[ks] = float(np.clip(prob_func(ks), 0, 1))
        else:
            prob_dict[ks] = None  # outside grid range

    return {
        "probabilities": prob_dict,
        "n_strikes": len(strikes),
        "underlying_price": spot,
        "forward": forward,
        "fit_rmse": fit_rmse,
        "validation_passed": validation_passed,
        "pct_negative_density": pct_negative,
        "integral": integral,
    }


# ─── Time Grid ────────────────────────────────────────────────────────────────


def build_time_grid(trade_date: datetime) -> list[int]:
    """
    Build 5-minute time grid from 09:30 to 16:00 ET as microsecond timestamps.

    Returns list of 78 timestamp_us values.
    """
    # Build datetimes for every 5 minutes from 09:30 to 16:00 inclusive
    # We work in naive datetime representing ET times since ThetaData
    # timestamps are in ET.
    base = trade_date.replace(hour=9, minute=30, second=0, microsecond=0)
    end = trade_date.replace(hour=16, minute=0, second=0, microsecond=0)

    times = []
    current = base
    while current <= end:
        # Convert to microseconds since epoch (treating as UTC for the
        # arithmetic — the raw data timestamps are also ET-as-epoch)
        epoch = datetime(1970, 1, 1)
        us = int((current - epoch).total_seconds() * 1_000_000)
        times.append(us)
        current += timedelta(minutes=5)

    return times


def us_to_et_str(timestamp_us: int) -> str:
    """Convert microsecond timestamp to readable ET string."""
    epoch = datetime(1970, 1, 1)
    dt = epoch + timedelta(microseconds=timestamp_us)
    return dt.strftime("%H:%M")


def us_to_datetime(timestamp_us: int) -> datetime:
    """Convert microsecond timestamp to datetime."""
    epoch = datetime(1970, 1, 1)
    return epoch + timedelta(microseconds=timestamp_us)


# ─── Data Loading ─────────────────────────────────────────────────────────────


def load_data(data_dir: Path) -> dict[str, pl.DataFrame]:
    """Load all interval Parquet files and add timestamp_us column."""
    data = {}
    for interval, filename in FILE_MAP.items():
        filepath = data_dir / filename
        if not filepath.exists():
            print(f"WARNING: missing {filepath}, skipping interval '{interval}'")
            continue

        print(f"Loading {interval}: {filepath}")
        df = pl.read_parquet(filepath)

        # Convert timestamp string to microseconds
        df = df.with_columns(
            pl.col("timestamp")
            .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f")
            .dt.epoch("us")
            .alias("timestamp_us")
        )

        print(f"  {interval}: {df.height:,} rows, {df['strike'].n_unique()} unique strikes")
        data[interval] = df

    return data


# ─── Main Comparison ──────────────────────────────────────────────────────────


def run_comparison(data: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Run B-L pipeline at every 5-min mark for each interval."""
    time_points = build_time_grid(TRADE_DATE)
    total = len(time_points) * len(INTERVALS)
    print(f"\nRunning {total} B-L computations ({len(time_points)} time points x {len(INTERVALS)} intervals)")

    results = []
    done = 0
    skipped = 0
    t_start = time.time()

    for t_us in time_points:
        for interval in INTERVALS:
            done += 1
            if interval not in data:
                skipped += 1
                continue

            # Step 1: Extract chain snapshot
            snapshot = extract_chain_snapshot(data[interval], t_us)

            if snapshot.height < MIN_STRIKES * 2:
                # Need at least MIN_STRIKES strikes with both C and P
                skipped += 1
                if done % 50 == 0:
                    elapsed = time.time() - t_start
                    print(f"  [{done}/{total}] {us_to_et_str(t_us)} {interval} - skipped (too few rows) [{elapsed:.1f}s]")
                continue

            bl_result = run_bl_pipeline(snapshot, TARGET_STRIKES)
            if bl_result is None:
                skipped += 1
                if done % 50 == 0:
                    elapsed = time.time() - t_start
                    print(f"  [{done}/{total}] {us_to_et_str(t_us)} {interval} - skipped (pipeline failed) [{elapsed:.1f}s]")
                continue

            for strike, prob in bl_result["probabilities"].items():
                if prob is not None:
                    results.append({
                        "timestamp_us": t_us,
                        "time_et": us_to_et_str(t_us),
                        "strike": strike,
                        "interval": interval,
                        "prob_above": prob,
                        "n_strikes": bl_result["n_strikes"],
                        "underlying_price": bl_result["underlying_price"],
                        "forward": bl_result["forward"],
                        "fit_rmse": bl_result["fit_rmse"],
                        "validation_passed": bl_result["validation_passed"],
                        "pct_negative_density": bl_result["pct_negative_density"],
                        "integral": bl_result["integral"],
                    })

            if done % 20 == 0:
                elapsed = time.time() - t_start
                print(f"  [{done}/{total}] {us_to_et_str(t_us)} {interval} [{elapsed:.1f}s]")

    elapsed = time.time() - t_start
    print(f"\nCompleted: {done - skipped} successful, {skipped} skipped, {elapsed:.1f}s total")

    if not results:
        print("ERROR: No results produced. Check data files and timestamps.")
        sys.exit(1)

    return pl.DataFrame(results)


# ─── Analysis & Output ────────────────────────────────────────────────────────


def compute_summary(results_df: pl.DataFrame) -> pl.DataFrame:
    """Compute per (strike, interval) summary stats vs tick baseline."""
    # Pivot: for each (timestamp_us, strike), get prob by interval
    tick_df = results_df.filter(pl.col("interval") == "tick").select(
        "timestamp_us", "strike",
        pl.col("prob_above").alias("prob_tick"),
        pl.col("n_strikes").alias("n_strikes_tick"),
    )

    summaries = []
    for interval in ["1s", "1m"]:
        interval_df = results_df.filter(pl.col("interval") == interval).select(
            "timestamp_us", "strike",
            pl.col("prob_above").alias("prob_interval"),
            pl.col("n_strikes").alias("n_strikes_interval"),
        )

        joined = tick_df.join(interval_df, on=["timestamp_us", "strike"], how="inner")
        if joined.height == 0:
            continue

        joined = joined.with_columns(
            (pl.col("prob_interval") - pl.col("prob_tick")).alias("diff"),
            (pl.col("prob_interval") - pl.col("prob_tick")).abs().alias("abs_diff"),
            (pl.col("n_strikes_interval") - pl.col("n_strikes_tick")).alias("n_strikes_diff"),
        )

        summary = joined.group_by("strike").agg(
            pl.col("abs_diff").max().alias("max_abs_diff"),
            ((pl.col("diff") ** 2).mean().sqrt()).alias("rmse_vs_tick"),
            pl.col("abs_diff").mean().alias("mean_abs_diff"),
            pl.col("diff").mean().alias("mean_diff"),
            pl.col("n_strikes_diff").mean().alias("mean_n_strikes_diff"),
            pl.len().alias("n_observations"),
        ).with_columns(
            pl.lit(interval).alias("interval"),
        )

        summaries.append(summary)

    if not summaries:
        return pl.DataFrame()

    return pl.concat(summaries, how="diagonal_relaxed").sort(["strike", "interval"])


def save_plots(results_df: pl.DataFrame, output_dir: Path) -> None:
    """Generate and save all comparison plots."""
    # Convert timestamp_us to datetime for plotting
    results_pd = results_df.to_pandas()
    epoch = datetime(1970, 1, 1)
    results_pd["datetime"] = results_pd["timestamp_us"].apply(
        lambda x: epoch + timedelta(microseconds=int(x))
    )

    colors = {"tick": "#1f77b4", "1s": "#ff7f0e", "1m": "#2ca02c"}
    styles = {"tick": "-", "1s": "--", "1m": ":"}

    # 1. Probability timeseries per strike
    for strike in TARGET_STRIKES:
        fig, ax = plt.subplots(figsize=(12, 5))
        strike_data = results_pd[results_pd["strike"] == strike]
        if strike_data.empty:
            plt.close(fig)
            continue

        for interval in INTERVALS:
            idata = strike_data[strike_data["interval"] == interval].sort_values("datetime")
            if idata.empty:
                continue
            ax.plot(
                idata["datetime"], idata["prob_above"],
                label=interval, color=colors[interval],
                linestyle=styles[interval], linewidth=1.5,
            )

        ax.set_title(f"P(S_T > {strike}) — B-L Probability by Interval")
        ax.set_xlabel("Time (ET)")
        ax.set_ylabel("P(S_T > K)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(output_dir / f"prob_timeseries_{int(strike)}.png", dpi=150)
        plt.close(fig)
        print(f"  Saved prob_timeseries_{int(strike)}.png")

    # 2. Diff timeseries (interval - tick) per strike
    tick_data = results_pd[results_pd["interval"] == "tick"][
        ["datetime", "timestamp_us", "strike", "prob_above"]
    ].rename(columns={"prob_above": "prob_tick"})

    for strike in TARGET_STRIKES:
        fig, ax = plt.subplots(figsize=(12, 5))
        tick_s = tick_data[tick_data["strike"] == strike]
        if tick_s.empty:
            plt.close(fig)
            continue

        has_data = False
        for interval in ["1s", "1m"]:
            idata = results_pd[
                (results_pd["interval"] == interval) & (results_pd["strike"] == strike)
            ][["datetime", "timestamp_us", "prob_above"]].rename(
                columns={"prob_above": "prob_interval"}
            )
            merged = tick_s.merge(idata, on="timestamp_us", suffixes=("", "_int"))
            if merged.empty:
                continue
            merged["diff"] = merged["prob_interval"] - merged["prob_tick"]
            merged = merged.sort_values("datetime")
            ax.plot(
                merged["datetime"], merged["diff"],
                label=f"{interval} - tick", color=colors[interval],
                linestyle=styles[interval], linewidth=1.5,
            )
            has_data = True

        if not has_data:
            plt.close(fig)
            continue

        ax.axhline(y=0, color="black", linewidth=0.5)
        ax.set_title(f"P(S_T > {strike}) Difference vs Tick")
        ax.set_xlabel("Time (ET)")
        ax.set_ylabel("P(interval) - P(tick)")
        ax.legend()
        ax.grid(True, alpha=0.3)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(output_dir / f"diff_timeseries_{int(strike)}.png", dpi=150)
        plt.close(fig)
        print(f"  Saved diff_timeseries_{int(strike)}.png")

    # 3. Strike count over the day by interval
    fig, ax = plt.subplots(figsize=(12, 5))
    for interval in INTERVALS:
        idata = results_pd[results_pd["interval"] == interval]
        if idata.empty:
            continue
        # One n_strikes per (timestamp_us, interval) — take first strike's value
        counts = idata.groupby("datetime")["n_strikes"].first().reset_index()
        counts = counts.sort_values("datetime")
        ax.plot(
            counts["datetime"], counts["n_strikes"],
            label=interval, color=colors[interval],
            linestyle=styles[interval], linewidth=1.5,
        )

    ax.set_title("Valid Strikes Over the Day by Interval")
    ax.set_xlabel("Time (ET)")
    ax.set_ylabel("Number of Valid Strikes")
    ax.legend()
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(output_dir / "strike_count.png", dpi=150)
    plt.close(fig)
    print("  Saved strike_count.png")

    # 4. Summary table as image
    save_summary_table_plot(results_df, output_dir)


def save_summary_table_plot(results_df: pl.DataFrame, output_dir: Path) -> None:
    """Render summary metrics as a formatted table image."""
    summary = compute_summary(results_df)
    if summary.height == 0:
        print("  WARNING: No summary data to plot")
        return

    summary_pd = summary.to_pandas()

    # Build table data
    col_labels = [
        "Strike", "Interval", "Max |Diff|", "RMSE vs Tick",
        "Mean |Diff|", "Mean Diff", "Mean dN_Strikes", "N Obs",
    ]
    cell_text = []
    for _, row in summary_pd.iterrows():
        cell_text.append([
            f"{row['strike']:.1f}",
            row["interval"],
            f"{row['max_abs_diff']:.6f}",
            f"{row['rmse_vs_tick']:.6f}",
            f"{row['mean_abs_diff']:.6f}",
            f"{row['mean_diff']:.6f}",
            f"{row['mean_n_strikes_diff']:.1f}",
            f"{int(row['n_observations'])}",
        ])

    fig, ax = plt.subplots(figsize=(14, max(3, 0.4 * len(cell_text) + 1.5)))
    ax.axis("off")
    table = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        loc="center",
        cellLoc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.4)

    # Style header row
    for j in range(len(col_labels)):
        table[0, j].set_facecolor("#4472C4")
        table[0, j].set_text_props(color="white", fontweight="bold")

    # Alternate row colors
    for i in range(1, len(cell_text) + 1):
        color = "#D9E2F3" if i % 2 == 0 else "white"
        for j in range(len(col_labels)):
            table[i, j].set_facecolor(color)

    fig.suptitle("B-L Granularity Comparison Summary", fontweight="bold", fontsize=12)
    fig.tight_layout()
    fig.savefig(output_dir / "summary_table.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("  Saved summary_table.png")


def print_summary(results_df: pl.DataFrame) -> None:
    """Print summary metrics to stdout."""
    summary = compute_summary(results_df)
    if summary.height == 0:
        print("\nNo summary data (need tick baseline + at least one other interval)")
        return

    print("\n" + "=" * 90)
    print("SUMMARY: B-L Granularity Comparison (vs tick baseline)")
    print("=" * 90)

    header = (
        f"{'Strike':>8}  {'Interval':>8}  {'Max|Diff|':>10}  {'RMSE':>10}  "
        f"{'Mean|Diff|':>10}  {'MeanDiff':>10}  {'dN_Strikes':>10}  {'N':>5}"
    )
    print(header)
    print("-" * 90)

    for row in summary.iter_rows(named=True):
        print(
            f"{row['strike']:>8.1f}  {row['interval']:>8}  "
            f"{row['max_abs_diff']:>10.6f}  {row['rmse_vs_tick']:>10.6f}  "
            f"{row['mean_abs_diff']:>10.6f}  {row['mean_diff']:>10.6f}  "
            f"{row['mean_n_strikes_diff']:>10.1f}  {row['n_observations']:>5}"
        )

    print("=" * 90)

    # Also print per-interval aggregates
    print("\nPer-interval aggregate (across all strikes):")
    for interval in ["1s", "1m"]:
        rows = summary.filter(pl.col("interval") == interval)
        if rows.height == 0:
            continue
        avg_rmse = rows["rmse_vs_tick"].mean()
        avg_max = rows["max_abs_diff"].mean()
        avg_mean = rows["mean_abs_diff"].mean()
        print(
            f"  {interval}: avg RMSE={avg_rmse:.6f}, "
            f"avg Max|Diff|={avg_max:.6f}, avg Mean|Diff|={avg_mean:.6f}"
        )


# ─── CLI Entry Point ──────────────────────────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare B-L probabilities across tick/1s/1m granularities."
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="D:/data/thetadata/granularity_test",
        help="Directory containing the granularity test Parquet files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_dir = data_dir / "results"

    print(f"Data directory: {data_dir}")
    if not data_dir.exists():
        print(f"ERROR: data directory does not exist: {data_dir}")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {output_dir}")
    print(f"Trade date: {TRADE_DATE.strftime('%Y-%m-%d')}")
    print(f"Expiry date: {EXPIRY_DATE.strftime('%Y-%m-%d')}")
    print(f"Days to expiry: {(EXPIRY_DATE - TRADE_DATE).days}")
    print(f"Target strikes: {TARGET_STRIKES}")
    print()

    # Load data
    data = load_data(data_dir)
    if not data:
        print("ERROR: No data files loaded.")
        sys.exit(1)

    # Run comparison
    results_df = run_comparison(data)

    # Save results
    print(f"\nSaving results ({results_df.height:,} rows)...")
    results_df.write_parquet(output_dir / "probabilities.parquet")
    print(f"  Saved probabilities.parquet")

    # Compute and save summary
    summary_df = compute_summary(results_df)
    if summary_df.height > 0:
        summary_df.write_csv(output_dir / "summary.csv")
        print(f"  Saved summary.csv")

    # Generate plots
    print("\nGenerating plots...")
    save_plots(results_df, output_dir)

    # Print summary to stdout
    print_summary(results_df)

    print("\nDone.")


if __name__ == "__main__":
    main()
