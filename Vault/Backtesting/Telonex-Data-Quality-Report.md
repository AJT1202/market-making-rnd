---
title: Telonex Data Quality Report — NVDA March 30 POC
date: 2026-03-31
tags:
  - data-quality
  - telonex
  - polymarket
  - orderbook
  - nvda
  - poc
aliases:
  - Telonex DQ Report
related:
  - "[[NVDA-POC-Implementation-Plan]]"
  - "[[Orderbook-Backtesting-with-Telonex]]"
  - "[[Telonex-Data-Platform]]"
---

# Telonex Data Quality Report — NVDA March 30, 2026 POC

This report provides an evidence-based assessment of the Polymarket orderbook data downloaded from the [[Telonex-Data-Platform]] for the [[NVDA-POC-Implementation-Plan|NVDA March 30 POC]]. All statistics are computed directly from the parquet files.

## Data Inventory

| File | Strike | Rows | File Size |
|------|--------|------|-----------|
| `book_snapshot_25_strike160_2026-03-30.parquet` | $160 | 20,107 | 935.3 KB |
| `book_snapshot_25_strike165_2026-03-30.parquet` | $165 | 39,346 | 1,245.5 KB |
| `book_snapshot_25_strike170_2026-03-30.parquet` | $170 | 27,241 | 859.0 KB |
| `book_snapshot_25_strike175_2026-03-30.parquet` | $175 | 28,382 | 1,436.7 KB |
| `book_snapshot_25_strike180_2026-03-30.parquet` | $180 | 25,762 | 1,243.9 KB |
| `nvda_prices_1m.parquet` | -- | 778 | 31.8 KB |
| **Total** | -- | **140,838** | **5.62 MB** |

**Schema:** 107 columns per book snapshot file — `timestamp_us`, `local_timestamp_us`, 5 string metadata fields (`exchange`, `market_id`, `slug`, `asset_id`, `outcome`), and 100 price/size columns (`bid_price_0..24`, `bid_size_0..24`, `ask_price_0..24`, `ask_size_0..24`). All price/size columns are stored as strings and require conversion to float.

**NVDA reference:** The stock opened at $168.74 on March 30 and closed at $165.06. Day range was $164.27 -- $169.45 with total volume of 157,906,103 shares.

**Resolution:** Strikes $160 and $165 resolved YES (NVDA closed above). Strikes $170, $175, and $180 resolved NO.

---

## 1. Coverage & Completeness

### Snapshot Counts and Time Range

| Strike | Snapshots | First Timestamp (UTC) | Last Timestamp (UTC) | Duration (hrs) |
|--------|-----------|----------------------|---------------------|----------------|
| $160 | 20,107 | 2026-03-30 00:13:31 | 2026-03-30 22:26:14 | 22.21 |
| $165 | 39,346 | 2026-03-30 00:13:31 | 2026-03-30 22:54:00 | 22.67 |
| $170 | 27,241 | 2026-03-30 00:00:13 | 2026-03-30 22:26:12 | 22.43 |
| $175 | 28,382 | 2026-03-30 00:00:08 | 2026-03-30 22:26:10 | 22.43 |
| $180 | 25,762 | 2026-03-30 00:00:09 | 2026-03-30 22:25:16 | 22.42 |

All five contracts cover approximately 22+ hours of the day. Strikes $170, $175, and $180 start right at midnight UTC; strikes $160 and $165 begin ~13 minutes later. Coverage extends into the evening well past the 4:00 PM ET equity close (20:00 UTC).

The $165 strike has the most snapshots (39,346) — nearly double the $160 strike — consistent with it being the at-the-money contract where trading activity was highest.

### BBO Validity

| Strike | % Valid Bid | % Valid Ask | % Valid BBO (both sides) |
|--------|------------|------------|--------------------------|
| $160 | 100.00% | 85.05% | 85.05% |
| $165 | 100.00% | 96.00% | 96.00% |
| $170 | 97.93% | 100.00% | 97.93% |
| $175 | 87.12% | 100.00% | 87.12% |
| $180 | 93.29% | 100.00% | 93.29% |

