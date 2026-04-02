---
title: Polymarket Trading API
created: 2026-03-31
updated: 2026-03-31
tags:
  - polymarket
  - trading
  - api
  - authentication
  - orders
  - rate-limits
  - research
sources:
  - https://docs.polymarket.com/api-reference/authentication
  - https://docs.polymarket.com/trading/overview
  - https://docs.polymarket.com/trading/orders/create
  - https://docs.polymarket.com/trading/orders/cancel
  - https://docs.polymarket.com/api-reference/rate-limits
  - https://docs.polymarket.com/resources/error-codes
  - https://docs.polymarket.com/market-makers/getting-started
  - https://docs.polymarket.com/market-makers/trading
---

# Polymarket Trading API

Authentication, order placement, execution, cancellation, rate limits, and operational details for building a live trading system on Polymarket.

## Authentication

### Two-Level Model

| Level | Method | Purpose |
|---|---|---|
| **L1** | EIP-712 signature (private key) | Create or derive API credentials |
| **L2** | HMAC-SHA256 (API credentials) | Place orders, cancel orders, query trades |

You use your private key **once** to derive L2 credentials (apiKey, secret, passphrase), which authenticate all subsequent trading requests.

### Public vs Authenticated Endpoints

| Category | Auth Required |
|---|---|
| Gamma API (all endpoints) | No |
| Data API (all endpoints) | No |
| CLOB read endpoints (orderbook, prices, spreads) | No |
| CLOB trading endpoints (orders, cancellations, heartbeat) | Yes (L2) |

### Getting API Credentials

#### Via SDK

```typescript
// TypeScript
import { ClobClient } from "@polymarket/clob-client";
import { Wallet } from "ethers"; // v5.8.0

const client = new ClobClient(
  "https://clob.polymarket.com",
  137,
  new Wallet(process.env.PRIVATE_KEY)
);
const credentials = await client.createOrDeriveApiKey();
// { apiKey: "550e8400-...", secret: "base64...", passphrase: "..." }
```

```python
# Python
from py_clob_client.client import ClobClient

client = ClobClient("https://clob.polymarket.com", key=private_key, chain_id=137)
credentials = client.create_or_derive_api_creds()
```

```rust
// Rust
let client = Client::new("https://clob.polymarket.com", Config::default())?
    .authentication_builder(&signer)
    .authenticate()
    .await?;
```

#### Via REST API

**Create credentials**:
```bash
POST https://clob.polymarket.com/auth/api-key
```

**Derive existing credentials**:
```bash
GET https://clob.polymarket.com/auth/derive-api-key
```

Both require L1 headers:

| Header | Description |
|---|---|
| `POLY_ADDRESS` | Polygon signer address |
| `POLY_SIGNATURE` | EIP-712 signature |
| `POLY_TIMESTAMP` | Current UNIX timestamp |
| `POLY_NONCE` | Nonce (default: 0) |

The EIP-712 signing domain:
```typescript
const domain = {
  name: "ClobAuthDomain",
  version: "1",
  chainId: 137
};

const types = {
  ClobAuth: [
    { name: "address", type: "address" },
    { name: "timestamp", type: "string" },
    { name: "nonce", type: "uint256" },
    { name: "message", type: "string" }
  ]
};

const value = {
  address: signingAddress,
  timestamp: ts,
  nonce: nonce,
  message: "This message attests that I control the given wallet"
};
```

