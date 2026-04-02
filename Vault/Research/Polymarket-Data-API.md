---
title: Polymarket Data API
created: 2026-03-31
updated: 2026-03-31
tags:
  - polymarket
  - api
  - market-data
  - websocket
  - rest
  - research
sources:
  - https://docs.polymarket.com/api-reference/introduction
  - https://docs.polymarket.com/market-data/overview
  - https://docs.polymarket.com/market-data/fetching-markets
  - https://docs.polymarket.com/trading/orderbook
  - https://docs.polymarket.com/market-data/websocket/overview
  - https://docs.polymarket.com/market-data/websocket/market-channel
  - https://docs.polymarket.com/market-data/websocket/user-channel
  - https://docs.polymarket.com/market-data/subgraph
---

# Polymarket Data API

All available data endpoints, schemas, query methods, WebSocket streams, and subgraph access for market data on Polymarket.

## API Architecture

Polymarket data is served by **three separate REST APIs** plus **WebSocket channels** and **GraphQL subgraphs**.

| API | Base URL | Auth Required | Purpose |
|---|---|---|---|
| **Gamma API** | `https://gamma-api.polymarket.com` | No | Markets, events, tags, series, search, profiles |
| **Data API** | `https://data-api.polymarket.com` | No | Positions, trades, activity, holders, OI, leaderboards |
| **CLOB API** | `https://clob.polymarket.com` | No (read) / Yes (trade) | Orderbook, prices, midpoints, spreads, price history |
| **Bridge API** | `https://bridge.polymarket.com` | Varies | Deposits and withdrawals (proxy of fun.xyz) |

All market data endpoints are **public** -- no API key, authentication, or wallet required.

---

## REST API: Orderbook & Pricing (CLOB API)

Base URL: `https://clob.polymarket.com`

### Get Order Book (L2 Depth)

```bash
GET /book?token_id={token_id}
```

**Response schema** (`OrderBookSummary`):

```json
{
  "market": "0xbd31dc8a...",       // Condition ID
  "asset_id": "52114319501245...", // Token ID
  "timestamp": "1234567890",
  "bids": [
    { "price": "0.48", "size": "1000" },
    { "price": "0.47", "size": "2500" }
  ],
  "asks": [
    { "price": "0.52", "size": "800" },
    { "price": "0.53", "size": "1500" }
  ],
  "min_order_size": "5",
  "tick_size": "0.01",
  "neg_risk": false,
  "last_trade_price": "0.45",
  "hash": "0xabc123..."
}
```

| Field | Type | Description |
|---|---|---|
| `market` | string | Condition ID of the market |
| `asset_id` | string | Token ID |
| `bids` | array | Buy orders sorted by price descending (highest first) |
| `asks` | array | Sell orders sorted by price ascending (lowest first) |
| `tick_size` | string | Minimum price increment for this market |
| `min_order_size` | string | Minimum order size |
| `neg_risk` | boolean | Whether multi-outcome (neg risk) market |
| `hash` | string | Hash of orderbook state -- use to detect changes |
| `last_trade_price` | string | Most recent trade price |

### Batch Order Books

```bash
POST /books
Content-Type: application/json

[
  { "token_id": "TOKEN_A" },
  { "token_id": "TOKEN_B", "side": "BUY" }
]
```

Up to **500 tokens** per batch request. Optional `side` parameter to filter by bid or ask.

### Get Price (Best Bid/Ask)

```bash
# Best ask (price to buy)
GET /price?token_id={token_id}&side=BUY

# Best bid (price to sell)
GET /price?token_id={token_id}&side=SELL
```

**Response**:
```json
{ "price": "0.52" }
```

### Batch Prices

```bash
POST /prices
Content-Type: application/json

[
  { "token_id": "TOKEN_A", "side": "BUY" },
  { "token_id": "TOKEN_B", "side": "SELL" }
]
```

