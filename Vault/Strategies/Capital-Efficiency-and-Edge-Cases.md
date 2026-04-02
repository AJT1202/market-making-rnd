---
title: Capital Efficiency and Edge Cases in Binary Market Making
created: 2026-03-31
updated: 2026-03-31
tags:
  - strategy
  - market-making
  - polymarket
  - capital-efficiency
  - sharpe-ratio
  - edge-cases
  - earnings
  - gap-risk
  - platform-risk
  - binary-options
sources:
  - https://www.research.hangukquant.com/p/digital-option-market-making-on-prediction
  - https://newyorkcityservers.com/blog/prediction-market-making-guide
  - https://www.odaily.news/en/post/5209869
  - https://docs.polymarket.com/developers/market-makers/liquidity-rewards
  - https://www.quantstart.com/articles/Sharpe-Ratio-for-Algorithmic-Trading-Performance-Measurement/
  - https://www.sciencedirect.com/science/article/abs/pii/S0169207018300712
  - https://pmc.ncbi.nlm.nih.gov/articles/PMC7517297/
---

# Capital Efficiency and Edge Cases in Binary Market Making

This note covers return metrics, capital allocation, and risk scenarios for market making on Polymarket's stock/index binary event markets. It complements [[Core-Market-Making-Strategies]] (quoting frameworks) and [[Inventory-and-Risk-Management]] (hedging and adverse selection).

---

## 1. Return Metrics for Binary Market Making

### Expected Return Per Trade

The expected return on a round-trip market making trade (buy at bid, sell at ask) is:

$$
E[\text{Return per RT}] = \underbrace{s}_{\text{spread captured}} - \underbrace{c_{\text{adverse}}}_{\text{adverse selection cost}} - \underbrace{c_{\text{inventory}}}_{\text{inventory carry cost}}
$$