Reference implementations:
- [TypeScript EIP-712](https://github.com/Polymarket/clob-client/blob/main/src/signing/eip712.ts)
- [Python EIP-712](https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/signing/eip712.py)

### L2 Authentication Headers

All trading endpoints require these 5 headers:

| Header | Description |
|---|---|
| `POLY_ADDRESS` | Polygon signer address |
| `POLY_SIGNATURE` | HMAC-SHA256 signature of request |
| `POLY_TIMESTAMP` | Current UNIX timestamp |
| `POLY_API_KEY` | API key value |
| `POLY_PASSPHRASE` | API passphrase value |

HMAC signature reference:
- [TypeScript HMAC](https://github.com/Polymarket/clob-client/blob/main/src/signing/hmac.ts)
- [Python HMAC](https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/signing/hmac.py)

> Even with L2 authentication, order placement still requires the user's private key for EIP-712 order payload signing. L2 authenticates the request; the order itself must be signed by the key.

---

## Wallet & Signature Types

| Type | ID | Description | Funder Address |
|---|---|---|---|
| **EOA** | `0` | Standalone wallet, pays own gas (POL) | Your EOA address |
| **POLY_PROXY** | `1` | Magic Link (email/Google) login, exported PK | Your proxy wallet address |
| **GNOSIS_SAFE** | `2` | Browser wallet (MetaMask, Rabby) or embedded wallet (Privy, Turnkey) -- most common | Your proxy wallet address |

### Initialize Trading Client

```typescript
// TypeScript
const client = new ClobClient(
  "https://clob.polymarket.com",
  137,
  signer,
  apiCreds,
  2,        // GNOSIS_SAFE
  "0x..."   // proxy wallet address (funder)
);
```

```python
# Python
client = ClobClient(
    "https://clob.polymarket.com",
    key=private_key,
    chain_id=137,
    creds=api_creds,
    signature_type=2,       # GNOSIS_SAFE
    funder="0x..."          # proxy wallet address
)
```

```rust
// Rust -- funder auto-derived via CREATE2
let client = Client::new("https://clob.polymarket.com", Config::default())?
    .authentication_builder(&signer)
    .signature_type(SignatureType::GnosisSafe)
    .authenticate()
    .await?;
```

---

## Official SDKs

| Language | Package | Repository |
|---|---|---|
| TypeScript | `@polymarket/clob-client` | [github.com/Polymarket/clob-client](https://github.com/Polymarket/clob-client) |
| Python | `py-clob-client` | [github.com/Polymarket/py-clob-client](https://github.com/Polymarket/py-clob-client) |
| Rust | `polymarket-client-sdk` | [github.com/Polymarket/rs-clob-client](https://github.com/Polymarket/rs-clob-client) |

All three support full CLOB API including market data, order management, and authentication.

---

## One-Time Setup (Market Maker)

### 1. Deposit USDC.e to Polygon

Methods: Bridge API, direct Polygon transfer, or cross-chain bridge.

```bash
POST https://bridge.polymarket.com/deposit
Content-Type: application/json

{ "address": "YOUR_POLYMARKET_WALLET_ADDRESS" }
```

### 2. Deploy a Wallet

For Safe wallet (gasless transactions):
```typescript
const client = new RelayClient(
  "https://relayer-v2.polymarket.com/",
  137, signer, builderConfig, RelayerTxType.SAFE
);
const response = await client.deploy();
```

### 3. Approve Tokens

| Token | Spender | Purpose |
|---|---|---|
| USDC.e | CTF Contract (`0x4D97DCd97eC945f40cF65F87097ACe5EA0476045`) | Split into tokens |
| CTF tokens | CTF Exchange (`0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E`) | Trade standard markets |
| CTF tokens | Neg Risk CTF Exchange (`0xC5d563A36AE78145C45a50134d48A1215220f80a`) | Trade neg risk markets |

Approve with `MaxUint256` for convenience. Can be done gaslessly via Relayer Client.

### 4. Generate API Credentials

See [Authentication](#authentication) above.

---

## Order Placement

### POST /order -- Place Single Order

```bash
POST https://clob.polymarket.com/order
```

**Request body** (`SendOrder`):

```json
{
  "order": {
    "maker": "0x...",
    "signer": "0x...",
    "taker": "0x0000000000000000000000000000000000000000",
    "tokenId": "71321045679252212...",
    "makerAmount": "100000000",
    "takerAmount": "200000000",
    "side": "BUY",
    "expiration": "1735689600",
    "nonce": "0",
    "feeRateBps": "30",
    "signature": "0x1234abcd...",
    "salt": 1234567890,
    "signatureType": 2
  },
  "owner": "f4f247b7-4ac7-ff29-a152-04fda0a8755a",
  "orderType": "GTC",
  "deferExec": false
}
```

**Order object fields**:

| Field | Type | Required | Description |
|---|---|---|---|
| `maker` | string | Yes | Funder address (proxy wallet for GNOSIS_SAFE) |
| `signer` | string | Yes | Signing address (EOA private key address) |
| `taker` | string | Yes | Taker address (`0x0` for open orders) |
| `tokenId` | string | Yes | Token ID (asset ID) |
| `makerAmount` | string | Yes | Amount maker provides (6 decimal fixed-point) |
| `takerAmount` | string | Yes | Amount taker provides (6 decimal fixed-point) |
| `side` | string | Yes | `BUY` or `SELL` |
| `expiration` | string | Yes | Unix timestamp (`"0"` for no expiration) |
| `nonce` | string | Yes | Order nonce |
| `feeRateBps` | string | Yes | Fee rate in basis points (fetch dynamically) |
| `signature` | string | Yes | EIP-712 signature |
| `salt` | integer | Yes | Random salt for uniqueness |
| `signatureType` | integer | Yes | 0=EOA, 1=POLY_PROXY, 2=GNOSIS_SAFE |

**Wrapper fields**:

| Field | Type | Required | Description |
|---|---|---|---|
| `owner` | string | Yes | UUID of the API key owner |
| `orderType` | string | No | `GTC` (default), `GTD`, `FOK`, `FAK` |
| `deferExec` | boolean | No | Whether to defer execution (default: false) |

**Response** (`SendOrderResponse`):

```json
{
  "success": true,
  "orderID": "0xabc123...",
  "status": "live",
  "makingAmount": "100000000",
  "takingAmount": "200000000",
  "transactionsHashes": [],
  "tradeIDs": [],
  "errorMsg": ""
}
```

| Status | Description |
|---|---|
| `live` | Resting on the book |
| `matched` | Matched immediately |
| `delayed` | Subject to matching delay |

### POST /orders -- Batch Orders (up to 15)

```bash
POST https://clob.polymarket.com/orders
```

Request body is an array of `SendOrder` objects.

Per-order errors are returned in the 200 response array with individual error messages.

### SDK Examples (Recommended)

#### Limit Order (One-Step)

```typescript
const response = await client.createAndPostOrder(
  { tokenID: "TOKEN_ID", price: 0.5, size: 10, side: Side.BUY },
  { tickSize: "0.01", negRisk: false },
  OrderType.GTC
);
```

```python
response = client.create_and_post_order(
    OrderArgs(token_id="TOKEN_ID", price=0.50, size=10, side=BUY),
    options={"tick_size": "0.01", "neg_risk": False},
    order_type=OrderType.GTC
)
```

#### Market Order (FOK)

```typescript
// BUY: specify dollar amount
const response = await client.createAndPostMarketOrder(
  { tokenID: "TOKEN_ID", side: Side.BUY, amount: 100, price: 0.5 },
  { tickSize: "0.01", negRisk: false },
  OrderType.FOK
);

// SELL: specify share amount
const response = await client.createAndPostMarketOrder(
  { tokenID: "TOKEN_ID", side: Side.SELL, amount: 200, price: 0.45 },
  { tickSize: "0.01", negRisk: false },
  OrderType.FOK
);
```

The `price` field on market orders is a **worst-price limit** (slippage protection), not target price.

#### GTD Order (Auto-Expire)

```typescript
const expiration = Math.floor(Date.now() / 1000) + 60 + 3600; // 1 hour effective

const response = await client.createAndPostOrder(
  { tokenID: "TOKEN_ID", price: 0.5, size: 10, side: Side.BUY, expiration },
  { tickSize: "0.01", negRisk: false },
  OrderType.GTD
);
```

Remember: **60-second security threshold** on GTD expiration.

#### Post-Only Order

```typescript
const response = await client.postOrder(signedOrder, OrderType.GTC, true);
```

Only with GTC/GTD. Rejected if it would cross the spread.

#### Estimate Fill Price

```typescript
const price = await client.calculateMarketPrice(
  "TOKEN_ID", Side.BUY, 500, OrderType.FOK
);
```

Walks the orderbook to estimate slippage for a given size.

---

## Order Cancellation

All cancel endpoints require L2 authentication.

### Cancel Single Order

```bash
DELETE https://clob.polymarket.com/order
Content-Type: application/json

{ "orderID": "0xb816482a..." }
```

### Cancel Multiple Orders

```bash
DELETE https://clob.polymarket.com/orders
Content-Type: application/json

["0xb816482a...", "0xc927593b..."]
```

### Cancel All Orders

```bash
DELETE https://clob.polymarket.com/cancel-all
```

### Cancel by Market

```bash
DELETE https://clob.polymarket.com/cancel-market-orders
Content-Type: application/json

{
  "market": "0xbd31dc8a...",
  "asset_id": "52114319501245..."
}
```

Both `market` and `asset_id` are optional. Omit both to cancel all.

**Response format** (all cancel endpoints):
```json
{
  "canceled": ["0xb816482a..."],
  "not_canceled": { "0xc927593b...": "reason" }
}
```

### Onchain Cancellation (Fallback)

If the API is unavailable, cancel directly on the Exchange contract by calling `cancelOrder(Order order)` onchain. Uses `CTFExchange` or `NegRiskCTFExchange` depending on market type.

---

## Heartbeat

```bash
POST https://clob.polymarket.com/heartbeat
```

**Critical for market makers**: if no valid heartbeat within **10 seconds** (5-second buffer), **all open orders are cancelled**.

```typescript
let heartbeatId = "";
setInterval(async () => {
  const resp = await client.postHeartbeat(heartbeatId);
  heartbeatId = resp.heartbeat_id;
}, 5000);
```

```python
heartbeat_id = ""
while True:
    resp = client.post_heartbeat(heartbeat_id)
    heartbeat_id = resp["heartbeat_id"]
    time.sleep(5)
```

```rust
// Auto-send in background with `heartbeats` feature:
Client::start_heartbeats(&mut client)?;
// ... trading logic ...
client.stop_heartbeats().await?;
```

- First request: empty string for `heartbeat_id`
- If expired ID: server responds 400 with correct ID -- update and retry
- Send every ~5 seconds for safety

---

## Order Scoring (Maker Rebates Eligibility)

Check if resting orders qualify for maker rebates:

```bash
# Single order
GET https://clob.polymarket.com/order-scoring?orderId=0x...

# Multiple orders
POST https://clob.polymarket.com/order-scoring
Content-Type: application/json

{ "orderIds": ["0x...", "0x..."] }
```

---

## Fee Handling in Orders

### For SDK Users

The SDK **automatically handles fees** -- it fetches the fee rate and includes `feeRateBps` in the signed order. No extra work needed.

### For REST API Users

1. **Fetch fee rate**: `GET /fee-rate?token_id={token_id}`
2. **Include in order**: Add `feeRateBps` to order object before signing
3. **Sign complete order**: The CLOB validates signature against the included fee rate
4. **Never hardcode**: Fee rates vary by market type and may change

Fee-free markets return `0`. Fee-enabled markets return a non-zero value.

See [[Polymarket-CLOB-Mechanics]] for full fee structure details.

---

## Rate Limits (Trading)

Trading endpoints have both **burst** (short spikes) and **sustained** (longer-term) limits:

| Endpoint | Burst Limit | Sustained Limit |
|---|---|---|
| `POST /order` | 3,500 req / 10s | 36,000 req / 10 min |
| `DELETE /order` | 3,000 req / 10s | 30,000 req / 10 min |
| `POST /orders` (batch) | 1,000 req / 10s | 15,000 req / 10 min |
| `DELETE /orders` (batch) | 1,000 req / 10s | 15,000 req / 10 min |
| `DELETE /cancel-all` | 250 req / 10s | 6,000 req / 10 min |
| `DELETE /cancel-market-orders` | 1,000 req / 10s | 1,500 req / 10 min |

### Other Rate Limits

| Endpoint | Limit |
|---|---|
| `/trades`, `/orders`, `/notifications`, `/order` (ledger) | 900 req / 10s |
| `/data/orders`, `/data/trades` | 500 req / 10s |
| `/notifications` | 125 req / 10s |
| API key endpoints | 100 req / 10s |
| Relayer `/submit` | 25 req / 1 min |
| General CLOB rate limit | 9,000 req / 10s |
| Overall rate limit | 15,000 req / 10s |

All limits enforced via Cloudflare throttling (delayed/queued, not immediately rejected). Sliding time windows.

**429 Too Many Requests**: Implement exponential backoff.

---

## Error Handling

### HTTP Status Codes

| Status | Meaning | Action |
|---|---|---|
| `400` | Bad Request | Invalid parameters, payload, or business logic violation |
| `401` | Unauthorized | Missing/invalid API key, bad HMAC signature |
| `404` | Not Found | Market/order/token not recognized |
| `425` | Too Early | Matching engine restarting -- retry with backoff |
| `429` | Too Many Requests | Rate limited -- exponential backoff |
| `500` | Internal Server Error | Retry with backoff |
| `503` | Service Unavailable | Exchange paused or cancel-only mode |

### Order Placement Errors

| Error | Description |
|---|---|
| `INVALID_ORDER_MIN_TICK_SIZE` | Price doesn't conform to tick size |
| `INVALID_ORDER_MIN_SIZE` | Below minimum order size |
| `INVALID_ORDER_DUPLICATED` | Identical order already placed |
| `INVALID_ORDER_NOT_ENOUGH_BALANCE` | Insufficient balance or allowance |
| `INVALID_ORDER_EXPIRATION` | Expiration in the past |
| `INVALID_POST_ONLY_ORDER_TYPE` | Post-only with FOK/FAK |
| `INVALID_POST_ONLY_ORDER` | Post-only order would cross book |
| `FOK_ORDER_NOT_FILLED_ERROR` | FOK couldn't be fully filled |
| `INVALID_ORDER_ERROR` | System error inserting order |
| `EXECUTION_ERROR` | System error executing trade |
| `ORDER_DELAYED` | Match delayed due to market conditions |
| `MARKET_NOT_READY` | Market not yet accepting orders |

### Global Errors

| Error | Description |
|---|---|
| `Unauthorized/Invalid api key` | API key missing, expired, or invalid |
| `Invalid L1 Request headers` | L1 auth headers malformed |
| `Trading is currently disabled` | Exchange paused, no operations |
| `Trading is currently cancel-only` | Can cancel but not place orders |

### Matching Engine Restart

- HTTP 425 (Too Early) during restart
- Weekly Tuesdays at 7:00 AM ET, ~90 seconds duration
- Retry with exponential backoff (start 1-2s, max 30s)

```python
def post_with_retry(path, body, headers, max_retries=10):
    delay = 1
    for attempt in range(max_retries):
        response = requests.post(f"{CLOB_HOST}{path}", json=body, headers=headers)
        if response.status_code == 425:
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        return response
    raise Exception("Engine restart exceeded maximum retry attempts")
```

---

## Inventory Management (CTF Operations)

All operations can be executed gaslessly via the Relayer Client.

### Split USDC.e into Tokens

```typescript
// Split $1000 USDC.e --> 1000 YES + 1000 NO
const amount = ethers.utils.parseUnits("1000", 6);
const splitTx = {
  to: CTF_ADDRESS,
  data: ctfInterface.encodeFunctionData("splitPosition", [
    USDCe_ADDRESS,
    ethers.constants.HashZero,  // parentCollectionId (always zero)
    conditionId,
    [1, 2],                     // partition: [YES, NO]
    amount
  ]),
  value: "0"
};
await client.execute([splitTx], "Split");
```

### Merge Tokens to USDC.e

```typescript
// Merge 500 YES + 500 NO --> $500 USDC.e
const mergeTx = {
  to: CTF_ADDRESS,
  data: ctfInterface.encodeFunctionData("mergePositions", [
    USDCe_ADDRESS,
    ethers.constants.HashZero,
    conditionId,
    [1, 2],
    ethers.utils.parseUnits("500", 6)
  ]),
  value: "0"
};
await client.execute([mergeTx], "Merge");
```

### Redeem After Resolution

```typescript
const redeemTx = {
  to: CTF_ADDRESS,
  data: ctfInterface.encodeFunctionData("redeemPositions", [
    USDCe_ADDRESS,
    ethers.constants.HashZero,
    conditionId,
    [1, 2]   // Redeem both -- only winners pay out
  ]),
  value: "0"
};
await client.execute([redeemTx], "Redeem");
```

### Batch Operations

Execute multiple inventory operations in a single relayer call for efficiency.

---

## WebSocket for Trade Monitoring

See [[Polymarket-Data-API]] for full WebSocket documentation.

### User Channel (Authenticated)

```
wss://ws-subscriptions-clob.polymarket.com/ws/user
```

Subscribe with API credentials to receive real-time:
- **Trade events**: match, mine, confirm status progression
- **Order events**: placement, update, cancellation

Critical for:
- Monitoring fill notifications
- Tracking trade settlement status
- Detecting order cancellations

---

## Market Making Best Practices

### Quote Management

- Quote **both sides** to earn maximum liquidity rewards
- **Skew on inventory** -- adjust quote prices based on current position
- **Cancel stale quotes** immediately when conditions change
- Use **GTD for events** -- auto-expire quotes before known catalysts
- Use **post-only orders** to guarantee maker status

### Performance

- **Batch orders** -- use `POST /orders` (up to 15) instead of individual calls
- **WebSocket for data** -- subscribe to real-time feeds instead of polling
- **Monitor fills** via WebSocket user channel

### Risk Controls

- **Heartbeat** -- send every 5 seconds (mandatory if using heartbeat feature)
- **Kill switch** -- `cancelAll()` immediately on errors or position breaches
- **Size limits** -- check balances before quoting; don't exceed available inventory
- **Price guards** -- validate prices against book midpoint; reject outliers
- **Handle 425** -- implement retry logic for matching engine restarts

### Inventory Management

- Split sufficient USDC.e before quoting to cover expected size
- Skew quotes when inventory becomes imbalanced
- Merge excess tokens to free capital for other markets
- After resolution: cancel orders, redeem winning tokens, merge remaining pairs

---

## Announcements & Support

- **Telegram**: [t.me/polytradingapis](https://t.me/polytradingapis) -- real-time API announcements
- **Discord**: [#trading-apis](https://discord.com/channels/710897173927297116/1473553279421255803) -- community support
- **Email**: support@polymarket.com -- market maker onboarding
- **GitHub**: Open-source SDKs and contract source code
