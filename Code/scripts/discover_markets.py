"""
Market discovery for Polymarket stock/index binary event markets.

Loads the Telonex free markets dataset, classifies events by type and recurrence,
enriches with Gamma API series metadata, and produces a complete inventory JSON.

Usage:
    python scripts/discover_markets.py                    # Full discovery
    python scripts/discover_markets.py --skip-gamma       # Telonex-only (no API calls)
    python scripts/discover_markets.py --ticker NVDA META # Subset of tickers
"""

import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# ─── Configuration ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TELONEX_MARKETS = PROJECT_ROOT / "Telonex testing" / "datasets" / "polymarket_markets.parquet"
GAMMA_CACHE_DIR = PROJECT_ROOT / "data" / "discovery" / "gamma_cache"
OUTPUT_DIR = PROJECT_ROOT / "data" / "discovery"

GAMMA_API = "https://gamma-api.polymarket.com"
GAMMA_RATE_LIMIT_DELAY = 0.25  # seconds between API calls

ALL_TICKERS = {
    # ticker -> (slug_prefix, type)
    "NFLX":  ("nflx",  "stock"),
    "MSFT":  ("msft",  "stock"),
    "PLTR":  ("pltr",  "stock"),
    "GOOGL": ("googl", "stock"),
    "AAPL":  ("aapl",  "stock"),
    "TSLA":  ("tsla",  "stock"),
    "META":  ("meta",  "stock"),
    "AMZN":  ("amzn",  "stock"),
    "NVDA":  ("nvda",  "stock"),
    "SPX":   ("spx",   "index"),
    "NDX":   ("ndx",   "index"),
}

# Slug patterns for event classification.
# Order matters: first match wins. Patterns are applied to event_slug.
# Each entry: (category, recurrence_hint, regex_pattern)
# recurrence_hint is used when Gamma API is skipped; Gamma overrides it.
STOCK_PATTERNS = [
    ("close_above", "daily",   r"^{pfx}-close-above-on-"),
    ("close_above", "weekly",  r"^{pfx}-above-on-"),
    ("close_above", "monthly", r"^{pfx}-above-in-"),
    ("range",       "weekly",  r"^{pfx}-week-"),
    ("range",       "monthly", r"^(?:what-price-will-{pfx}-hit|will-{pfx}-reach|will-{pfx}-dip)"),
    ("up_or_down",  "daily",   r"^{pfx}-up-or-down-on-"),
]

# Index tickers have messier slug patterns
INDEX_EXTRA_PATTERNS = {
    "SPX": [
        ("close_above", "weekly",  r"^spx-above-on-"),
        ("close_above", "monthly", r"^sp(?:x|-500-spx)-above-(?:end-of|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"),
        ("close_above", "monthly", r"^spx-close-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"),
        ("range",       "monthly", r"^what-will-sp(?:x|-500-spx)-(?:hit|close-at)-"),
        ("range",       "monthly", r"^spx-hit-"),
        ("up_or_down",  "daily",   r"^spx-(?:up-or-down|opens-up-or-down)-on-"),
    ],
    "NDX": [
        ("close_above", "weekly",  r"^ndx-above-on-"),
        ("close_above", "monthly", r"^ndx-above-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"),
        ("close_above", "monthly", r"^ndx-close-(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"),
        ("range",       "monthly", r"^what-will-ndx-hit-"),
        ("range",       "monthly", r"^ndx-hit-"),
        ("up_or_down",  "daily",   r"^ndx-up-or-down-on-"),
    ],
}


# ─── Telonex Loading & Classification ─────────────────────────────────────────

def load_telonex_markets() -> pd.DataFrame:
    """Load the free Telonex markets dataset, filtered to Polymarket."""
    print(f"Loading {TELONEX_MARKETS.name} ...")
    df = pd.read_parquet(TELONEX_MARKETS)
    df = df[df["exchange"] == "polymarket"].copy()
    print(f"  {len(df):,} Polymarket markets loaded")
    return df