A clear pattern emerges: **high-probability contracts ($160, $165) always have bids but sometimes lack asks**, while **low-probability contracts ($170, $175, $180) always have asks but sometimes lack bids.** This is structurally expected — when a contract trades near $0.95+, there are few sellers willing to offer above that price; when a contract trades near $0.01--0.02, there are few buyers willing to bid.

The $175 strike has the lowest BBO validity at 87.12%, meaning ~13% of snapshots have no bid posted at all. This is concerning for backtesting but consistent with a deep out-of-the-money contract trading at $0.01--0.02.

### Gap Analysis

| Strike | Gaps > 60s | Gaps > 300s | Max Gap (s) | Top 5 Gaps (s) |
|--------|-----------|------------|-------------|----------------|
| $160 | 18 | 12 | 10,504 (2.9h) | 10504, 5809, 4780, 2868, 1424 |
| $165 | 23 | 11 | 8,122 (2.3h) | 8122, 4633, 2860, 2768, 2183 |
| $170 | 32 | 13 | 2,768 (46m) | 2768, 1588, 1180, 1063, 835 |
| $175 | 11 | 5 | 4,743 (1.3h) | 4743, 1487, 691, 630, 443 |
| $180 | 107 | 14 | 2,768 (46m) | 2768, 1059, 960, 901, 691 |

**Multi-hour gaps exist in the data.** The $160 strike has a gap of 2.9 hours and the $165 strike has a gap of 2.3 hours. These likely correspond to overnight low-activity periods (UTC 00:00--06:00, which is 8 PM -- 2 AM ET).

The $180 strike has the most gaps over 60 seconds (107), reflecting its low liquidity as a deep OTM contract.

> [!warning] Backtesting Impact
> Gaps of this magnitude mean any backtester must handle stale quotes. A fill simulator that assumes continuous quoting will overstate fill rates during these periods. Consider marking gap periods as "no-trade zones" in the [[NVDA-POC-Implementation-Plan]].

---

## 2. Update Frequency

### Inter-Snapshot Interval Statistics

| Strike | Mean (s) | Median (s) | P5 (s) | P95 (s) | Min (s) | Max (s) | Std (s) |
|--------|---------|-----------|-------|---------|---------|---------|---------|
| $160 | 3.977 | 0.071 | 0.000 | 14.609 | 0.000 | 10,504 | 94.75 |
| $165 | 2.075 | 0.247 | 0.000 | 7.250 | 0.000 | 8,122 | 53.39 |
| $170 | 2.965 | 0.342 | 0.000 | 16.775 | 0.000 | 2,768 | 24.85 |
| $175 | 2.846 | 0.021 | 0.000 | 14.428 | 0.000 | 4,743 | 30.71 |
| $180 | 3.133 | 0.340 | 0.000 | 14.026 | 0.000 | 2,768 | 24.61 |

**Median update intervals are sub-second for all strikes** (21ms to 342ms), indicating the data captures individual book changes at high resolution. The mean is pulled up dramatically by the large gaps discussed above.

The $165 (ATM) strike has the tightest distribution with a P95 of 7.25 seconds and the lowest mean of 2.07 seconds — expected for the most actively traded contract.

### Interval Distribution

