---
title: Range Binary Markets on Polymarket
created: 2026-03-31
tags:
  - polymarket
  - range-markets
  - binary-options
  - strategy
  - market-making
  - breeden-litzenberger
---

# Range Binary Markets on Polymarket

## Market Structure

Polymarket offers **range binary markets** alongside simple above/below markets:

> **"Will AMZN finish the week of March 30 between $210-$220?"**

These pay $1.00 if the underlying closes within the specified range, $0.00 otherwise. A complete range event typically partitions the price space into non-overlapping ranges:

| Market | Range | Implied P |
|--------|-------|-----------|
| AMZN < $190 | (-∞, $190) | ~5% |
| AMZN $190-$200 | [$190, $200) | ~12% |
| AMZN $200-$210 | [$200, $210) | ~25% |
| AMZN $210-$220 | [$210, $220) | ~30% |
| AMZN $220-$230 | [$220, $230) | ~18% |
| AMZN > $230 | ($230, ∞) | ~10% |
| **Total** | | **100%** |

## Pricing: Direct B-L Application

A range binary is the difference of two cumulative probabilities from the [[Breeden-Litzenberger-Pipeline]]:

$$V_{\text{range}}(K_1, K_2) = P^{\mathbb{Q}}(S_T > K_1) - P^{\mathbb{Q}}(S_T > K_2)$$

This is equivalent to a **digital call spread** in options terms. No new pricing machinery is needed — the same B-L extraction pipeline that prices above/below markets prices range markets directly.

For the expiry mismatch problem (Polymarket date ≠ options expiry), the same interpolation approaches from [[Breeden-Litzenberger-Pipeline#7. Expiry Mismatch]] apply.

## Why Range Markets Are Attractive for Market Making

### 1. Larger Mispricings Expected

Range probabilities are harder for retail participants to intuit than simple above/below:
- "Will AMZN be above $210?" → easy directional intuition
- "Will AMZN be between $210-$220?" → requires estimating both tails, much harder

This should produce **systematically larger mispricings** relative to B-L fair values.

### 2. Sum-to-One Arbitrage Constraint

All non-overlapping ranges for the same event must satisfy:

$$\sum_{i} P(K_i < S_T < K_{i+1}) = 1$$

If Polymarket prices for the ranges sum to more or less than $1.00, there is a **pure arbitrage opportunity**:
- **Sum > $1.00**: Sell all ranges. Guaranteed profit = sum - $1.00 per unit.
- **Sum < $1.00**: Buy all ranges. Guaranteed profit = $1.00 - sum per unit.

In practice, the sum will be close to $1.00 but individual ranges can still be mispriced relative to each other. This creates **relative value** opportunities beyond the absolute mispricing vs B-L.

### 3. Cross-Range Relationships

For adjacent ranges, additional constraints exist:

$$V(K_1, K_2) + V(K_2, K_3) = V(K_1, K_3)$$

If Polymarket violates this, there's an arbitrage between the individual ranges and any broader range that spans them.

### 4. Greeks Behavior

Range binary Greeks are more complex than above/below:

**Delta**: Changes sign — positive when stock moves toward the range, negative when moving away. A range binary at $210-$220 with stock at $200 has positive delta (want stock to go up into range), but at $215 has near-zero delta, and at $225 has negative delta (want stock to come back down).

**Gamma**: Higher than single-barrier binaries because delta changes direction. This means more dynamic quoting and more adverse selection risk near the boundaries.

**Theta**: Range binaries that are "in the range" gain time value as the probability of staying increases with less time remaining.

## Backtesting Considerations

### Data from Telonex

Range markets are standard Polymarket markets with YES/NO tokens. Telonex should have:
- `book_snapshot_full` for each range market's YES and NO tokens
- `trades` for each range market

Each range in an event is a separate market with its own orderbook.

### Strategy Adaptations

1. **Sum-to-one monitoring**: Track the sum of all range prices in real time. When the sum deviates significantly from $1.00, signal an arbitrage opportunity.

2. **Cross-range inventory management**: Positions across ranges on the same event are correlated. Long the $210-$220 range and short the $200-$210 range creates a directional bet that AMZN finishes above $210.

3. **Portfolio delta**: The aggregate delta across all range positions should be computed relative to the underlying stock price, not individually per range.

4. **Spread calibration**: Range markets may have different liquidity profiles than above/below markets. Spreads should be calibrated per market type.

### Engine Support Needed

The [[Engine-Architecture-Plan]] should support:
- Multiple markets per event (already planned for multi-strike above/below)
- Sum-to-one parity checks (analogous to [[btc-backtesting-engine|cross-leg parity checks]] but across ranges instead of YES/NO)
- Per-event portfolio aggregation
- Range-aware fair value computation (B-L difference)

## Comparison: Above/Below vs Range Markets

| Aspect | Above/Below | Range |
|--------|------------|-------|
| Pricing | $P(S > K)$ | $P(K_1 < S < K_2)$ |
| B-L inputs | 1 probability | 2 probabilities (difference) |
| Arbitrage constraints | Monotonicity across strikes | Sum-to-one across ranges |
| Retail difficulty | Easy to intuit | Harder → more mispricing |
| Delta behavior | Single sign | Changes sign (more complex) |
| Gamma | High near strike | Higher (two boundaries) |
| Market making complexity | Medium | Higher |
| Expected edge | Good | Potentially larger |

## Related Notes

- [[Breeden-Litzenberger-Pipeline]] — Probability extraction (range = difference of two CDFs)
- [[Core-Market-Making-Strategies]] — Quoting strategies (adaptable to range markets)
- [[Engine-Architecture-Plan]] — Engine must support multi-market events
- [[Inventory-and-Risk-Management]] — Cross-range inventory correlation
- [[Capital-Efficiency-and-Edge-Cases]] — Sum-to-one arbitrage as a low-risk strategy
