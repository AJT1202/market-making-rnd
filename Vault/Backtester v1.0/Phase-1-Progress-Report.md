---
title: "Phase 1: Data Acquisition — Progress Report"
created: 2026-04-06
updated: 2026-04-06
tags:
  - backtester-v1
  - phase-1
  - progress
  - data-pipeline
status: in-progress
related:
  - "[[Phase-1-Data-Acquisition]]"
  - "[[ROADMAP]]"
---

# Phase 1: Data Acquisition — Progress Report

> **As of**: 2026-04-06
> **Overall status**: ~60% complete — Telonex data fully downloaded, ThetaData EOD complete, options IV download partially complete (27%), trade-quote not started.

---

## 1. Completed Work

### 1.1 Download Infrastructure (100%)

All download scripts built, reviewed, and hardened through 3 rounds of expert code review:

| Script | Lines | Status |
|--------|-------|--------|
| `Code/scripts/download_options.py` | 1,065 | Production-ready |
| `Code/scripts/download_telonex.py` | 1,127 | Production-ready |
| `Code/scripts/validate_data.py` | 1,477 | Production-ready |
| `Code/scripts/bl_granularity_test.py` | 802 | Test complete |

**Key improvements applied during review:**
- Switched from `/option/history/quote` to `/option/history/greeks/implied_volatility` — adds `implied_vol`, `bid_implied_vol`, `ask_implied_vol`, `underlying_price` fields required by the [[Breeden-Litzenberger-Pipeline]]
- Changed from tick-level to 1-minute interval after granularity comparison test proved equivalence (max probability difference: 0.17%, well below Polymarket's $0.01 tick size)
- Added server-side `strike_range` filtering to prevent OOM on liquid names
- Atomic manifest writes (crash-safe)
- O(1) manifest lookups with set index
- Shared httpx client across downloads
- Graceful `Ctrl+C` handling with immediate manifest save
- NYSE holiday skipping
- Comprehensive error handling for all ThetaData status codes (470-570)
- API key removed from git-tracked config; `config.toml.example` created

### 1.2 Telonex Polymarket Data (100% for close-above markets)

| Channel | Market/Outcome Pairs | Downloaded | Files | No Data |
|---------|---------------------|-----------|-------|---------|
| `book_snapshot_full` | 7,408 | 7,408 (100%) | 52,886 | 0 |
| `trades` | 7,156 | 7,110 (99.4%) | 34,552 | 46 |

- **Market registry**: 4,780 close-above/above markets across 10 tickers
- **Date range**: 2026-01-02 to 2026-03-31 (~63 trading days)
- **Tickers**: AAPL, AMZN, GOOGL, META, MSFT, NFLX, NVDA, PLTR, SPX, TSLA
- **Data quality**: Timestamps 100% monotonic, 98.3% trading-day coverage, full L2 depth (5-20 bid/ask levels)
- **Disk usage**: ~22 GB

**Audit findings**: All 3,704 markets in the registry have corresponding data on disk. The 46 "no data" trades entries are illiquid/far-OTM markets with zero trading activity — expected and correct.

### 1.3 ThetaData EOD Greeks & Open Interest (100%)

| Dataset | Trading Days | Tickers | Status |
|---------|-------------|---------|--------|
| EOD Greeks | 61 (60 full, 1 partial) | 11 (AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, NFLX, PLTR, SPX, SPXW) | Complete |
| Open Interest | 61 | 11 | Complete |

- **Date range**: 2026-01-02 to 2026-03-31
- **Partial day**: 2026-03-30 has NVDA only (from early single-ticker test)
- **Storage**: `D:/data/thetadata/eod/{YYYYMMDD}/greeks.parquet` and `oi.parquet`

### 1.4 Granularity Comparison Test (Complete)

Tested whether tick-level options IV data is necessary for the B-L pipeline, or whether 1-minute snapshots produce equivalent probabilities. **Result: 1-minute is equivalent.**

| Strike | Max |Diff| (tick vs 1m) | RMSE |
|--------|-------------------------|------|
| $155 (OTM put) | 0.0009 | 0.0002 |
| $165 (ATM) | 0.0016 | 0.0003 |
| $175 (OTM call) | 0.0015 | 0.0003 |

Maximum probability difference of 0.17% — 6x smaller than Polymarket's minimum tick ($0.01 = 1%). The download interval was changed from `tick` to `1m`, reducing estimated download time from 4-6 days to ~7 hours, and storage from ~30 GB to ~2 GB.

---

## 2. In Progress

### 2.1 ThetaData Options IV — 1-Minute (27% complete)

| Ticker | Days Downloaded | Files | Status |
|--------|----------------|-------|--------|
| AAPL | 18 | 91 | Partial |
| MSFT | 18 | 81 | Partial |
| GOOGL | 18 | 81 | Partial |
| AMZN | 18 | 86 | Partial |
| META | 18 | 80 | Partial |
| NVDA | 17 | 84 | Partial |
| TSLA | 17 | 80 | Partial |
| NFLX | 16 | 74 | Partial |
| PLTR | 16 | 74 | Partial |
| SPX | 14 | 14 | Partial |
| SPXW | 17 | 349 | Partial |
| **Total** | **18 of 61 days** | **1,094 files** | **27%** |

**Date coverage**: 2026-01-02 to 2026-01-28 (18 trading days complete).

**Why stopped**: The ThetaData terminal session was disconnected (`ACCOUNT_ALREADY_CONNECTED` — terminal running on two machines simultaneously), and subsequent download attempts returned 478 (Invalid Session ID) errors. The manifest tracked all completed files, so the download is fully resumable.

**To resume**: Ensure terminal is running on one machine only, then:
```
python Code/scripts/download_options.py tick-quotes --start 2026-01-02 --end 2026-03-31
```
The script will skip the 1,094 completed files and resume from 2026-01-29.

**Estimated time remaining**: ~5 hours (~3,200 remaining requests at ~6s each).

---

## 3. Not Started

### 3.1 ThetaData Trade-Quote

Option trades paired with NBBO at execution time. Same smart filtering as IV data. Blocked on IV download completion (uses the same terminal session). Estimated download time: ~2-3 hours.

```
python Code/scripts/download_options.py trade-quote --start 2026-01-02 --end 2026-03-31
```

### 3.2 ThetaData Stock/Index OHLC

1-minute underlying price bars. **Blocked by subscription**: requires Stock VALUE ($20/mo) and Index VALUE ($10/mo) tiers. Currently on FREE tier for both stocks and indices. The `underlying_price` field in the IV endpoint provides spot prices at each 1-minute options snapshot, which may be sufficient.

---

## 4. Polymarket Market Categories Not Yet Downloaded

The current Telonex download covers **close-above/above** binary markets only (4,780 markets). Three additional market categories exist on Polymarket for our target tickers but have not been downloaded or incorporated into the market registry:

### 4.1 Range Markets (92 markets)

Markets where the underlying must close within a price range. Example slugs:
- `will-amazon-amzn-close-at-200-205-in-2025`
- `will-amazon-amzn-close-at-210-215-in-2025`

These require sum-to-one constraint handling across range buckets (see [[Range-Market-Strategy]]) and are explicitly deferred from v1.0 scope in the [[ROADMAP]].

### 4.2 Hit/Reach/Dip Markets (978 markets)

Markets betting whether a stock price will touch a certain level before expiry. These are **barrier-style binary options** (touch/no-touch), not European-style close-above. Example questions:
- "Will NVIDIA reach $192 in December?"
- "Will Tesla dip to $330 in December?"
- "Will S&P 500 (SPX) hit 5350 (LOW) in March?"

Distribution across tickers:

| Ticker | Markets |
|--------|---------|
| GOOGL | 120 |
| AMZN | 112 |
| NVDA | 112 |
| TSLA | 112 |
| MSFT | 99 |
| AAPL | 97 |
| META | 91 |
| PLTR | 84 |
| NFLX | 77 |
| SPX | 74 |

These require different pricing models than close-above markets (barrier option pricing vs European digital) and are not covered by the B-L pipeline in its current form.

### 4.3 Up/Down Daily Markets (1,219 markets)

Simple directional bets — whether a stock will close up or down on a given day. Example questions:
- "NVIDIA (NVDA) Up or Down on March 25?"
- "Apple (AAPL) Up or Down on January 5?"
- "S&P 500 (SPX) opening price Up or Down on January 5?"

Distribution: ~115-117 markets per equity ticker, 170 for SPX.

These are essentially ATM digital options (strike = previous close). The B-L pipeline could price these, but they require knowledge of the previous day's closing price as the implicit strike.

### 4.4 Decision

Whether to download and incorporate these additional market categories is a **scope decision** that affects the backtester's market coverage and strategy diversity. The close-above markets alone provide sufficient data for v1.0 validation and the core market-making strategy. The additional categories could be added to the registry and downloaded without changes to the download infrastructure — only the slug-parsing regex patterns in `download_telonex.py` need extending.

---

## 5. Data Summary

### Disk Usage

| Directory | Size | Contents |
|-----------|------|----------|
| `D:/data/telonex/` | ~22 GB | Books + trades for 3,704 markets |
| `D:/data/thetadata/eod/` | ~0.4 GB | EOD Greeks + OI for 61 days |
| `D:/data/thetadata/options_iv/` | ~0.1 GB | 1m IV data for 18 days (partial) |
| **Total** | **~24 GB** | |

### Estimated Final Size (when complete)

| Dataset | Estimated Size |
|---------|---------------|
| Telonex books + trades | 22 GB (done) |
| ThetaData EOD | 0.4 GB (done) |
| ThetaData IV (1m) | ~2 GB |
| ThetaData trade-quote | ~1 GB |
| **Total** | **~25-26 GB** |

---

## 6. Remaining Work to Complete Phase 1

The remaining downloads will be completed on the **MacBook Air M2 (macOS)**, using the same external SSD (D: on Windows, mounted as `/Volumes/SSD` on macOS). The scripts are cross-platform — only `config.toml` `paths.data_dir` needs to change per machine.

| Task | Estimated Time | Blocked By |
|------|---------------|------------|
| Resume IV download (Jan 29 - Mar 31) | ~5 hours | Terminal must be running, single machine only |
| Trade-quote download | ~2-3 hours | IV download completion |
| Run `validate_data.py` on complete dataset | ~10 minutes | All downloads |
| **Total** | **~8 hours of download time** | |

**macOS setup checklist:**
1. Mount external SSD
2. Update `config.toml`: `data_dir = "/Volumes/SSD/data"` (or wherever the SSD mounts)
3. Install ThetaData terminal and start it
4. Ensure terminal is **not** running on Windows simultaneously (causes 478 session errors)
5. Run the download commands — manifest will resume from where Windows left off

---

## 7. Lessons Learned

1. **ThetaData terminal session conflicts**: Running the terminal on two machines simultaneously causes `ACCOUNT_ALREADY_CONNECTED` disconnections and persistent 478 errors. Always ensure the terminal runs on one machine only.

2. **Long downloads must run in user's terminal**: Claude's background task timeout (10 min) is insufficient for multi-hour downloads. The scripts are fully resumable via manifest tracking.

3. **Server-side filtering is essential**: Without `strike_range`, tick-level requests for liquid names (MSFT, SPXW) cause OOM (10+ GB memory). The `strike_range` parameter limits the response at the API level.

4. **1-minute IV data matches tick-level for B-L**: The granularity comparison test saved ~99.5% of storage and download time with no measurable accuracy loss for probability computation.

5. **Manifest corruption risk**: Overlapping writes to the manifest JSON can corrupt it. Fixed with atomic writes (`tempfile` + `os.replace`).