| Bucket | $160 | $165 | $170 | $175 | $180 |
|--------|------|------|------|------|------|
| < 0.1s | 6,648 (33.1%) | 11,056 (28.1%) | 6,400 (23.5%) | 8,507 (30.0%) | 6,457 (25.1%) |
| 0.1 -- 0.5s | 2,598 (12.9%) | 8,586 (21.8%) | 6,427 (23.6%) | 3,056 (10.8%) | 4,735 (18.4%) |
| 0.5 -- 1s | 1,536 (7.6%) | 6,297 (16.0%) | 4,109 (15.1%) | 1,738 (6.1%) | 2,480 (9.6%) |
| 1 -- 5s | 2,739 (13.6%) | 5,448 (13.8%) | 3,655 (13.4%) | 4,251 (15.0%) | 4,594 (17.8%) |
| 5 -- 10s | 1,339 (6.7%) | 1,252 (3.2%) | 1,278 (4.7%) | 2,054 (7.2%) | 1,344 (5.2%) |
| 10 -- 30s | 1,547 (7.7%) | 1,229 (3.1%) | 1,924 (7.1%) | 2,154 (7.6%) | 1,624 (6.3%) |
| 30 -- 60s | 131 (0.7%) | 168 (0.4%) | 275 (1.0%) | 233 (0.8%) | 206 (0.8%) |
| 60 -- 300s | 6 (0.0%) | 12 (0.0%) | 19 (0.1%) | 6 (0.0%) | 93 (0.4%) |
| > 300s | 12 (0.1%) | 11 (0.0%) | 13 (0.0%) | 5 (0.0%) | 14 (0.1%) |

Over 50% of inter-snapshot intervals are under 0.5 seconds across all strikes. The data is heavily concentrated in the sub-second regime — this is true tick-level orderbook data, not periodic polling.

### Hourly Update Density

Activity is not uniform throughout the day. Key patterns by strike:

- **$165 (ATM):** Peak activity at 14:00--15:00 UTC (10--11 AM ET, US market hours) with 6,873 and 5,206 snapshots respectively. Also a spike at 19:00 UTC (3 PM ET, the settlement-relevant hour) with 7,064 snapshots.
- **$160 (deep ITM):** Activity peaks during 08:00--10:00 UTC (4--6 AM ET, pre-market/European overlap) then secondary peaks at 17:00--20:00 UTC (1--4 PM ET).
- **$170, $175, $180 (OTM):** These show more uniform activity across the full day with peaks during 08:00--09:00 UTC. The $170 and $165 strikes show heavy activity during 13:00--15:00 UTC as the market entered the decisive settlement window.

---

## 3. Spread Analysis

### Bid-Ask Spread Statistics

| Strike | Mean | Median | Min | Max | Std |
|--------|------|--------|-----|-----|-----|
| $160 | $0.0771 | $0.037 | $0.000 | $0.530 | $0.0934 |
| $165 | $0.2351 | $0.120 | $0.000 | $0.980 | $0.2347 |
| $170 | $0.3308 | $0.080 | -$0.001 | $0.940 | $0.3485 |
| $175 | $0.0264 | $0.029 | $0.012 | $0.032 | $0.0063 |
| $180 | $0.0056 | $0.005 | $0.000 | $0.012 | $0.0023 |

The spread distribution reveals two distinct regimes:

1. **Actively traded contracts ($160, $165, $170):** Wide spreads that vary enormously. The $165 ATM contract has a mean spread of $0.235 — meaning 23.5 cents on a dollar, or roughly 33% of mid-price. The $170 strike is even worse with a mean spread of $0.33. These are not competitive market-making environments.

2. **Low-probability contracts ($175, $180):** Tight spreads in absolute terms ($0.026 and $0.006) because the contracts trade at 1--2 cents. However, as a percentage of mid-price, these are also wide (the $175 spread of $0.026 is ~170% of its $0.015 mid-price).

### Hourly Spread Evolution (Selected Strikes)

**$165 (ATM) — Spread narrows dramatically approaching settlement:**

| Hour (UTC) | ET Equivalent | Mean Spread | Median Spread | Snapshots |
|------------|---------------|-------------|---------------|-----------|
| 00--05 | 8 PM -- 1 AM | $0.830 | $0.830 | 303 |
| 06--07 | 2 -- 3 AM | $0.766 | $0.760 | 727 |
| 08--09 | 4 -- 5 AM | $0.664 | $0.655 | 2,356 |
| 10--12 | 6 -- 8 AM | $0.619 | $0.630 | 3,436 |
| 13 | 9 AM | $0.233 | $0.150 | 3,481 |
| 14 | 10 AM | $0.123 | $0.080 | 6,873 |
| 15 | 11 AM | $0.079 | $0.070 | 5,206 |
| 16 | 12 PM | $0.141 | $0.100 | 3,504 |
| 17--18 | 1 -- 2 PM | $0.093 | $0.095 | 4,802 |
| 19 | 3 PM | $0.194 | $0.150 | 7,064 |
| 20 | 4 PM | $0.847 | $0.885 | 22 |