**Response**:
```json
{
  "TOKEN_A": { "BUY": "0.52" },
  "TOKEN_B": { "SELL": "0.74" }
}
```

### Get Midpoint Price

The midpoint is the average of best bid and best ask. This is the price displayed on Polymarket as the market's implied probability.

```bash
GET /midpoint?token_id={token_id}
```

**Response**:
```json
{ "mid_price": "0.45" }
```

> If the bid-ask spread is wider than $0.10, Polymarket displays the last traded price instead of the midpoint.

### Batch Midpoints

```bash
POST /midpoints
Content-Type: application/json

[{ "token_id": "TOKEN_A" }, { "token_id": "TOKEN_B" }]
```

### Get Spread

```bash
GET /spread?token_id={token_id}
```

**Response**:
```json
{ "spread": "0.04" }
```

### Batch Spreads

```bash
POST /spreads
Content-Type: application/json

[{ "token_id": "TOKEN_A" }, { "token_id": "TOKEN_B" }]
```

### Get Last Trade Price

```bash
GET /last-trade-price?token_id={token_id}
```

**Response** includes `price` and `side` (BUY or SELL).

### Batch Last Trade Prices

```bash
POST /last-trades-prices
Content-Type: application/json

[{ "token_id": "TOKEN_A" }, { "token_id": "TOKEN_B" }]
```

### Get Fee Rate

```bash
GET /fee-rate?token_id={token_id}
# or
GET /fee-rate/{token_id}
```

**Response**:
```json
{ "base_fee": 30 }
```

Value is in **basis points**. Fee-free markets return `0`.

### Get Tick Size

```bash
GET /tick-size?token_id={token_id}
# or
GET /tick-size/{token_id}
```

Returns: `"0.1"` | `"0.01"` | `"0.001"` | `"0.0001"`

### Summary of Single vs Batch Endpoints

| Single | Batch | REST Method |
|---|---|---|
| `GET /book` | `POST /books` | GET / POST |
| `GET /price` | `POST /prices` | GET / POST |
| `GET /midpoint` | `POST /midpoints` | GET / POST |
| `GET /spread` | `POST /spreads` | GET / POST |
| `GET /last-trade-price` | `POST /last-trades-prices` | GET / POST |

---

## REST API: Historical Price Data (CLOB API)

### Price History

```bash
# By relative interval
GET /prices-history?market={token_id}&interval=1d&fidelity=60

# By absolute timestamp range
GET /prices-history?market={token_id}&startTs=1697875200&endTs=1697961600
```

> Note: the `market` parameter actually takes a **token ID**, not a condition ID.

**Parameters**:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `market` | string | Yes | Token ID (asset ID) |
| `interval` | string | No | Relative time window |
| `startTs` | number | No | Start unix timestamp |
| `endTs` | number | No | End unix timestamp |
| `fidelity` | integer | No | Data point interval in minutes (default: 1) |

`interval` and `startTs`/`endTs` are **mutually exclusive**.

**Available intervals**:

| Interval | Description |
|---|---|
| `1h` | Last hour |
| `6h` | Last 6 hours |
| `1d` | Last day |
| `1w` | Last week |
| `1m` | Last month |
| `max` / `all` | All available data |

**Response**:
```json
{
  "history": [
    { "t": 1697875200, "p": 0.52 },
    { "t": 1697875260, "p": 0.53 }
  ]
}
```

Each entry: `t` = unix timestamp (uint32), `p` = price (float).

### OHLC Data

```bash
GET /ohlc?asset_id={token_id}&startTs={timestamp}&fidelity={interval}
```

**Parameters**:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `asset_id` | string | Yes | Token ID |
| `startTs` | number | Yes | Start unix timestamp |
| `fidelity` | string | Yes | Candle interval: `1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`, `1w` |
| `limit` | integer | No | Max results (max 1000) |

> Documented only in error codes reference. Use with care -- may be less stable than `/prices-history`.

### Orderbook History

