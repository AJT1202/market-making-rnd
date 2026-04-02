---
title: Polymarket CLOB Mechanics
created: 2026-03-31
updated: 2026-03-31
tags:
  - polymarket
  - clob
  - order-book
  - market-making
  - fees
  - tokens
  - research
sources:
  - https://docs.polymarket.com/trading/overview
  - https://docs.polymarket.com/concepts/prices-orderbook
  - https://docs.polymarket.com/trading/orderbook
  - https://docs.polymarket.com/trading/fees
  - https://docs.polymarket.com/trading/orders/overview
  - https://docs.polymarket.com/trading/orders/create
  - https://docs.polymarket.com/trading/matching-engine
  - https://docs.polymarket.com/concepts/resolution
---

# Polymarket CLOB Mechanics

Deep dive on the Central Limit Order Book architecture, order types, token mechanics, fee structure, and resolution mechanics.

## Architecture Overview

Polymarket's CLOB is a **hybrid-decentralized** trading system:

- **Offchain order matching** -- an operator matches compatible orders for speed
- **Onchain settlement** -- matched trades settle atomically on **Polygon** (Chain ID 137) via the [CTF Exchange contract](https://github.com/Polymarket/ctf-exchange)
- **Non-custodial** -- all orders are [EIP-712](https://eips.ethereum.org/EIPS/eip-712) signed messages; the operator cannot set prices or execute unauthorized trades
- Users can cancel orders onchain independently if trust issues arise

### Key Properties

| Property | Detail |
|---|---|
| Chain | Polygon Mainnet (137) |
| Collateral | USDC.e (bridged USDC, 6 decimals) |
| Token Standard | ERC1155 (Gnosis Conditional Token Framework) |
| Settlement | Atomic onchain via Exchange contract |
| Audit | [Chainsecurity audit](https://github.com/Polymarket/ctf-exchange/blob/main/audit/ChainSecurity_Polymarket_Exchange_audit.pdf) |

---

## Token Mechanics

### Outcome Tokens (CTF)

Every market has exactly **two** ERC1155 outcome tokens:

| Token | Redeems For | Condition |
|---|---|---|
| **Yes** | $1.00 USDC.e | Event occurs |
| **No** | $1.00 USDC.e | Event does not occur |

Tokens are **always fully collateralized** -- every Yes/No pair is backed by exactly $1.00 USDC.e locked in the CTF contract.

### Core Token Operations

| Operation | Description | Use Case |
|---|---|---|
| **Split** | $1 USDC.e --> 1 Yes + 1 No | Create inventory for market making |
| **Merge** | 1 Yes + 1 No --> $1 USDC.e | Exit position without trading |
| **Redeem** | Winning token --> $1 USDC.e after resolution | Collect winnings |
| **Trade** | Buy/sell on CLOB | Normal trading |

### Token Identifier Computation

Position IDs (token IDs / asset IDs) are computed onchain in three steps:

1. **Condition ID**: `getConditionId(oracle, questionId, 2)` -- oracle is the UMA CTF Adapter
2. **Collection IDs**: `getCollectionId(bytes32(0), conditionId, indexSet)` -- indexSet `1` for first outcome, `2` for second
3. **Position IDs**: `getPositionId(USDC.e_address, collectionId)` -- the ERC1155 token IDs

In practice, token IDs are available via the Gamma API (`GET /markets` or `GET /events`) in the `tokens` array.

### Holding Rewards

Polymarket pays **4.00% annualized** holding reward based on total position value in eligible markets. Sampled randomly once per hour, distributed daily. Rate is variable.

---

## Contract Addresses

### Core Trading Contracts

| Contract | Address | Purpose |
|---|---|---|
| CTF Exchange | `0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E` | Standard market settlement |
| Neg Risk CTF Exchange | `0xC5d563A36AE78145C45a50134d48A1215220f80a` | Multi-outcome market settlement |
| Neg Risk Adapter | `0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296` | Converts No tokens between outcomes |
| Conditional Tokens (CTF) | `0x4D97DCd97eC945f40cF65F87097ACe5EA0476045` | ERC1155 token storage (split/merge/redeem) |
| USDC.e | `0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174` | Collateral token (6 decimals) |

### Resolution Contracts

| Contract | Address |
|---|---|
| UMA Adapter | `0x6A9D222616C90FcA5754cd1333cFD9b7fb6a4F74` |
| UMA Optimistic Oracle | `0xCB1822859cEF82Cd2Eb4E6276C7916e692995130` |

---

## Order Types

All orders on Polymarket are fundamentally **limit orders**. Market orders are limit orders priced to execute immediately.

| Type | Behavior | Use Case |
|---|---|---|
| **GTC** (Good-Til-Cancelled) | Rests on book until filled or cancelled | Default for passive limit orders |
| **GTD** (Good-Til-Date) | Active until specified expiration (UTC seconds timestamp) | Auto-expire before known events |
| **FOK** (Fill-Or-Kill) | Must fill entirely and immediately, or cancel | All-or-nothing market orders |
| **FAK** (Fill-And-Kill) | Fills what's available immediately, cancels rest | Partial-fill market orders |

### Key Distinctions

- **GTC/GTD** are **limit order** types -- they rest on the book at your price
- **FOK/FAK** are **market order** types -- they execute against resting liquidity immediately
  - BUY: specify dollar amount to spend
  - SELL: specify number of shares to sell

### Post-Only Orders

- Guarantees you are always the **maker**
- If the order would cross the spread (match immediately), it is **rejected**
- Only works with GTC and GTD
- Rejected if combined with FOK or FAK

### GTD Expiration Security Threshold

There is a **60-second security threshold** on GTD expiration. For an effective lifetime of N seconds, set expiration to `now + 60 + N`.

### Sports Market Special Behaviors

- Outstanding limit orders are **automatically cancelled** once the game begins
- Marketable orders have a **3-second placement delay** before matching
- Game start times can shift -- orders may not be cleared if start time changes unexpectedly

---

## Tick Sizes

Order prices must conform to the market's tick size or be rejected.

| Tick Size | Precision | Example Prices |
|---|---|---|
| `0.1` | 1 decimal | 0.1, 0.2, 0.5 |
| `0.01` | 2 decimals | 0.01, 0.50, 0.99 |
| `0.001` | 3 decimals | 0.001, 0.500, 0.999 |
| `0.0001` | 4 decimals | 0.0001, 0.5000, 0.9999 |

**Dynamic tick size changes**: When a market price reaches >0.96 or <0.04, the tick size may change. The WebSocket emits `tick_size_change` events -- critical to handle for trading bots.

Query tick size:
```bash
GET https://clob.polymarket.com/tick-size?token_id={token_id}
```

Also available on the market object as `minimum_tick_size`.

---

## Minimum Order Size

The orderbook response includes a `min_order_size` field. This varies by market. Orders below this size are rejected with `INVALID_ORDER_MIN_SIZE`.

---

## Negative Risk (Multi-Outcome) Markets

Events with 3+ outcomes (e.g., "Who will win the election?") use the **Neg Risk CTF Exchange**. Key differences:

| Feature | Standard Markets | Neg Risk Markets |
|---|---|---|
| Exchange Contract | CTF Exchange | Neg Risk CTF Exchange |
| `negRisk` flag | `false` | `true` |
| Multi-outcome | Independent markets | Linked via conversion |

**Conversion operation**: A No share in any outcome can be converted into 1 Yes share in every other outcome via the Neg Risk Adapter.

When placing orders, pass `negRisk: true` in order options. The Rust SDK auto-detects this.

### Augmented Negative Risk

For events where new outcomes emerge after launch:
- **Named outcomes**: Known at creation
- **Placeholder outcomes**: Reserved slots clarified later
- **Explicit "Other"**: Catches any unnamed outcome
- `enableNegRisk: true` AND `negRiskAugmented: true` on the event object

---

## Order Lifecycle

```
Create & Sign (EIP-712) --> Submit to CLOB --> Validate --> Match or Rest --> Onchain Settlement --> Confirmation
```

### Order Statuses (on placement)

| Status | Description |
|---|---|
| `live` | Resting on the book |
| `matched` | Matched immediately with resting order |
| `delayed` | Marketable but subject to 3-second delay (sports) |
| `unmatched` | Marketable but failed to delay -- placement still successful |

### Trade Statuses (after matching)

| Status | Terminal? | Description |
|---|---|---|
| `MATCHED` | No | Matched, sent to executor for onchain submission |
| `MINED` | No | Mined on chain, no finality yet |
| `CONFIRMED` | Yes | Achieved finality -- trade successful |
| `RETRYING` | No | Transaction failed (revert/reorg), being retried |
| `FAILED` | Yes | Failed permanently |

### Maker vs Taker

| Role | Description |
|---|---|
| **Maker** | Adds liquidity to the book (order rests, later matched) |
| **Taker** | Removes liquidity (order matches immediately) |

**Price improvement** always benefits the taker. Buy at $0.55 matching a resting sell at $0.52 = you pay $0.52.

---

## Order Validity & Balance Checks

Orders are continuously monitored for validity:
- Underlying balances
- Allowances
- Onchain cancellations

Maximum order size:

$$
\text{maxOrderSize} = \text{balance} - \sum(\text{openOrderSize} - \text{filledAmount})
$$

> Any maker caught intentionally abusing these checks will be blacklisted.

### Required Approvals Before Trading

| Token | Spender | Purpose |
|---|---|---|
| USDC.e | CTF Contract | Split USDC.e into outcome tokens |
| CTF (outcome tokens) | CTF Exchange | Trade outcome tokens |
| CTF (outcome tokens) | Neg Risk CTF Exchange | Trade neg-risk market tokens |

---

## Heartbeat Mechanism

The heartbeat endpoint maintains session liveness. If a valid heartbeat is not received within **10 seconds** (with a 5-second buffer), **all open orders are cancelled**.

```
POST https://clob.polymarket.com/heartbeat
```

- Include the most recent `heartbeat_id` in each request (empty string for first)
- Send every ~5 seconds
- If you send an expired ID, server responds with 400 and the correct ID

---

## Batch Orders

Place up to **15 orders** in a single request via `POST /orders`.

---

## Fee Structure

See [[Polymarket-Trading-API]] for fee handling in the API. This section covers the fee economics.

### Fee-Free Markets

The **vast majority** of markets have zero trading fees:
- No fees to deposit or withdraw USDC
- No fees to trade shares

### Markets With Fees

The following market types charge a **taker fee** (makers pay zero):

- **All crypto markets** (15-min, 5-min, 1H, 4H, Daily, Weekly)
- **NCAAB (college basketball) markets**
- **Serie A markets**

Fees apply only to markets deployed on or after the activation date. Check: `feesEnabled: true` on the market object.

### Fee Formula

```
fee = C * p * feeRate * (p * (1 - p))^exponent
```

Where:
- `C` = number of shares traded
- `p` = price of the shares

| Parameter | Sports (NCAAB, Serie A) | Crypto |
|---|---|---|
| Fee Rate | 0.0175 | 0.25 |
| Exponent | 1 | 2 |

### Effective Fee Rates (Crypto Markets, per 100 shares)

| Price | Fee (USDC) | Effective Rate |
|---|---|---|
| $0.10 | $0.02 | 0.20% |
| $0.20 | $0.13 | 0.64% |
| $0.30 | $0.33 | 1.10% |
| $0.40 | $0.58 | 1.44% |
| **$0.50** | **$0.78** | **1.56%** (max) |
| $0.60 | $0.86 | 1.44% |
| $0.70 | $0.77 | 1.10% |
| $0.80 | $0.51 | 0.64% |
| $0.90 | $0.18 | 0.20% |

### Effective Fee Rates (Sports Markets, per 100 shares)

| Price | Fee (USDC) | Effective Rate |
|---|---|---|
| $0.10 | $0.02 | 0.16% |
| $0.30 | $0.11 | 0.37% |
| **$0.50** | **$0.22** | **0.44%** (max) |
| $0.70 | $0.26 | 0.37% |
| $0.90 | $0.14 | 0.16% |

### Fee Collection

- Fees are calculated in USDC
- Collected in **shares** on buy orders, **USDC** on sell orders
- Rounded to 4 decimal places (minimum fee: 0.0001 USDC)
- Very small trades near extremes may incur zero fee

### Fee Handling in Code

The SDK auto-fetches and includes `feeRateBps` in the signed order. For REST API users:

1. Fetch fee rate: `GET https://clob.polymarket.com/fee-rate?token_id={token_id}`
2. Include `feeRateBps` in order object **before signing**
3. Never hardcode -- fee rates vary by market type and may change

---

## Maker Rebates Program

Taker fees are redistributed to market makers as daily USDC rebates.

| Market Type | Maker Rebate % | Period |
|---|---|---|
| 15-Min Crypto | 20% | Jan 19, 2026+ |
| 5-Min Crypto | 20% | Feb 12, 2026+ |
| Sports (NCAAB, Serie A) | 25% | Feb 18, 2026+ |
| 1H, 4H, Daily, Weekly Crypto | 20% | Mar 6, 2026+ |

Rebates are **fee-curve weighted**: proportional to the fee value your filled liquidity generates. Calculated per market.

```
rebate = (your_fee_equivalent / total_fee_equivalent) * rebate_pool
```

### Liquidity Rewards Program

Separate from maker rebates. Rewards passive, balanced quoting tight to midpoint. Formula inspired by dYdX:

- Quadratic scoring: `S(v,s) = ((v-s)/v)^2 * b`
- Rewards two-sided depth (single-sided scores at reduced rate, 1/c where c=3.0)
- Sampled every minute, 10,080 samples per epoch
- Minimum payout: $1
- Market-configurable `max_incentive_spread` and `min_incentive_size`

---

## Resolution Mechanics

### UMA Optimistic Oracle

Polymarket uses the **UMA Optimistic Oracle** for decentralized resolution.

### Resolution Rules

Every market defines:
- **Resolution source** -- where the outcome is determined (e.g., official announcements, specific websites)
- **End date** -- when eligible for resolution
- **Edge cases** -- how ambiguous situations are handled

> The market title describes the question, but the **rules** define how it resolves.

### Resolution Flow

1. **Proposal**: Anyone proposes a resolution by selecting the winning outcome and posting a bond (typically $750 USDC.e)
2. **Challenge Period**: 2-hour window where anyone can dispute
3. **If undisputed**: Proposal accepted, market resolves (~2 hours total)
4. **If disputed**: Counter-bond posted, new proposal round begins
5. **If disputed twice**: Escalates to UMA DVM (Data Verification Mechanism) token holder vote

### Resolution Timeline

| Phase | Duration |
|---|---|
| Challenge period | 2 hours |
| Debate period (if disputed) | 24-48 hours |
| UMA voting (if disputed) | ~48 hours |

**Undisputed**: ~2 hours after proposal
**Disputed**: 4-6 days total

### After Resolution

- Trading stops
- Winning tokens redeemable for $1.00 each
- Losing tokens become worthless ($0.00)
- Call `redeemPositions` on CTF contract to exchange winning tokens for USDC.e
- No deadline to redeem

### Edge Cases

- **Unknown/50-50**: Neither outcome applicable (rare) -- each token redeems for $0.50
- **Clarifications**: Polymarket may issue "Additional context" updates for unforeseen circumstances; published onchain via bulletin board contract

### For Stock/Index/Crypto Binary Markets

Resolution source is typically specified in the market rules. For markets like "Will NVDA close above $240":
- The "official closing price" is defined per the resolution source (usually a specific exchange or data provider specified in the market description)
- The resolution source URL is included in the market metadata (`resolutionSource` field)
- Each market's resolution criteria are unique -- always read the specific rules

---

## Matching Engine

### Restart Schedule

- **Weekly on Tuesdays at 7:00 AM ET**
- Typical duration: ~90 seconds
- During restart: API returns **HTTP 425** (Too Early) on all order-related endpoints
- Requests are throttled, not immediately rejected

### Handling Downtime

- Retry with exponential backoff starting at 1-2 seconds
- Announcements via [Telegram](https://t.me/polytradingapis) and [Discord #trading-apis](https://discord.com/channels/710897173927297116/1473553279421255803)
- ~2 days advance notice when possible

---

## Onchain Order Events

When a trade settles onchain, the Exchange contract emits `OrderFilled`:

| Field | Description |
|---|---|
| `orderHash` | Unique hash for the filled order |
| `maker` | Source of funds |
| `taker` | Filler (or Exchange contract for multi-match) |
| `makerAssetId` | Asset given out (0 = BUY order giving USDC.e) |
| `takerAssetId` | Asset received (0 = SELL order receiving USDC.e) |
| `makerAmountFilled` | Amount given out |
| `takerAmountFilled` | Amount received |
| `fee` | Fees paid by order maker |

---

## Onchain Data (Subgraph)

Polymarket provides GraphQL subgraphs via Goldsky for indexed onchain data:

| Subgraph | Description |
|---|---|
| Positions | User token balances |
| Orders | Order book and trade events |
| Activity | Splits, merges, redemptions |
| Open Interest | Per-market and global OI |
| PNL | User position P&L |

See [[Polymarket-Data-API]] for subgraph endpoints and query examples.