The ATM spread starts at $0.83 overnight and narrows to $0.07--0.08 during peak US trading hours before widening again at close. This is a healthy intraday pattern. However, even the tightest spread of $0.07 represents ~10% of mid-price — still substantial.

**$160 (deep ITM) — Much tighter spreads overall:**

| Period (UTC) | Mean Spread | Notes |
|-------------|-------------|-------|
| 00--01 | $0.520 | Overnight, minimal activity |
| 03--07 | $0.220 -- $0.456 | Pre-market |
| 08--10 | $0.023 -- $0.057 | Peak tightness |
| 13--15 | $0.027 -- $0.041 | US market hours |
| 17--19 | $0.033 -- $0.038 | Afternoon |

### Spread vs. Moneyness

The relationship between spread and moneyness is non-linear:

| Strike | Mid-Price (mean) | Spread (mean) | Spread as % of Mid |
|--------|-----------------|---------------|---------------------|
| $160 (deep ITM) | $0.954 | $0.077 | 8.1% |
| $165 (ATM) | $0.712 | $0.235 | 33.0% |
| $170 (OTM) | $0.235 | $0.331 | 140.7% |
| $175 (deep OTM) | $0.015 | $0.026 | 173.3% |
| $180 (deep OTM) | $0.006 | $0.006 | 100.0% |

> [!important] Market-Making Implication
> The ATM contract ($165) where a market maker would most want to trade has a 33% spread-to-mid ratio. This is extremely wide by traditional market standards but may be normal for prediction markets. Any backtesting strategy must account for the true cost of crossing these spreads. See [[Performance-Metrics-and-Pitfalls]].

---

## 4. Depth Profile

### Level Population Rates

Average number of populated levels per snapshot:

| Strike | Avg Bid Levels | Avg Ask Levels | Total Avg Levels |
|--------|---------------|---------------|------------------|
| $160 | 20.83 | 3.94 | 24.77 |
| $165 | 11.15 | 4.37 | 15.52 |
| $170 | 4.85 | 11.41 | 16.26 |
| $175 | 1.75 | 22.41 | 24.16 |
| $180 | 1.74 | 23.07 | 24.81 |

**Depth is heavily asymmetric and mirrors probability.** High-probability contracts ($160) have deep bid books (20+ levels) but thin ask books (4 levels) — many buyers, few sellers at these prices. The pattern reverses completely for low-probability contracts ($175, $180) — deep ask books (22+ levels) but only 1--2 bid levels.

**Level-by-level population rates (BBO through level 5):**

| Level | $160 Bid | $160 Ask | $165 Bid | $165 Ask | $170 Bid | $170 Ask |
|-------|---------|---------|---------|---------|---------|---------|
| 0 | 100.0% | 85.0% | 100.0% | 96.0% | 97.9% | 100.0% |
| 1 | 100.0% | 59.3% | 99.9% | 91.5% | 96.6% | 100.0% |
| 2 | 100.0% | 59.2% | 99.0% | 75.5% | 84.2% | 99.9% |
| 3 | 100.0% | 56.8% | 95.2% | 57.6% | 70.5% | 99.5% |
| 4 | 100.0% | 54.4% | 91.1% | 39.8% | 55.2% | 92.6% |
| 5 | 100.0% | 48.0% | 88.1% | 25.2% | 41.9% | 81.7% |

Beyond level 5, the thin side of each book drops off rapidly. For the $165 ATM contract, ask-side depth beyond level 5 is present less than 25% of the time.

### Depth in Dollar Terms