```bash
GET /orderbook-history?asset_id={token_id}&startTs={timestamp}
# or
GET /orderbook-history?market={condition_id}&startTs={timestamp}
```

**Parameters**:

| Parameter | Type | Required | Description |
|---|---|---|---|
| `asset_id` or `market` | string | Yes (one of) | Token ID or condition ID |
| `startTs` | number | Yes | Start unix timestamp |
| `limit` | integer | No | Max results (max 1000) |

> Also documented only in error codes. Provides historical orderbook snapshots.

---

## REST API: Market Metadata (Gamma API)

Base URL: `https://gamma-api.polymarket.com`

### List Events

```bash
GET /events?active=true&closed=false&limit=100&order=volume_24hr&ascending=false
```

**Key parameters**:

| Parameter | Type | Description |
|---|---|---|
| `active` | boolean | Filter active events |
| `closed` | boolean | Filter closed events |
| `limit` | integer | Results per page |
| `offset` | integer | Pagination offset |
| `order` | string | Sort field: `volume_24hr`, `volume`, `liquidity`, `start_date`, `end_date`, `competitive`, `closed_time` |
| `ascending` | boolean | Sort direction (default: false) |
| `slug` | string | Filter by slug |
| `tag_id` | integer | Filter by tag |
| `related_tags` | boolean | Include related tag markets |
| `exclude_tag_id` | integer | Exclude specific tag |

### Get Event by ID or Slug

```bash
GET /events/{id}
GET /events/slug/{slug}
GET /events?slug={slug}
```

**Event response** includes:
- `id`, `title`, `slug`, `description`
- `negRisk`, `negRiskAugmented`, `enableNegRisk`
- `markets[]` -- array of market objects
- `resolutionSource`
- `negRiskFeeBips`

### List Markets

```bash
GET /markets?active=true&closed=false&limit=100
```

**Key parameters**:

| Parameter | Type | Description |
|---|---|---|
| `active` | boolean | Filter active markets |
| `closed` | boolean | Filter closed markets |
| `limit` | integer | Results per page |
| `offset` | integer | Pagination offset |
| `slug` | string | Filter by slug |
| `uma_resolution_status` | string | Filter by resolution status |

### Get Market by ID or Slug

```bash
GET /markets/{id}
GET /markets/slug/{slug}
GET /markets?slug={slug}
```

**Market object key fields**:

| Field | Type | Description |
|---|---|---|
| `id` | string | Market ID |
| `question` | string | Market question text |
| `conditionId` | string | Condition ID (for CLOB) |
| `slug` | string | URL slug |
| `resolutionSource` | string | URL of resolution source |
| `outcomes` | string | JSON array of outcome names `["Yes","No"]` |
| `outcomePrices` | string | JSON array of prices `["0.20","0.80"]` |
| `tokens` | array | Token objects with `token_id`, `outcome`, `winner` |
| `enableOrderBook` | boolean | Whether tradeable on CLOB |
| `closed` | boolean | Whether market has closed |
| `fee` | string | Fee amount |
| `makerBaseFee` | number | Maker base fee in bps |
| `takerBaseFee` | number | Taker base fee in bps |
| `feesEnabled` | boolean | Whether fees are active |
| `minimum_tick_size` | string | Tick size |
| `neg_risk` | boolean | Neg risk flag |
| `umaResolutionStatus` | string | UMA resolution status |
| `volume` | string | Total volume |
| `volume24hr` | number | 24-hour volume |
| `liquidity` | string | Current liquidity |
| `startDate` | string | Market start date |
| `endDate` | string | Market end date |

### Sampling Markets (Lightweight)

```bash
GET /sampling/markets?limit=100
GET /sampling/simplified-markets?limit=100
```

Returns lightweight market objects with `maker_base_fee` and `taker_base_fee` fields. Useful for polling without full market payload.

### Search

```bash
GET /public-search?query={search_term}
```

