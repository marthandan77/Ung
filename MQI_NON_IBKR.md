# Non-IBKR MQI V1

MQI is a market-quality score for UNG. It is not a directional forecast and not an execution feed.

## Free source hierarchy

1. Yahoo Finance public chart endpoint — primary free fallback.
2. Stooq public CSV — independent secondary fallback.
3. Optional second Henry Hub contract — enables term-structure quality.
4. IBKR remains separate and can remain the preferred execution/account source.

No API key is required by the built-in Yahoo or Stooq adapters. Availability and terms of public feeds can change, so source health and freshness are explicitly scored.

## MQI composition

| Component | Weight | Purpose |
|---|---:|---|
| Liquidity | 25% | Volume availability and recent participation |
| Volatility quality | 20% | Penalizes unusually unstable realized volatility |
| Volume-price quality | 20% | Checks whether price movement has volume confirmation |
| Cross-market coherence | 20% | UNG/natural-gas-futures coherence; optionally blended with term structure |
| Trend structure | 10% | Structural consistency of natural-gas market |
| Data quality | 5% | Freshness of observations |

Output is 0–100 plus a separate 0–1 confidence score.

## Governance

- MQI does not create orders.
- MQI does not override cost-basis protection.
- Missing data is not treated as neutral; confidence falls or MQI becomes unavailable.
- Free-feed data is explicitly marked as analytical/degraded.
- Term structure is optional because free sources may not expose a reliable second-contract series.
- New providers can implement the FreeMarketDataSource protocol without changing the scoring engine.

## Suggested interpretation

- MQI >= 70: strong quality confirmation.
- 55–69.99: usable but not strong.
- 40–54.99: degraded; do not treat as confirmation.
- <40: weak market quality.
- confidence <0.70: degraded-confidence output.
- UNAVAILABLE: no manufactured score.

These are quality gates, not standalone buy/sell rules.