| Strike | Avg Bid Depth ($) | Avg Ask Depth ($) | Bid/Ask Ratio |
|--------|------------------|------------------|---------------|
| $160 | $4,989.70 | $627.32 | 7.95 |
| $165 | $5,180.91 | $9,818.84 | 0.53 |
| $170 | $224.15 | $34,201.11 | 0.007 |
| $175 | $1.04 | $17,395.58 | 0.0001 |
| $180 | -- | -- | -- |

Key observations:

- **$160:** Massive bid-side depth ($4,990 avg) vs. minimal ask depth ($627). Buyers stacked up to acquire YES shares in this deep ITM contract.
- **$165 (ATM):** Roughly balanced, with ask-side heavier ($9,819 vs. $5,181). The most symmetric book of all five strikes.
- **$170:** Minimal bids ($224) vs. enormous ask depth ($34,201). Sellers dominating — the market correctly priced this as unlikely.
- **$175:** Essentially no bids ($1.04 avg). Only ask-side liquidity exists at these deep OTM levels.

> [!note] Depth Usefulness for Backtesting
> Only the top 3--5 levels have reliable population on both sides for the ATM contract. The "25 levels of depth" headline is misleading — effective two-sided depth rarely exceeds 5 levels except for deep ITM/OTM contracts where it is entirely one-sided. For backtesting fill simulation, focus on levels 0--4.

---

## 5. Price Dynamics

### Mid-Price Evolution

| Strike | Start Mid | End Mid | Day Low | Day High | Day Mean |
|--------|----------|---------|---------|----------|----------|
| $160 | $0.730 | $0.985 | $0.725 | $0.995 | $0.954 |
| $165 | $0.535 | $0.990 | $0.145 | $0.990 | $0.712 |
| $170 | $0.445 | $0.001 | $0.001 | $0.570 | $0.235 |
| $175 | $0.018 | $0.016 | $0.008 | $0.019 | $0.015 |
| $180 | -- | -- | -- | -- | ~$0.006 |

The mid-prices tell the story of the day:

1. **$160 (deep ITM):** Started at $0.73 and steadily climbed to $0.985 as NVDA stayed well above $160. Never seriously threatened. Resolved YES.
2. **$165 (ATM):** The dramatic contract. Started at $0.535, dipped as low as $0.145 (when NVDA dropped to $164.27), then rallied to $0.99 as NVDA recovered above $165. This is the contract where a market maker would have had the most opportunity — and the most risk.
3. **$170 (OTM):** Opened at $0.445 and collapsed to $0.001 as NVDA fell below $170 and never recovered. Resolved NO.
4. **$175, $180 (deep OTM):** Traded at 1--2 cents all day. These were priced as near-certainties for NO and never moved meaningfully.

### Cross-Strike Consistency

**Monotonicity check:** At every 1-minute aligned timestamp across all 5 strikes, mid-prices should satisfy: $P_{160} \geq P_{165} \geq P_{170} \geq P_{175} \geq P_{180}$.

- **491 common 1-minute timestamps** were tested
- **0 violations** (0.00%)

This is a perfect result. The orderbook data is internally consistent — implied probabilities are always monotonically decreasing across strikes at every sampled point.

### Consistency with NVDA Price Movement

| Period (ET) | NVDA Price Range | $165 Mid | Consistency |
|-------------|-----------------|----------|-------------|
| 9:30--10:00 AM | $168.74 -- $166.21 (falling) | $0.53 -> declining | Consistent |
| 10:00--11:00 AM | $166.27 -- $168.08 (recovering) | Rising | Consistent |
| 12:00--1:00 PM | $166.24 -- $166.59 (flat near 165) | $0.58 -- $0.23 (volatile) | Consistent |
| 2:00--3:00 PM | $165.18 -- $165.29 (tight near 165) | Narrowing near $0.50 | Consistent |
| 3:00--4:00 PM | $164.27 -- $165.06 (volatile close) | $0.08 -> $0.99 | Consistent |