Searches across events, markets, and profiles.

### Tags

```bash
GET /tags
GET /tags/{id}
GET /tags/slug/{slug}
```

### Series

```bash
GET /series
GET /series/{id}
```

### Sports Metadata

```bash
GET /sports
GET /teams
GET /sports/market-types
```

Sports metadata includes tag IDs, resolution sources, and series information.

---

## REST API: User & Trade Data (Data API)

Base URL: `https://data-api.polymarket.com`

### Trade History

```bash
GET /trades?market={condition_id}&limit=100&offset=0
```

**Parameters**:

| Parameter | Type | Description |
|---|---|---|
| `market` | string[] | Comma-separated condition IDs (mutually exclusive with `eventId`) |
| `eventId` | integer[] | Comma-separated event IDs (mutually exclusive with `market`) |
| `user` | string | User wallet address |
| `side` | string | `BUY` or `SELL` |
| `limit` | integer | Max results (0-10000, default 100) |
| `offset` | integer | Pagination offset (0-10000) |
| `takerOnly` | boolean | Only taker trades (default true) |
| `filterType` | string | `CASH` or `TOKENS` (must pair with `filterAmount`) |
| `filterAmount` | number | Minimum trade size |

**Trade response fields**:

| Field | Type | Description |
|---|---|---|
| `proxyWallet` | string | User proxy wallet address |
| `side` | string | BUY or SELL |
| `asset` | string | Asset/token ID |
| `conditionId` | string | Market condition ID |
| `size` | number | Trade size |
| `price` | number | Trade price |
| `timestamp` | integer | Unix timestamp |
| `title` | string | Market title |
| `slug` | string | Market slug |
| `outcome` | string | Outcome name |
| `outcomeIndex` | integer | Outcome index |
| `transactionHash` | string | Onchain transaction hash |

### User Positions

```bash
GET /positions?user={wallet_address}
```

### Closed Positions

```bash
GET /closed-positions?user={wallet_address}
```

### User Activity

```bash
GET /activity?user={wallet_address}
```

### Total Position Value

```bash
GET /value?user={wallet_address}
```

### Open Interest

```bash
GET /oi?market={condition_id}
```

### Top Holders

```bash
GET /holders?market={condition_id}
```

### Leaderboard

```bash
GET /leaderboard
```

### Rebated Fees (for Makers)

```bash
GET /rebated-fees?address={wallet}&date={YYYY-MM-DD}&market={condition_id}
```

No authentication required. Returns:

```json
{
  "address": "0x...",
  "market": "0x...",
  "date": "2026-01-20",
  "rebated_fees_usdc": "0.237519"
}
```

---

## REST API: CLOB Trade Data (Authenticated)

Base URL: `https://clob.polymarket.com`

These require L2 authentication headers. See [[Polymarket-Trading-API]].

### Get User Trades

```bash
GET /trades?market={condition_id}
```

**Trade object fields**:

| Field | Type | Description |
|---|---|---|
| `id` | string | Trade ID |
| `taker_order_id` | string | Taker order hash |
| `market` | string | Condition ID |
| `asset_id` | string | Token ID |
| `side` | string | BUY or SELL |
| `size` | string | Trade size |
| `price` | string | Execution price |
| `fee_rate_bps` | string | Fee rate in basis points |
| `status` | string | MATCHED/MINED/CONFIRMED/RETRYING/FAILED |
| `match_time` | string | Unix timestamp when matched |
| `last_update` | string | Unix timestamp of last status change |
| `outcome` | string | Outcome name |
| `maker_address` | string | Maker funder address |
| `trader_side` | string | TAKER or MAKER |
| `transaction_hash` | string | Onchain tx hash |
| `bucket_index` | number | Index for trade reconciliation |
| `maker_orders` | array | Array of maker orders matched |

Filter parameters: `id`, `market`, `asset_id`, `maker_address`, `before`, `after`.

Supports cursor-based pagination via `next_cursor`.

