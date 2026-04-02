# Polymarket Stock & Index Market Making — Research Brief

## Purpose

This document provides the full context for a research initiative into **market making strategies** on Polymarket's stock and index binary event markets. A research agent will use this brief to independently plan, execute, and document a comprehensive investigation into viable strategies grounded in established financial market making theory.

**All research output** — notes, analysis, data exploration, strategy write-ups, backtest results — must be documented in the **Obsidian vault** located at `~/market-making-rnd`. Agents have access to a skill that allows them to create and modify Obsidian-compatible markdown files directly. The vault should be treated as the single source of truth for all findings.

---

## 1. Platform Context: Polymarket

### 1.1 What Is Polymarket?

Polymarket is a decentralized prediction market platform built on the Polygon blockchain. Users trade binary outcome contracts — tokens that resolve to either **$1.00** (if the event occurs) or **$0.00** (if it does not). Prices trade between $0.00 and $1.00 and are commonly interpreted as the market's implied probability of the event occurring.

### 1.2 Order Book & Trading Mechanics

- **CLOB (Central Limit Order Book):** Polymarket operates a hybrid on-chain/off-chain order book via the CTF (Conditional Token Framework). Orders are submitted off-chain to an operator and matched, with settlement occurring on-chain.
- **Two tokens per market:** Every binary market has a **YES** token and a **NO** token. YES + NO always sum to $1.00 at resolution. A trader can buy YES (bullish on the event) or buy NO (bearish). Selling YES is economically equivalent to buying NO, but the order book has separate bid/ask stacks for each token.
- **Maker/Taker model:** Limit orders that rest on the book are "maker" orders. Orders that cross the spread and execute immediately are "taker" orders. Polymarket charges **zero maker fees** and a small taker fee, making it structurally favorable for market making.
- **Resolution:** At expiry, the oracle resolves the market. Winning tokens pay out $1.00 per share; losing tokens pay $0.00. There is no partial payout — it is strictly binary.

### 1.3 Liquidity & Participant Profile

Polymarket's stock/index markets are **significantly less liquid and less efficient** than traditional options or futures markets. Participants are a mix of retail speculators with limited quantitative sophistication, a small number of quantitative traders attempting to exploit mispricings, and passive liquidity providers quoting wide spreads. This creates a structural opportunity: prices often deviate materially from fair value as implied by the options market or any rigorous probabilistic model.

---

## 2. Target Markets

### 2.1 Market Structure

We are targeting a specific category of Polymarket events with the following canonical format:

> **"Will [TICKER] close above [PRICE] on [DATE/PERIOD]?"**

Each market has two tokens:

| Token | Meaning | Payout |
|-------|---------|--------|
| **YES** | The ticker closes **above** the strike price | $1.00 if true, $0.00 if false |
| **NO** | The ticker closes **at or below** the strike price | $1.00 if true, $0.00 if false |

These are economically identical to **binary (digital) options** — a well-studied instrument in quantitative finance.

### 2.2 Resolution Variants

Markets exist across multiple time horizons:

- **Daily:** "Will NVDA close above $120 on April 2, 2025?"
- **Weekly:** "Will SPX close above 5,500 this week?" (typically Friday close)
- **Monthly:** "Will AAPL close above $200 in April 2025?" (last trading day)

The resolution source is typically the official closing price on the relevant exchange (NYSE, NASDAQ) or, for indices, the official index close (S&P Dow Jones for SPX, Nasdaq for NDX).

### 2.3 Tickers of Interest

| Ticker | Type  | Exchange/Index    |
| ------ | ----- | ----------------- |
| NFLX   | Stock | NASDAQ            |
| MSFT   | Stock | NASDAQ            |
| PLTR   | Stock | NYSE              |
| GOOGL  | Stock | NASDAQ            |
| AAPL   | Stock | NASDAQ            |
| TSLA   | Stock | NASDAQ            |
| META   | Stock | NASDAQ            |
| AMZN   | Stock | NASDAQ            |
| NVDA   | Stock | NASDAQ            |
| SPX    | Index | S&P 500 (CBOE)    |
| NDX    | Index | Nasdaq-100 (CBOE) |

**Important distinction for SPX and NDX:** These are indices, not ETFs. Options data should come from **SPX and NDX index options directly** (not SPY/QQQ ETF proxies), because index options have different exercise/settlement mechanics and are the standard for institutional probability extraction.

---

## 3. The Core Edge Thesis

### 3.1 Hypothesis

Polymarket stock/index binary event markets are **systematically mispriced** relative to the probabilities implied by listed equity and index options markets. This mispricing arises because:

1. **Polymarket participants lack access to (or don't use) options-implied probability distributions.** Most traders on the platform price events based on intuition, simple heuristics, or directional bias rather than rigorous quantitative models.
2. **Thin liquidity allows mispricings to persist.** In deep, institutional markets, arbitrageurs would immediately correct such deviations. On Polymarket, there are far fewer sophisticated participants, so mispricings can persist for hours or even days.
3. **Options markets are informationally superior.** The listed options market for major stocks and indices is one of the deepest, most liquid, and most studied markets in the world. The implied probability distributions extracted from options chains represent the aggregate beliefs of institutional traders, market makers, and systematic funds — a far more accurate pricing signal than Polymarket's thin order book.

### 3.2 Pricing Methodology: Risk-Neutral Probability Extraction

The fair value of a Polymarket YES token for "Will X close above K on date T?" is, in theory, the **risk-neutral probability** that the underlying closes above the strike K at expiry T:

```
Fair_Value(YES) = P_RN(S_T > K)
```

This probability can be extracted from the listed options market using the **Breeden-Litzenberger method**:

1. Collect the full options chain (calls and puts) for the underlying at the relevant expiry.
2. Unify calls and puts via **put-call parity** to create a single smooth implied volatility surface.
3. Fit a parametric model (**SABR or SVI** preferred; unconstrained cubic splines are fragile and not recommended) to the implied volatility smile.
4. Compute the **risk-neutral probability density function (PDF)** as the second derivative of the call price with respect to strike: `q(K) = e^{rT} * ∂²C/∂K²`
5. Integrate the PDF from the strike K to infinity to get `P_RN(S_T > K)`.

**Key pipeline decisions (already established):**

- For short-dated expiries (≤2 days), **drop the risk-free rate and dividend yield** — their impact is negligible and adds noise.
- **Put-call parity unification should be done before smoothing**, not after.
- **SABR or SVI parameterization** is strongly preferred over unconstrained cubic splines, which can produce spurious densities (negative probabilities, oscillations).
- Options-implied probabilities carry a **systematic left-tail bias** due to variance risk premium and skew risk premium. The risk-neutral distribution overstates the probability of large downside moves relative to the real-world (physical) distribution. This bias should be understood and potentially corrected for when comparing to Polymarket prices.

### 3.3 What Constitutes a Tradeable Signal

A tradeable signal exists when:

```
|Polymarket_Price(YES) - P_RN(S_T > K)| > threshold
```

Where the threshold accounts for Polymarket bid-ask spread and execution slippage, taker fees (if crossing the spread), model uncertainty / confidence interval around the extracted probability, and inventory risk and capital cost of holding the position until resolution.

---

## 4. Available Data

### 4.1 Polymarket Data

| Data Type | Granularity | Coverage | Notes |
|-----------|-------------|----------|-------|
| Midpoint price (YES & NO) | 1-minute | Full event duration | Primary signal for backtesting Polymarket-side |
| All trades | Tick-level | Full event duration | For order flow analysis, realized spread, etc. |

Use the **polymarket-docs** MCP server to research the exact API endpoints, data schemas, and query methods for retrieving this data.

### 4.2 Options Data (Thetadata)

| Data Type | Coverage | Plan | Notes |
|-----------|----------|------|-------|
| Live options chains | All OPRA-listed options | Options Standard ($80/mo) | Covers all 11 tickers including NDX (via OPRA) |
| Historical options data | Full history | Options Standard ($80/mo) | Required for backtesting the Breeden-Litzenberger pipeline |
| Greeks, IV, open interest | Per-contract | Options Standard | Useful for filtering and vol surface construction |

**Key note on NDX:** The Thetadata Options Standard plan covers NDX options data via OPRA, even though it does not provide the raw NDX index price feed. The NDX spot price can be sourced separately — what matters for the Breeden-Litzenberger pipeline is the full options chain, which is available.

Use the **thetadata-docs** MCP server to research the exact API endpoints, historical data query syntax, and available fields for options chains.

### 4.3 Data Gaps to Investigate

- **Underlying stock/index price at 1-minute granularity:** Needed to compute real-time mispricing during backtesting. Verify availability via thetadata-docs; if not included, an additional source is required.
- **Polymarket order book snapshots:** Midpoint and trades are confirmed, but full L2 order book depth is not. This matters for realistic execution simulation.
- **Polymarket fee schedule history:** Verify current and historical fee structures via polymarket-docs.

---

## 5. MCP Servers Available

| Server | Purpose | Use For |
|--------|---------|---------|
| `polymarket-docs` | Polymarket platform documentation | API endpoints, data schemas, order types, fee structure, resolution rules, CLOB mechanics, token economics |
| `thetadata-docs` | Thetadata API documentation | Options data endpoints, historical data queries, available fields, data granularity, request limits, supported tickers |

Before writing any data-fetching code or making assumptions about available data fields, **always query the relevant MCP server first** to confirm the exact API surface, request format, and response schema.

---

## 6. Key Risks & Considerations

- **Model risk:** The Breeden-Litzenberger pipeline is only as good as the options data and the smoothing method. Illiquid strikes, wide bid-ask spreads in the options market, and earnings-related vol jumps can produce unreliable probability estimates.
- **Latency risk:** Polymarket prices adjust to underlying moves with a lag. A market maker quoting stale prices during fast moves will be adversely selected.
- **Resolution risk:** Verify the exact resolution source and methodology for each market. Edge cases (e.g., trading halts, early closes) must be handled.
- **Regulatory/platform risk:** Polymarket's rules, fee structures, and market availability can change. All assumptions about platform mechanics should be documented and flagged if they could affect strategy viability.
- **Risk-neutral vs. physical probability divergence:** The options-implied probability is risk-neutral, not a real-world forecast. The gap between the two (driven by risk premiums) means that even a "correctly priced" Polymarket contract can differ from the options-implied probability. Strategies must account for this or explicitly model the risk premium adjustment.
- **Capital lockup:** Polymarket positions lock capital until resolution. This opportunity cost should be factored into strategy evaluation, especially for weekly and monthly expiries.