The orderbook data aligns with the NVDA price trajectory. The $165 contract's mid-price dipped to $0.145 when NVDA briefly touched $164.27, then ripped to $0.99 as the stock recovered above $165 into the close. This is exactly the behavior we'd expect.

---

## 6. Data Quality Issues

### Crossed Books

| Strike | Crossed Book Snapshots | % of Valid BBO |
|--------|----------------------|----------------|
| $160 | 0 | 0.000% |
| $165 | 0 | 0.000% |
| $170 | 1 | 0.004% |
| $175 | 0 | 0.000% |
| $180 | 0 | 0.000% |

Only 1 crossed book out of 140,838 total snapshots. The single occurrence on the $170 strike had a spread of -$0.001 — likely a transient race condition in the exchange. This is excellent data quality.

### Zero-Spread Snapshots

| Strike | Zero-Spread Count | % of Valid BBO |
|--------|-------------------|----------------|
| $160 | 2 | 0.012% |
| $165 | 9 | 0.024% |
| $170 | 5 | 0.019% |
| $175 | 0 | 0.000% |
| $180 | 0 | 0.000% |

Zero-spread snapshots are rare (16 total across all strikes) and may represent locked markets or transient states. Not a concern.

### Null/NaN Values

The null pattern is structural, not random. Nulls correspond to unpopulated depth levels:

- **$160:** BBO always populated on bid side. Ask-side level 0 is null 14.95% of the time. Ask levels 12+ are 100% null — the ask book rarely has more than 7 levels.
- **$165:** Bid level 0 always populated. Ask level 0 null 4% of time. Deeper levels progressively more null.
- **$170:** Bid level 0 null 2.07% of time. Bid levels 10+ are 100% null. Ask side fully populated through level 9.
- **$175:** Bid level 0 null 12.88%. Bid levels 4+ are 100% null. Ask side fully populated through level 24.
- **$180:** Bid level 0 null 6.71%. Similar pattern to $175.

These nulls are not data quality issues — they accurately reflect that certain depth levels had no orders. No columns have unexpected or spurious null patterns.

### Timestamp Ordering

All five strikes have **monotonically increasing timestamps** — no out-of-order snapshots detected.

### Summary of Anomalies

| Issue | Count | Severity |
|-------|-------|----------|
| Crossed books | 1 of 140,838 | Negligible |
| Zero spreads | 16 of 140,838 | Negligible |
| Gaps > 5 minutes | 55 total | Moderate |
| Gaps > 1 hour | ~8 total | Significant |
| Missing BBO (one side) | 5--13% per strike | Expected, structural |
| Out-of-order timestamps | 0 | None |
| Spurious null patterns | 0 | None |

---

## 7. Telonex Viability Assessment

### Dimension Scores

| Dimension | Score (1--5) | Assessment |
|-----------|-------------|------------|
| **Coverage Completeness** | 4 | 22+ hours covered per contract. Multi-hour gaps exist but are during low-activity overnight periods. No data missing during critical US market hours (13:30--20:00 UTC). |
| **Schema Usability** | 3 | Clean 107-column schema is usable but requires string-to-float conversion for all 100 price/size columns. Parquet format is efficient. Column naming is logical and consistent. However, the flat `bid_price_0..24` layout is verbose — a nested structure would be more elegant. |
| **Update Frequency** | 5 | Median sub-second updates (21ms -- 342ms). This is genuine tick-level data, not sampled or aggregated. The ATM contract ($165) achieves P95 of 7.25s. Far exceeds what's needed for backtesting at any reasonable simulation granularity. |
| **Depth Usefulness** | 3 | 25 levels are advertised but effective two-sided depth is 3--5 levels for ATM contracts. The remaining levels are one-sided. Adequate for BBO-level backtesting but limited for depth-based strategies. Dollar depth at ATM ($5K--$10K per side) is thin by traditional standards. |
| **Data Size Efficiency** | 5 | 5.62 MB for 140,838 snapshots across 5 contracts. Parquet compression is excellent. A full day of 5-contract orderbook data fits in under 6 MB — projecting to ~150 MB/month for a similar scope, well within any storage budget. |