### Get User Orders

```bash
GET /orders?market={condition_id}&asset_id={token_id}
```

### Get Single Order

```bash
GET /order/{order_id}
```

---

## WebSocket API

### Available Channels

| Channel | Endpoint | Auth | Purpose |
|---|---|---|---|
| **Market** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | No | Orderbook, prices, trades |
| **User** | `wss://ws-subscriptions-clob.polymarket.com/ws/user` | Yes | Order & trade updates |
| **Sports** | `wss://sports-api.polymarket.com/ws` | No | Live sports scores |
| **RTDS** | `wss://ws-live-data.polymarket.com` | Optional | Comments, crypto prices |

### Market Channel

**Subscribe**:
```json
{
  "assets_ids": ["<token_id_1>", "<token_id_2>"],
  "type": "market",
  "custom_feature_enabled": true
}
```

Set `custom_feature_enabled: true` to receive `best_bid_ask`, `new_market`, and `market_resolved` events.

**Dynamic subscription** (no reconnect needed):
```json
{
  "assets_ids": ["NEW_TOKEN_ID"],
  "operation": "subscribe"
}
```

```json
{
  "assets_ids": ["OLD_TOKEN_ID"],
  "operation": "unsubscribe"
}
```

**Heartbeat**: Send `PING` every 10 seconds. Server responds with `PONG`.

#### Event Types

| Event | Trigger | Key Fields |
|---|---|---|
| `book` | On subscribe + trade affects book | `bids[]`, `asks[]`, `hash`, `timestamp` |
| `price_change` | New order placed or cancelled | `price_changes[]` with `price`, `size`, `side`, `best_bid`, `best_ask` |
| `last_trade_price` | Trade executed | `price`, `side`, `size`, `fee_rate_bps` |
| `tick_size_change` | Price hits >0.96 or <0.04 | `old_tick_size`, `new_tick_size` |
| `best_bid_ask` | Top-of-book changes (custom feature) | `best_bid`, `best_ask`, `spread` |
| `new_market` | Market created (custom feature) | `question`, `assets_ids`, `outcomes` |
| `market_resolved` | Market resolved (custom feature) | `winning_asset_id`, `winning_outcome` |

#### Example Messages

**`book` (full snapshot)**:
```json
{
  "event_type": "book",
  "asset_id": "65818619...",
  "market": "0xbd31dc8a...",
  "bids": [
    { "price": ".48", "size": "30" },
    { "price": ".49", "size": "20" }
  ],
  "asks": [
    { "price": ".52", "size": "25" },
    { "price": ".53", "size": "60" }
  ],
  "timestamp": "123456789000",
  "hash": "0x0...."
}
```

**`price_change`**:
```json
{
  "market": "0x5f65177b...",
  "price_changes": [
    {
      "asset_id": "71321045...",
      "price": "0.5",
      "size": "200",
      "side": "BUY",
      "hash": "56621a121a...",
      "best_bid": "0.5",
      "best_ask": "1"
    }
  ],
  "timestamp": "1757908892351",
  "event_type": "price_change"
}
```

A `size` of `"0"` means the price level has been **removed** from the book.

**`last_trade_price`**:
```json
{
  "asset_id": "114122071...",
  "event_type": "last_trade_price",
  "fee_rate_bps": "0",
  "market": "0x6a67b9d8...",
  "price": "0.456",
  "side": "BUY",
  "size": "219.217767",
  "timestamp": "1750428146322"
}
```

**`best_bid_ask`** (requires `custom_feature_enabled: true`):
```json
{
  "event_type": "best_bid_ask",
  "market": "0x0005c0d3...",
  "asset_id": "85354956...",
  "best_bid": "0.73",
  "best_ask": "0.77",
  "spread": "0.04",
  "timestamp": "1766789469958"
}
```