def classify_event(event_slug: str, ticker: str, slug_prefix: str, ticker_type: str) -> Optional[tuple]:
    """
    Classify an event_slug into (category, recurrence_hint).
    Returns None if the event doesn't match any known pattern.
    """
    # Use index-specific patterns if available, otherwise stock patterns
    if ticker in INDEX_EXTRA_PATTERNS:
        patterns = INDEX_EXTRA_PATTERNS[ticker]
    else:
        patterns = [(cat, rec, pat.format(pfx=slug_prefix)) for cat, rec, pat in STOCK_PATTERNS]

    for category, recurrence, pattern in patterns:
        if re.search(pattern, event_slug):
            return (category, recurrence)
    return None


def filter_and_classify(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Filter markets to target tickers and classify by event type."""
    results = []

    for ticker in tickers:
        slug_prefix, ticker_type = ALL_TICKERS[ticker]["slug_prefix"], ALL_TICKERS[ticker]["type"]

        # Broad filter: event_slug contains the ticker prefix
        mask = df["event_slug"].str.contains(slug_prefix, case=False, na=False)
        ticker_df = df[mask].copy()

        for event_slug in ticker_df["event_slug"].unique():
            classification = classify_event(event_slug, ticker, slug_prefix, ticker_type)
            if classification is None:
                continue
            category, recurrence_hint = classification

            event_markets = ticker_df[ticker_df["event_slug"] == event_slug]
            for _, row in event_markets.iterrows():
                results.append({
                    "ticker": ticker,
                    "ticker_type": ticker_type,
                    "category": category,
                    "recurrence_hint": recurrence_hint,
                    "event_slug": event_slug,
                    "market_slug": row["slug"],
                    "question": row["question"],
                    "asset_id_yes": row["asset_id_0"],
                    "asset_id_no": row["asset_id_1"],
                    "status": row["status"],
                    "result_id": row["result_id"],
                    "start_date_us": row["start_date_us"],
                    "end_date_us": row["end_date_us"],
                    "created_at_us": row["created_at_us"],
                    "book_snapshot_full_from": row["book_snapshot_full_from"],
                    "book_snapshot_full_to": row["book_snapshot_full_to"],
                    "trades_from": row["trades_from"],
                    "trades_to": row["trades_to"],
                })

    result_df = pd.DataFrame(results)
    print(f"  Classified {len(result_df):,} markets across {result_df['event_slug'].nunique()} events")
    return result_df


# ─── Gamma API Enrichment ─────────────────────────────────────────────────────

def gamma_fetch_event(event_slug: str) -> Optional[dict]:
    """
    Fetch event metadata from Gamma API with disk caching.
    Returns the full event JSON or None on failure.
    """
    GAMMA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = GAMMA_CACHE_DIR / f"{event_slug}.json"

    if cache_file.exists():
        return json.loads(cache_file.read_text())

    url = f"{GAMMA_API}/events/slug/{event_slug}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            cache_file.write_text(json.dumps(data, indent=2))
            return data
        else:
            print(f"    WARN: Gamma {resp.status_code} for {event_slug}")
            return None
    except requests.RequestException as e:
        print(f"    WARN: Gamma request failed for {event_slug}: {e}")
        return None


def enrich_with_gamma(classified_df: pd.DataFrame) -> pd.DataFrame:
    """
    For each unique event, fetch Gamma API to get:
    - series slug and recurrence (authoritative)
    - market-level clobTokenIds
    - event startDate/endDate from Gamma
    """
    event_slugs = classified_df["event_slug"].unique()
    print(f"\nEnriching {len(event_slugs)} events via Gamma API ...")

    gamma_data = {}  # event_slug -> gamma response
    cached = 0
    fetched = 0

    for i, slug in enumerate(event_slugs):
        cache_file = GAMMA_CACHE_DIR / f"{slug}.json"
        if cache_file.exists():
            cached += 1
        else:
            fetched += 1
            if fetched > 1:
                time.sleep(GAMMA_RATE_LIMIT_DELAY)

        data = gamma_fetch_event(slug)
        if data:
            gamma_data[slug] = data

        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(event_slugs)} ({cached} cached, {fetched} fetched)")

    print(f"  Done: {cached} cached, {fetched} fetched, {len(event_slugs) - len(gamma_data)} failed")

    # Extract series info and market-level details
    series_map = {}      # event_slug -> {series_slug, recurrence, series_id}
    market_tokens = {}   # market_slug -> {clob_token_id_yes, clob_token_id_no}
    event_gamma = {}     # event_slug -> {title, startDate, endDate, negRisk}

    for event_slug, data in gamma_data.items():
        # Series
        series_list = data.get("series", [])
        if series_list:
            s = series_list[0]
            series_map[event_slug] = {
                "series_id": s.get("id"),
                "series_slug": s.get("slug"),
                "recurrence": s.get("recurrence"),
                "series_title": s.get("title"),
            }

        # Event-level
        event_gamma[event_slug] = {
            "gamma_title": data.get("title"),
            "gamma_start_date": data.get("startDate"),
            "gamma_end_date": data.get("endDate"),
            "neg_risk": data.get("negRisk", False),
        }

        # Market tokens
        for mkt in data.get("markets", []):
            mslug = mkt.get("slug")
            tokens = mkt.get("clobTokenIds", [])
            if mslug and len(tokens) >= 2:
                market_tokens[mslug] = {
                    "clob_token_id_yes": tokens[0],
                    "clob_token_id_no": tokens[1],
                    "gamma_group_item_threshold": mkt.get("groupItemThreshold"),
                    "gamma_group_item_title": mkt.get("groupItemTitle"),
                }

    # Merge into classified_df
    enriched = classified_df.copy()

    # Series info (join on event_slug)
    series_df = pd.DataFrame.from_dict(series_map, orient="index")
    if not series_df.empty:
        series_df.index.name = "event_slug"
        series_df = series_df.reset_index()
        enriched = enriched.merge(series_df, on="event_slug", how="left")
        # Override recurrence_hint with authoritative Gamma recurrence
        has_gamma = enriched["recurrence"].notna()
        enriched.loc[has_gamma, "recurrence_hint"] = enriched.loc[has_gamma, "recurrence"]
    else:
        enriched["series_id"] = None
        enriched["series_slug"] = None
        enriched["recurrence"] = None
        enriched["series_title"] = None

    # Event-level gamma data
    event_df = pd.DataFrame.from_dict(event_gamma, orient="index")
    if not event_df.empty:
        event_df.index.name = "event_slug"
        event_df = event_df.reset_index()
        enriched = enriched.merge(event_df, on="event_slug", how="left")

    # Market tokens (join on market_slug)
    tokens_df = pd.DataFrame.from_dict(market_tokens, orient="index")
    if not tokens_df.empty:
        tokens_df.index.name = "market_slug"
        tokens_df = tokens_df.reset_index()
        enriched = enriched.merge(tokens_df, on="market_slug", how="left")

    return enriched


# ─── Output ───────────────────────────────────────────────────────────────────

def build_inventory(enriched_df: pd.DataFrame) -> dict:
    """
    Build the structured inventory JSON from the enriched DataFrame.
    Organized: ticker -> category -> recurrence -> events[] -> markets[]
    """
    inventory = {"generated_at": datetime.now(timezone.utc).isoformat(), "tickers": {}}

    for ticker in sorted(enriched_df["ticker"].unique()):
        ticker_data = enriched_df[enriched_df["ticker"] == ticker]
        ticker_entry = {"type": ALL_TICKERS[ticker]["type"], "categories": {}}

        for category in sorted(ticker_data["category"].unique()):
            cat_data = ticker_data[ticker_data["category"] == category]
            cat_entry = {}

            for recurrence in sorted(cat_data["recurrence_hint"].unique()):
                rec_data = cat_data[cat_data["recurrence_hint"] == recurrence]
                events = []

                for event_slug in sorted(rec_data["event_slug"].unique()):
                    event_markets = rec_data[rec_data["event_slug"] == event_slug]
                    first = event_markets.iloc[0]

                    # Parse dates
                    start_us = int(first["start_date_us"]) if first["start_date_us"] else 0
                    end_us = int(first["end_date_us"]) if first["end_date_us"] else 0

                    markets = []
                    for _, mkt in event_markets.iterrows():
                        m = {
                            "market_slug": mkt["market_slug"],
                            "question": mkt["question"],
                            "asset_id_yes": mkt["asset_id_yes"],
                            "asset_id_no": mkt["asset_id_no"],
                            "status": mkt["status"],
                            "result_id": mkt.get("result_id"),
                            "data_availability": {
                                "book_snapshot_full": {
                                    "from": mkt.get("book_snapshot_full_from"),
                                    "to": mkt.get("book_snapshot_full_to"),
                                },
                                "trades": {
                                    "from": mkt.get("trades_from"),
                                    "to": mkt.get("trades_to"),
                                },
                            },
                        }
                        # Add Gamma enrichment if available
                        if pd.notna(mkt.get("clob_token_id_yes")):
                            m["clob_token_id_yes"] = mkt["clob_token_id_yes"]
                            m["clob_token_id_no"] = mkt["clob_token_id_no"]
                        if pd.notna(mkt.get("gamma_group_item_threshold")):
                            m["group_item_threshold"] = mkt["gamma_group_item_threshold"]
                        if pd.notna(mkt.get("gamma_group_item_title")):
                            m["group_item_title"] = mkt["gamma_group_item_title"]
                        markets.append(m)

                    event_entry = {
                        "event_slug": event_slug,
                        "start_date_us": start_us,
                        "end_date_us": end_us,
                        "num_markets": len(markets),
                        "markets": markets,
                    }
                    # Gamma enrichment
                    if pd.notna(first.get("series_slug")):
                        event_entry["series_slug"] = first["series_slug"]
                        event_entry["series_id"] = first["series_id"]
                    if pd.notna(first.get("gamma_title")):
                        event_entry["gamma_title"] = first["gamma_title"]
                    if pd.notna(first.get("gamma_start_date")):
                        event_entry["gamma_start_date"] = first["gamma_start_date"]
                        event_entry["gamma_end_date"] = first["gamma_end_date"]
                    if first.get("neg_risk"):
                        event_entry["neg_risk"] = True

                    events.append(event_entry)

                cat_entry[recurrence] = {
                    "num_events": len(events),
                    "num_markets": sum(e["num_markets"] for e in events),
                    "events": events,
                }

            ticker_entry["categories"][category] = cat_entry
        inventory["tickers"][ticker] = ticker_entry

    return inventory


def print_summary(inventory: dict):
    """Print a summary table of the inventory."""
    print("\n" + "=" * 90)
    print(f"{'Ticker':<8} {'Category':<15} {'Recurrence':<12} {'Events':>7} {'Markets':>8} {'DL Est':>10}")
    print("-" * 90)

    total_events = 0
    total_markets = 0
    total_downloads = 0

    for ticker, tdata in sorted(inventory["tickers"].items()):
        for category, cdata in sorted(tdata["categories"].items()):
            for recurrence, rdata in sorted(cdata.items()):
                n_events = rdata["num_events"]
                n_markets = rdata["num_markets"]
                # Download estimate: markets * 2 outcomes * 2 channels (bsf + trades)
                # For multi-day events, multiply by avg trading days
                if recurrence == "daily":
                    days_factor = 1
                elif recurrence == "weekly":
                    days_factor = 5
                else:
                    days_factor = 22
                dl_est = n_markets * 4 * days_factor
                total_events += n_events
                total_markets += n_markets
                total_downloads += dl_est
                print(f"{ticker:<8} {category:<15} {recurrence:<12} {n_events:>7} {n_markets:>8} {dl_est:>10,}")

    print("-" * 90)
    print(f"{'TOTAL':<37} {total_events:>7} {total_markets:>8} {total_downloads:>10,}")
    print(f"\nDownload estimate assumes full history for each market.")
    print(f"Actual downloads depend on Telonex data availability dates.")
    print("=" * 90)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Discover Polymarket stock/index event markets")
    parser.add_argument("--skip-gamma", action="store_true", help="Skip Gamma API enrichment")
    parser.add_argument("--ticker", nargs="+", help="Subset of tickers to discover")
    args = parser.parse_args()

    tickers = args.ticker if args.ticker else list(ALL_TICKERS.keys())
    for t in tickers:
        if t not in ALL_TICKERS:
            print(f"Unknown ticker: {t}. Available: {list(ALL_TICKERS.keys())}")
            return

    # Step 1: Load Telonex
    df = load_telonex_markets()

    # Step 2: Classify
    print("\nClassifying events ...")
    # Fix: pass ticker info properly
    classified = filter_and_classify_v2(df, tickers)

    # Step 3: Gamma enrichment
    if not args.skip_gamma:
        classified = enrich_with_gamma(classified)
    else:
        print("\nSkipping Gamma API enrichment (--skip-gamma)")

    # Step 4: Build inventory
    print("\nBuilding inventory ...")
    inventory = build_inventory(classified)

    # Step 5: Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "market_inventory.json"
    with open(output_path, "w") as f:
        json.dump(inventory, f, indent=2, default=str)
    print(f"\nInventory saved to {output_path}")

    # Also save the classified DataFrame as parquet for further analysis
    parquet_path = OUTPUT_DIR / "classified_markets.parquet"
    classified.to_parquet(parquet_path, index=False)
    print(f"Classified data saved to {parquet_path}")

    # Summary
    print_summary(inventory)


def filter_and_classify_v2(df: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Filter markets to target tickers and classify by event type."""
    results = []

    for ticker in tickers:
        slug_prefix = ALL_TICKERS[ticker]["slug_prefix"]
        ticker_type = ALL_TICKERS[ticker]["type"]

        # Broad filter: event_slug contains the ticker prefix
        mask = df["event_slug"].str.contains(slug_prefix, case=False, na=False)
        ticker_df = df[mask].copy()

        classified_count = 0
        skipped_slugs = set()

        for event_slug in ticker_df["event_slug"].unique():
            classification = classify_event(event_slug, ticker, slug_prefix, ticker_type)
            if classification is None:
                skipped_slugs.add(event_slug)
                continue
            category, recurrence_hint = classification
            classified_count += 1

            event_markets = ticker_df[ticker_df["event_slug"] == event_slug]
            for _, row in event_markets.iterrows():
                results.append({
                    "ticker": ticker,
                    "ticker_type": ticker_type,
                    "category": category,
                    "recurrence_hint": recurrence_hint,
                    "event_slug": event_slug,
                    "market_slug": row["slug"],
                    "question": row["question"],
                    "asset_id_yes": row["asset_id_0"],
                    "asset_id_no": row["asset_id_1"],
                    "status": row["status"],
                    "result_id": row["result_id"],
                    "start_date_us": row["start_date_us"],
                    "end_date_us": row["end_date_us"],
                    "created_at_us": row["created_at_us"],
                    "book_snapshot_full_from": row["book_snapshot_full_from"],
                    "book_snapshot_full_to": row["book_snapshot_full_to"],
                    "trades_from": row["trades_from"],
                    "trades_to": row["trades_to"],
                })

        if skipped_slugs:
            print(f"  {ticker}: {classified_count} events classified, {len(skipped_slugs)} skipped")
            for s in sorted(skipped_slugs)[:5]:
                print(f"    skipped: {s}")
            if len(skipped_slugs) > 5:
                print(f"    ... and {len(skipped_slugs) - 5} more")
        else:
            print(f"  {ticker}: {classified_count} events classified")

    result_df = pd.DataFrame(results)
    print(f"\n  Total: {len(result_df):,} markets across {result_df['event_slug'].nunique()} events")
    return result_df


# Fix ALL_TICKERS to be dicts with both fields
ALL_TICKERS = {
    "NFLX":  {"slug_prefix": "nflx",  "type": "stock"},
    "MSFT":  {"slug_prefix": "msft",  "type": "stock"},
    "PLTR":  {"slug_prefix": "pltr",  "type": "stock"},
    "GOOGL": {"slug_prefix": "googl", "type": "stock"},
    "AAPL":  {"slug_prefix": "aapl",  "type": "stock"},
    "TSLA":  {"slug_prefix": "tsla",  "type": "stock"},
    "META":  {"slug_prefix": "meta",  "type": "stock"},
    "AMZN":  {"slug_prefix": "amzn",  "type": "stock"},
    "NVDA":  {"slug_prefix": "nvda",  "type": "stock"},
    "SPX":   {"slug_prefix": "spx",   "type": "index"},
    "NDX":   {"slug_prefix": "ndx",   "type": "index"},
}


if __name__ == "__main__":
    main()