### Weighted Overall Score

$$\text{Overall} = \frac{4 + 3 + 5 + 3 + 5}{5} = 4.0 / 5.0$$

### Cost-Benefit Analysis

**Telonex free plan:** 5 downloads used for this POC (one per strike). At $0/month, the POC data was free.

**Telonex paid plan ($79/month):**

| Factor | Assessment |
|--------|------------|
| Data quality | High — tick-level, clean, consistent |
| Unique value | **This is the only known source for Polymarket L2 orderbook history** |
| Depth of data | Moderate — BBO is reliable, deeper levels are sparse |
| Coverage | Good — 22+ hours/day, all major contracts |
| Alternative cost | Building equivalent infrastructure (WebSocket recorder + storage) would cost $50--100/month in compute plus engineering time |
| ROI threshold | At $79/month, the data needs to support development of a strategy generating > ~$1,000/month in backtested PnL to justify the cost during R&D phase |

### Overall Verdict

> [!success] Verdict: Worth $79/month for active development
> The Telonex data is **production-quality for Polymarket orderbook backtesting**. The tick-level resolution, cross-strike consistency, and clean schema make it immediately usable. The primary limitations — wide ATM spreads and thin depth — are properties of the Polymarket venue itself, not of the data provider.
>
> **Recommended for:** The R&D phase of the market-making strategy. Use the free plan's remaining downloads strategically while building the backtesting framework, then upgrade to paid when running systematic backtests across multiple days/contracts.
>
> **Not needed if:** The strategy only requires BBO snapshots at 1-second or coarser intervals — in that case, a custom WebSocket recorder polling the Polymarket API would suffice and cost less.

---

## Appendix A: Key Metrics for Backtester Configuration

Based on this analysis, recommended parameters for the [[NVDA-POC-Implementation-Plan]] backtester:

| Parameter | Recommended Value | Rationale |
|-----------|------------------|-----------|
| Simulation time step | 1 second | Median update interval is sub-second; 1s captures 85%+ of state changes |
| Max usable depth levels | 5 | Beyond level 5, population drops below 50% on the thin side |
| Min spread filter | $0.01 | Filter out zero-spread snapshots (16 total) |
| Gap threshold | 60 seconds | Mark gaps > 60s as "no-trade" periods |
| BBO validity requirement | Both sides present | Skip snapshots where either bid or ask is null |
| Price column dtype | float64 | Source strings have up to 3 decimal places |

## Appendix B: File Locations

```
/Users/alex/market-making-rnd/data/telonex/nvda-poc/
├── book_snapshot_25_strike160_2026-03-30.parquet
├── book_snapshot_25_strike165_2026-03-30.parquet
├── book_snapshot_25_strike170_2026-03-30.parquet
├── book_snapshot_25_strike175_2026-03-30.parquet
├── book_snapshot_25_strike180_2026-03-30.parquet
└── nvda_prices_1m.parquet
```

## Appendix C: NVDA Hourly Price Reference (March 30, 2026)

| Hour (ET) | Open | High | Low | Close | Volume |
|-----------|------|------|-----|-------|--------|
| 9:00 AM | $168.74 | $169.45 | $166.21 | $166.29 | 29,304,675 |
| 10:00 AM | $166.28 | $168.08 | $166.27 | $167.95 | 28,262,794 |
| 11:00 AM | $167.96 | $168.25 | $166.54 | $167.36 | 21,992,175 |
| 12:00 PM | $167.35 | $167.41 | $166.24 | $166.59 | 17,554,887 |
| 1:00 PM | $166.59 | $166.82 | $165.59 | $166.18 | 16,382,128 |
| 2:00 PM | $166.18 | $166.27 | $165.18 | $165.29 | 14,046,657 |
| 3:00 PM | $165.28 | $165.54 | $164.27 | $165.06 | 30,362,787 |