**`tick_size_change`**:
```json
{
  "event_type": "tick_size_change",
  "asset_id": "65818619...",
  "market": "0xbd31dc8a...",
  "old_tick_size": "0.01",
  "new_tick_size": "0.001",
  "timestamp": "100000000"
}
```

### User Channel (Authenticated)

**Subscribe**:
```json
{
  "auth": {
    "apiKey": "your-api-key",
    "secret": "your-api-secret",
    "passphrase": "your-passphrase"
  },
  "markets": ["0x1234...condition_id"],
  "type": "user"
}
```

> User channel subscribes by **condition IDs**, not asset IDs.

**Dynamic subscription**:
```json
{
  "markets": ["0x1234...condition_id"],
  "operation": "subscribe"
}
```

**Heartbeat**: Same as market channel -- send `PING` every 10 seconds.

#### Event Types

**`trade`** -- Emitted on match and subsequent status changes:
```json
{
  "asset_id": "52114319...",
  "event_type": "trade",
  "id": "28c4d2eb-...",
  "market": "0xbd31dc8a...",
  "price": "0.57",
  "side": "BUY",
  "size": "10",
  "status": "MATCHED",
  "taker_order_id": "0x06bc63e3...",
  "maker_orders": [
    {
      "asset_id": "52114319...",
      "matched_amount": "10",
      "order_id": "0xff354cd7...",
      "outcome": "YES",
      "price": "0.57"
    }
  ],
  "timestamp": "1672290701",
  "type": "TRADE"
}
```

Trade status flow: `MATCHED --> MINED --> CONFIRMED` (with possible `RETRYING --> FAILED` branch)

**`order`** -- Emitted on placement, update, or cancellation:
```json
{
  "asset_id": "52114319...",
  "event_type": "order",
  "id": "0xff354cd7...",
  "market": "0xbd31dc8a...",
  "original_size": "10",
  "price": "0.57",
  "side": "SELL",
  "size_matched": "0",
  "type": "PLACEMENT"
}
```

Order event types: `PLACEMENT`, `UPDATE`, `CANCELLATION`

### Sports Channel

**Endpoint**: `wss://sports-api.polymarket.com/ws`

No subscription message needed -- connect and receive all active sports events.

**Heartbeat**: Server sends `ping` every 5 seconds. Respond with `pong` within 10 seconds or connection is closed.

### RTDS Channel (Real-Time Data Socket)

**Endpoint**: `wss://ws-live-data.polymarket.com`

Streams:
- **Crypto prices** from Binance and Chainlink (no auth required)
- **Comments** (may require Gamma auth for user-specific data)

---

## Subgraph (GraphQL)

Onchain data indexed via Goldsky. All endpoints are public.

| Subgraph | Endpoint | Key Queries |
|---|---|---|
| **Positions** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/positions-subgraph/0.0.7/gn` | `userBalances`, `netUserBalances`, `tokenIdConditions` |
| **Orders** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn` | `marketDatas`, `orderFilledEvents`, `ordersMatchedEvents`, `orderbooks` |
| **Activity** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/activity-subgraph/0.0.4/gn` | `splits`, `merges`, `redemptions`, `negRiskConversions` |
| **Open Interest** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/oi-subgraph/0.0.6/gn` | `marketOpenInterests`, `globalOpenInterests` |
| **PNL** | `https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/pnl-subgraph/0.0.14/gn` | `userPositions` |

**Example query**:
```bash
curl -X POST \
  https://api.goldsky.com/api/public/project_cl6mb8i9h0003e201j6li0diw/subgraphs/orderbook-subgraph/0.0.1/gn \
  -H "Content-Type: application/json" \
  -d '{"query": "query { orderbooks { id tradesQuantity } }"}'
```