where:
- $s$ = realized half-spread (the effective spread earned, typically less than the quoted half-spread due to partial fills and price movement)
- $c_{\text{adverse}}$ = expected cost from trading with informed counterparties (fills that move against you)
- $c_{\text{inventory}}$ = cost of carrying residual inventory to resolution (expected loss on positions that don't round-trip)

### Decomposing Spread Revenue

For a market maker quoting $\delta$ half-spread around fair value $V$, quoting $N$ contracts per day with fill rate $f$:

$$
\text{Daily Spread Revenue} = 2 \cdot N \cdot f \cdot \delta
$$

The factor of 2 reflects earning on both sides (bid and ask). In practice, fills are rarely symmetric, and the effective spread is compressed:

$$
\text{Effective Spread} = \frac{\sum_i |p_i - V_i| \cdot \text{sign}_i}{\text{Total Fills}}
$$

where $p_i$ is the fill price, $V_i$ is fair value at fill time, and $\text{sign}_i$ is +1 for maker sells, -1 for maker buys.

### Example: Daily P&L Estimation

Assumptions for a single market:
- Fair value: $V = 0.55$
- Quoted half-spread: $\delta = 0.025$ (2.5 cents)
- Daily quote size: 200 tokens per side
- Fill rate: 30% (typical for passive limit orders on thin markets)
- Adverse selection cost: 0.5 cents per fill (average)
- Inventory carry: 20% of positions don't round-trip

| Component | Calculation | Daily P&L |
|-----------|------------|-----------|
| Spread revenue (both sides) | $2 \times 200 \times 0.30 \times \$0.025$ | +\$3.00 |
| Adverse selection | $120 \text{ fills} \times \$0.005$ | -\$0.60 |
| Inventory carry (20% of fills at avg 1c loss) | $24 \text{ positions} \times \$0.01$ | -\$0.24 |
| **Net daily P&L** | | **+\$2.16** |

Capital deployed: ~200 tokens $\times$ \$0.55 = \$110 per side, \$220 total. Daily return: $\$2.16 / \$220 = 0.98\%$.

> [!note] Scaling Across Markets
> With 10 independent markets at similar parameters, daily P&L scales to ~\$21.60 on ~\$2,200 deployed capital. Annual return (250 trading days): ~\$5,400 on \$2,200 average deployed, or ~245% annualized. However, this overstates returns because (a) not all capital is always deployed, (b) losing days can wipe out multiple winning days due to binary resolution, and (c) markets are correlated.

---

## 2. Capital Lockup and Opportunity Cost

### The Capital Lockup Problem

Unlike equity market making where positions can be closed at any time, Polymarket binary positions may be effectively locked until resolution due to:
1. **Thin order book**: Selling a large position incurs significant slippage
2. **Illiquid counterparty**: No buyers for your position at reasonable prices
3. **Strategic holding**: The position has positive expected value -- selling would abandon the edge

### Capital Lockup Duration by Market Type

| Market Type | Typical Duration | Capital Turnover |
|-------------|-----------------|-----------------|
| Daily expiry | 1-16 hours | 1-2x per day |
| Weekly expiry | 1-5 days | 1-5x per week |
| Monthly expiry | 5-30 days | 1-4x per month |

### Opportunity Cost Calculation

The opportunity cost of locked capital relative to a risk-free benchmark:

$$
C_{\text{opp}} = \text{Locked Capital} \times r_f \times \frac{\tau}{365}
$$

where $r_f$ is the annualized risk-free rate (currently ~4.5-5.0%) and $\tau$ is the lockup period in days.

| Locked Capital | Duration | Risk-Free Rate | Opportunity Cost |
|---------------|----------|---------------|-----------------|
| \$1,000 | 1 day | 5% | \$0.14 |
| \$1,000 | 7 days | 5% | \$0.96 |
| \$1,000 | 30 days | 5% | \$4.11 |

For short-dated markets (daily/weekly), the opportunity cost is negligible relative to spread capture. For monthly markets, it becomes material and must be factored into the minimum spread threshold.

### Polymarket Holding Rewards Offset

Polymarket pays a **4.00% annualized holding reward** on eligible positions (see [[Polymarket-CLOB-Mechanics#Holding Rewards]]). This partially offsets the opportunity cost:

$$
C_{\text{net\_opp}} = \text{Locked Capital} \times (r_f - r_{\text{holding}}) \times \frac{\tau}{365}
$$

At current rates: $C_{\text{net\_opp}} = \text{Locked Capital} \times (0.05 - 0.04) \times \frac{\tau}{365} = \text{Locked Capital} \times 0.01 \times \frac{\tau}{365}$.

This makes the net opportunity cost roughly 1% annualized -- essentially negligible for daily and weekly markets.

### Effective Capital Utilization

Not all capital is actively deployed at any time. Define the **capital utilization ratio**:

$$
U = \frac{\text{Capital in active positions}}{\text{Total capital allocated to strategy}}
$$

| Utilization | Implication |
|-------------|-------------|
| $U < 0.3$ | Undercapitalized strategy -- too few markets or too conservative sizing |
| $U = 0.3 - 0.6$ | Healthy -- reserves for risk events and new opportunities |
| $U = 0.6 - 0.8$ | Aggressive -- limited buffer for drawdowns |
| $U > 0.8$ | Dangerous -- no reserve for margin calls or sudden opportunities |

Target utilization: **40-60%** of allocated capital, with the remainder as a buffer for:
- Adding to positions when large mispricings appear
- Covering margin on adverse moves (positions losing value require additional capital to maintain quoting)
- Rebalancing across markets

---

## 3. Sharpe Ratio Estimation

### Framework

The Sharpe ratio for a market making strategy:

$$
\text{SR} = \frac{E[R] - r_f}{\sigma_R} \times \sqrt{252}
$$

where $E[R]$ is the average daily return, $r_f$ is the daily risk-free rate, and $\sigma_R$ is the daily return standard deviation.

### Binary Market Making Sharpe Decomposition

Daily returns in binary market making have a characteristic distribution:
- **Most days**: Small positive returns from spread capture (0.5-2% of deployed capital)
- **Occasional days**: Large negative returns from adverse resolution (-5% to -20% of deployed capital on a single market)
- **Distribution**: Positively skewed mean, negative skew on tail events

### Estimated Sharpe Ratios by Strategy

| Strategy | Expected Daily Return | Daily Volatility | Annualized Sharpe |
|----------|---------------------|-----------------|-------------------|
| Pure spread capture (single market) | 0.8% | 3.5% | 3.6 |
| Spread capture (10 diversified markets) | 0.8% | 1.5% | 8.5 |
| Cross-market arbitrage overlay | 0.3% | 0.5% | 9.5 |
| Combined (spread + arb, 10 markets) | 1.0% | 1.8% | 8.8 |

> [!warning] Sharpe Ratio Caveats for Binary Markets
> These estimates are optimistic because:
> 1. **Serial correlation**: Binary market making returns are not independent across days. A position accumulated on Monday may resolve badly on Friday, creating multi-day correlated losses.
> 2. **Fat tails**: The binary resolution creates a distribution with heavier tails than normal. The standard Sharpe formula assumes normality.
> 3. **Small sample bias**: With only 250 trading days per year and potential for clustered losses, Sharpe estimates are noisy with wide confidence intervals.
> 4. **CAIA finding**: Real trading strategies with serial correlation can overestimate the Sharpe ratio by up to 65%.
>
> A more robust metric is the **Sortino ratio** (using downside deviation only) or the **Calmar ratio** (return / max drawdown).

### Comparison with Alternative Strategies

| Strategy | Typical Sharpe | Capital Requirement | Complexity |
|----------|---------------|-------------------|------------|
| Binary MM on Polymarket | 3-8 (estimated) | \$5,000-\$50,000 | High |
| Crypto MM (CEX) | 2-5 | \$50,000-\$500,000 | High |
| DeFi LP (AMM) | 0.5-2 | \$10,000-\$100,000 | Medium |
| Equity options MM | 3-10 | \$1M+ | Very High |
| Statistical arbitrage | 1.5-4 | \$500,000+ | Very High |
| Polymarket directional betting | 0.3-1.5 | \$1,000-\$50,000 | Low |

---

## 4. Capital Allocation Across Markets

### The Allocation Problem

With a fixed capital budget $W$ and $n$ available markets, how to allocate capital $w_i$ to each market?

### Kelly-Criterion-Inspired Allocation

For binary outcomes with edge $e_i$ and probability of winning $p_i$:

$$
f_i^* = \frac{p_i \cdot b_i - (1 - p_i)}{b_i}
$$

where $b_i$ is the "odds" (payout ratio). For market making, the "bet" is more nuanced -- we are not betting on the outcome, but on earning the spread. The effective Kelly fraction for a market making position:

$$
f_{\text{MM}}^* = \frac{E[\text{Spread Revenue}] - E[\text{Adverse Selection Cost}]}{E[\text{Max Loss if Resolution Goes Against}]}
$$

### Practical Allocation Heuristic

Rather than full Kelly (which is too aggressive for binary outcomes), use fractional Kelly:

$$
w_i = \frac{f_i^*}{F} \times W \times \frac{1}{n_{\text{corr}}}
$$

where:
- $F = 3$-$5$ (fractional Kelly denominator -- reduces position size for safety)
- $n_{\text{corr}}$ = effective number of independent bets (adjusted for correlation)

### Allocation Priorities

Rank markets by expected risk-adjusted return:

$$
\text{Score}_i = \frac{E[\text{Daily P\&L}_i]}{\text{Max Loss}_i \times p_{\text{loss},i}} \times \frac{1}{\text{Lockup Duration}_i}
$$

| Factor | Higher Priority | Lower Priority |
|--------|----------------|----------------|
| Mispricing magnitude | Large $\|\alpha\|$ (5+ cents) | Small $\|\alpha\|$ (< 2 cents) |
| Polymarket liquidity | More liquid (easier to exit) | Less liquid (positions get stuck) |
| Time to expiry | Shorter (faster capital turnover) | Longer (capital locked) |
| Underlying volatility | Moderate (spread opportunity) | Extreme (adverse selection risk) |
| Correlation with existing portfolio | Low (diversification) | High (concentration) |

### Example: Allocating \$20,000 Across 5 Markets

| Market | Score | Raw Allocation | Adjusted (Limits) | Deployed |
|--------|-------|---------------|-------------------|----------|
| NVDA > \$120 (daily) | 8.5 | \$5,200 | \$4,000 (cap 20%) | \$4,000 |
| TSLA > \$250 (daily) | 7.2 | \$4,400 | \$4,000 (cap 20%) | \$4,000 |
| AAPL > \$200 (weekly) | 6.0 | \$3,700 | \$3,700 | \$3,700 |
| SPX > 5,500 (weekly) | 5.5 | \$3,400 | \$3,400 | \$3,400 |
| META > \$500 (daily) | 4.8 | \$2,900 | \$2,900 | \$2,900 |
| **Reserve** | -- | -- | -- | **\$2,000** |
| **Total** | | | | **\$20,000** |

Reserve (10%) kept for: unexpected opportunities, drawdown buffer, and rebalancing.

---

## 5. Liquidity Rewards as Revenue Component

### Polymarket Liquidity Rewards Mechanics

Polymarket's liquidity rewards program is a significant revenue component for market makers. Key details from [[Polymarket-CLOB-Mechanics#Liquidity Rewards Program]]:

**Q-Score Formula:**

$$
S(v, s) = \left(\frac{v - s}{v}\right)^2 \times b
$$

where:
- $v$ = max incentive spread (market-specific parameter)
- $s$ = actual spread (distance from midpoint)
- $b$ = order size

Key properties:
- **Quadratic decay**: Orders far from the midpoint score exponentially less
- **Two-sided bonus**: Minimum of bid-score and ask-score is taken, then divided by $c = 3.0$ for single-sided quoters. Two-sided quoting earns ~3x the reward.
- **Sampling**: 10,080 samples per week (once per minute)
- **Minimum payout**: \$1.00

### Maker Rebates (Taker Fee Redistribution)

For markets with taker fees (currently crypto and select sports), 20-25% of taker fees are redistributed to makers proportional to their filled volume:

$$
\text{Rebate}_i = \frac{\text{Your fee-weighted fills}}{\text{Total fee-weighted fills}} \times \text{Rebate Pool}
$$

### Revenue Breakdown Estimation

For a market maker with \$20,000 deployed across 10 markets:

| Revenue Source | Monthly Estimate | Notes |
|---------------|-----------------|-------|
| Spread capture | \$400 - \$1,200 | Core MM revenue; varies with fill rate and mispricing |
| Cross-market alpha | \$100 - \$500 | Directional edge from options-implied pricing |
| Liquidity rewards | \$50 - \$200 | Q-score dependent; requires tight, two-sided quoting |
| Maker rebates | \$0 - \$50 | Only on fee-enabled markets; stock/index markets currently fee-free |
| Holding rewards | \$60 - \$70 | 4% APY on ~\$20,000 |
| **Total** | **\$610 - \$2,020** | **37-121% annualized** |

> [!caution] Realistic Expectations
> The above estimates are pre-loss. Monthly losses from adverse resolution can erase 1-3 months of spread revenue in a single bad event. Professional prediction market makers report that actual net returns are frequently lower than projected, with some LPs reporting negative overall returns offset only by speculation on future POLY token airdrops. Conservative planning should assume 15-40% annualized net returns after losses, with significant month-to-month variance.

---

## 6. Edge Cases and Risk Scenarios

### 6.1 Earnings Announcements

**Impact**: Earnings create massive discontinuous jumps in stock prices, dramatically shifting binary option probabilities.

**Example**: NVDA reports earnings after market close. The stock can move 10-20% overnight.

| Scenario | Binary Impact | Market Maker Risk |
|----------|--------------|-------------------|
| Pre-earnings (market open) | Implied vol elevated, probabilities reflect expected move | Wide spreads needed; models capture earnings via options IV |
| Earnings release (after hours) | Stock gaps; probabilities jump to near 0 or 1 | Cannot update Polymarket quotes during after-hours |
| Post-earnings (next open) | Probabilities already at extremes; thin value in MM | Residual positions resolve based on gap direction |

**Protocol**:
1. **48 hours before earnings**: Increase $\gamma$ (risk aversion) by 2-3x for that underlying
2. **Day of earnings**: Reduce $Q_{\max}$ to 50% of normal. Widen spreads to 2x.
3. **After-hours earnings release**: If holding positions, accept the resolution risk -- no quotes can be updated
4. **Do not market-make daily-expiry contracts that span an earnings announcement** unless the edge is exceptionally large (> 10 cents mispricing)

#### Options-Implied Probability Around Earnings

The options market prices earnings moves via **elevated implied volatility**. For a stock with 5% expected earnings move:

$$
\sigma_{\text{earnings}} = \sigma_{\text{normal}} + \sigma_{\text{event}}
$$

The [[Breeden-Litzenberger-Pipeline]] naturally captures this via the options chain -- the extracted probability already reflects the market's earnings expectations. The risk is that the actual move exceeds the implied move.

### 6.2 Market Halts

**Types of halts**:
- **LULD (Limit Up-Limit Down)**: 5-15 minute pause after extreme moves
- **Market-wide circuit breakers**: 15 minutes (Level 1, 7% drop) to full-day halt (Level 3, 20% drop)
- **News-related halts**: Company-specific, duration varies (minutes to days)

**Impact on Polymarket**:
- Polymarket markets **remain open** during stock halts -- they are on a separate platform
- This creates extreme adverse selection: informed traders who anticipate the post-halt price can pick off stale Polymarket quotes
- The [[Breeden-Litzenberger-Pipeline]] cannot update during halts (no new options data)

**Protocol**:
1. **On halt detection**: Immediately cancel all orders for affected underlying
2. **During halt**: Do not quote. Monitor news for cause of halt.
3. **Post-halt**: Wait 5 minutes after trading resumes. Recalculate fair values. Re-enter with 2x normal spreads.
4. **For resolution**: Verify the market's resolution rules -- some specify "official closing price" which may exclude halted periods

### 6.3 Gap Risk (Overnight and Weekend)

**The problem**: Stock markets close at 4 PM ET and reopen at 9:30 AM ET. During this 17.5-hour window, news can move prices dramatically. Polymarket tokens continue trading (though with minimal volume).

**Risk quantification**: The overnight gap in stock prices is typically:
- **Normal day**: 0.1-0.5% gap
- **After earnings/news**: 2-15% gap
- **After major macro events**: 1-5% gap for indices

For a binary option with strike near the current price, even a 2% gap can shift the probability by 10-30 cents.

**Mitigation strategies**:

| Strategy | Implementation | Effectiveness |
|----------|---------------|--------------|
| Flatten before close | Aggressively reduce inventory in last 30 minutes | High -- eliminates overnight exposure |
| Widen overnight spreads | If quoting overnight, use 3-5x normal spreads | Medium -- may still get picked off on news |
| Avoid overnight exposure | Only trade during market hours (9:30 AM - 4 PM ET) | High -- but misses overnight opportunities |
| Hedge with futures | Use overnight index futures to hedge SPX/NDX positions | Medium -- adds complexity and cost |

### 6.4 Platform Risk

#### Polymarket Outages

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| API downtime | Weekly maintenance (Tuesdays 7 AM ET) | Orders cancelled via heartbeat timeout | Schedule around maintenance; handle HTTP 425 |
| Unexpected outage | Rare but possible | Cannot update or cancel quotes | Rely on heartbeat mechanism (10s timeout) |
| Matching engine delay | During high volume | Stale orders execute at wrong prices | Widen spreads during high-volume events |

#### Oracle/Resolution Failures

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| Disputed resolution | Rare (<1% of markets) | Capital locked for 4-6 days; outcome uncertain | Exit positions before resolution if possible |
| Ambiguous resolution source | Very rare | Market resolves 50-50 (\$0.50 per token) | Read resolution rules carefully before entering |
| Price source discrepancy | Rare | Official close differs from expected source | Verify resolution source in market metadata |

#### Smart Contract Risk

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| CTF Exchange exploit | Very low (audited) | Total loss of deposited funds | Limit total capital on platform; diversify across platforms |
| Polygon chain issues | Very low | Settlement delays | Monitor chain health; have contingency plan |
| USDC.e depeg | Extremely low | Collateral value disruption | Monitor USDC/USDC.e peg |

### 6.5 Correlation Risk: Multiple Positions on Same Underlying

**Scenario**: You are market-making 5 strike levels on NVDA for the same expiry: \$115, \$120, \$125, \$130, \$135.

**The danger**: A sudden 8% drop in NVDA from \$128 to \$117.76 simultaneously:
- Flips \$125 from ITM to OTM (YES drops from ~0.70 to ~0.20)
- Flips \$120 from deep ITM to borderline (YES drops from ~0.85 to ~0.45)
- Pushes \$130 and \$135 to near-zero
- Only \$115 remains safely ITM

If you held net long YES across all 5 strikes, the total loss could be:

| Strike | Position | Pre-Drop Value | Post-Drop Value | Loss |
|--------|----------|---------------|-----------------|------|
| \$115 YES | +50 | \$47.50 | \$44.00 | -\$3.50 |
| \$120 YES | +40 | \$34.00 | \$18.00 | -\$16.00 |
| \$125 YES | +30 | \$21.00 | \$6.00 | -\$15.00 |
| \$130 YES | +20 | \$10.00 | \$1.00 | -\$9.00 |
| \$135 YES | +10 | \$3.50 | \$0.20 | -\$3.30 |
| **Total** | | **\$116.00** | **\$69.20** | **-\$46.80** |

This 40% portfolio drawdown from a single underlying move illustrates why per-underlying concentration limits (see [[Inventory-and-Risk-Management#Risk Budget Hierarchy]]) are essential.

### 6.6 Macro Event Risk

**Events that simultaneously affect all positions**:
- **Fed rate decisions**: Move all equities and indices together
- **Geopolitical shocks**: Market-wide sell-offs
- **Flash crashes**: Sudden liquidity withdrawal across all assets

**Key concern**: During a broad market crash, the correlation across all positions approaches 1.0. Every "close above $K$" YES token loses value simultaneously.

**Mitigation**:
1. **Maximum total capital at risk**: Never deploy more than 25% of total capital across all markets combined
2. **SPX/NDX awareness**: Index positions are naturally correlated with all single-stock positions. Reduce index exposure if heavily exposed to tech stocks.
3. **Tail risk reserve**: Keep 10-20% of capital uninvested as a drawdown buffer
4. **Consider NO positions**: In a portfolio of mostly YES positions, adding some NO positions at deep-ITM strikes provides portfolio-level downside protection (NO tokens on "close above X" gain value when the stock drops)

---

## 7. Monitoring and Reporting Framework

### Daily P&L Attribution

| Component | Calculation |
|-----------|------------|
| Spread revenue | $\sum (\text{sell price} - \text{buy price}) \times \text{matched volume}$ |
| Inventory mark-to-market | $\sum q_i \times (V_i^{\text{now}} - V_i^{\text{entry}})$ |
| Resolution P&L | $\sum q_i \times (\text{payout} - V_i^{\text{entry}})$ for resolved markets |
| Liquidity rewards | Daily USDC payout from Q-score program |
| Holding rewards | Daily accrual at 4% APY |
| Fees paid | Taker fees if any aggressive orders placed |

### Weekly Review Metrics

| Metric | Target | Red Flag |
|--------|--------|----------|
| Net P&L (after all costs) | Positive | Negative for 2+ consecutive weeks |
| Win rate (markets resolved profitably) | > 55% | < 45% |
| Average realized spread / quoted spread | > 60% | < 40% (excessive adverse selection) |
| Capital utilization | 40-60% | > 80% or < 20% |
| Max single-market loss / total capital | < 3% | > 5% |
| Correlation of daily returns | < 0.3 with SPX | > 0.6 (too directional) |

### Performance Benchmarks

Track strategy performance against relevant benchmarks:

| Benchmark | Purpose |
|-----------|---------|
| Risk-free rate (T-bills, ~5% APY) | Minimum hurdle rate |
| Polymarket holding reward (4% APY) | Passive alternative |
| Simple Polymarket LP (wide quotes, no model) | Value of the options-implied edge |
| Equal-weight long YES across all markets | Directional baseline |

---

## 8. Strategy Evolution Path

### Phase 1: Single-Market Validation (Weeks 1-4)

- Market-make a single daily-expiry market (e.g., NVDA > \$X)
- Use probability-based quoting with manual parameter tuning
- Capital: \$2,000-\$5,000
- Goal: Validate the options-implied fair value edge, measure realized spread, assess adverse selection

### Phase 2: Multi-Market Expansion (Weeks 5-12)

- Expand to 5-10 markets across 3-4 underlyings
- Implement AS/GLFT inventory management
- Add VPIN-based adverse selection detection
- Capital: \$10,000-\$20,000
- Goal: Demonstrate scalability, measure portfolio-level risk

### Phase 3: Full Automation (Weeks 13-24)

- Automated quoting across all target tickers and strike levels
- Real-time options chain integration via [[ThetaData-Options-API]]
- Cross-market arbitrage overlay
- Dynamic capital allocation
- Capital: \$20,000-\$50,000
- Goal: Steady-state operation with target 20-40% annualized net return

### Phase 4: Optimization (Ongoing)

- Machine learning for $\kappa$ (order arrival) and adverse selection parameter estimation
- Advanced hedging with vanilla options for large positions
- Expansion to weekly/monthly contracts
- Exploration of cross-platform arbitrage (Polymarket vs. Kalshi)

---

## Related Notes

- [[Core-Market-Making-Strategies]] -- Quoting frameworks (AS, GLFT, multi-market)
- [[Inventory-and-Risk-Management]] -- Hedging, adverse selection, position limits
- [[Polymarket-CLOB-Mechanics]] -- Platform mechanics, fees, resolution
- [[Breeden-Litzenberger-Pipeline]] -- Fair value extraction
- [[Backtesting-Architecture]] -- System design for testing strategies
- [[Performance-Metrics-and-Pitfalls]] -- Backtesting metrics and statistical rigor