Source code: [polymarket-subgraph on GitHub](https://github.com/Polymarket/polymarket-subgraph)

---

## Market Discovery Strategies

### Strategy 1: Fetch by Slug

Extract slug from URL: `https://polymarket.com/event/fed-decision-in-october` --> slug = `fed-decision-in-october`

```bash
GET https://gamma-api.polymarket.com/events?slug=fed-decision-in-october
GET https://gamma-api.polymarket.com/events/slug/fed-decision-in-october
```

### Strategy 2: Fetch by Tags

```bash
# Discover tags
GET https://gamma-api.polymarket.com/tags
GET https://gamma-api.polymarket.com/sports

# Filter by tag
GET https://gamma-api.polymarket.com/events?tag_id=100381&active=true&closed=false
```

### Strategy 3: Fetch All Active Markets

```bash
GET https://gamma-api.polymarket.com/events?active=true&closed=false&order=volume_24hr&ascending=false&limit=100
```

### Pagination

All list endpoints use `limit` and `offset`:
```bash
GET /events?active=true&closed=false&limit=50&offset=0   # Page 1
GET /events?active=true&closed=false&limit=50&offset=50  # Page 2
```

---

## Data Model

### Events vs Markets

| Concept | Description |
|---|---|
| **Event** | Top-level container (e.g., "Who will win the election?"), contains 1+ markets |
| **Market** | Single binary outcome (Yes/No), has condition ID, question ID, and 2 token IDs |

**Single-market event**: "Will BTC reach $100k?" --> 1 market (Yes/No)
**Multi-market event**: "Who will win?" --> Markets for Trump, Harris, Other

### Outcomes and Prices

```json
{
  "outcomes": "[\"Yes\", \"No\"]",
  "outcomePrices": "[\"0.20\", \"0.80\"]"
}
```

Arrays map 1:1. Prices represent implied probabilities. A market is tradeable on the CLOB if `enableOrderBook` is `true`.

---

## Rate Limits

All rate limits use Cloudflare's throttling (requests are delayed/queued, not rejected). Sliding time windows.

### Gamma API Rate Limits

| Endpoint | Limit |
|---|---|
| General | 4,000 req / 10s |
| `/events` | 500 req / 10s |
| `/markets` | 300 req / 10s |
| `/markets` + `/events` listing | 900 req / 10s |
| `/comments` | 200 req / 10s |
| `/tags` | 200 req / 10s |
| `/public-search` | 350 req / 10s |

### Data API Rate Limits

| Endpoint | Limit |
|---|---|
| General | 1,000 req / 10s |
| `/trades` | 200 req / 10s |
| `/positions` | 150 req / 10s |
| `/closed-positions` | 150 req / 10s |

### CLOB API -- Market Data Rate Limits

| Endpoint | Limit |
|---|---|
| `/book` | 1,500 req / 10s |
| `/books` | 500 req / 10s |
| `/price` | 1,500 req / 10s |
| `/prices` | 500 req / 10s |
| `/midpoint` | 1,500 req / 10s |
| `/midpoints` | 500 req / 10s |
| `/prices-history` | 1,000 req / 10s |
| Market tick size | 200 req / 10s |

### General

| Endpoint | Limit |
|---|---|
| General rate limiting | 15,000 req / 10s |
| Health check (`/ok`) | 100 req / 10s |

See [[Polymarket-Trading-API]] for trading-specific rate limits.

---

## Server Time

```bash
GET https://clob.polymarket.com/time
```

Returns current server time. Useful for synchronizing timestamps for authentication.

---

## Best Practices for Market Data

1. **Use WebSocket for live data** -- subscribe to the market channel instead of polling REST endpoints
2. **Use batch endpoints** for multiple tokens (up to 500 per request)
3. **Use events endpoint first** -- events contain their markets, reducing API calls
4. **Always include `active=true&closed=false`** unless you need historical data
5. **Monitor `tick_size_change` events** -- critical for trading bots; orders with wrong tick size are rejected
6. **Use `hash` field** on orderbook to detect changes efficiently
7. **Use `fidelity` parameter** on price history to control data density
8. **Prefer batch POST endpoints** for high-frequency polling of multiple markets
