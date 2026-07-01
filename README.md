# UNG Decision Engine V8 RTIS

V8 RTIS is a signal-only QuantConnect decision engine for UNG.

RTIS means Round-Trip Intelligence System. The engine does not try to sell the
exact top or buy the exact bottom. It asks one simple question:

```text
Is SELL -> WAIT -> BUYBACK expected to beat HOLD?
```

No live orders are placed in V8. It is long-only and alert-first.

## Core Setup

- Asset: UNG
- Resolution: minute
- Backtest window: 2024-01-01 to 2026-06-15
- Position assumption: 30,900 shares
- Average cost assumption: 11.5453
- Minimum harvest profit: 0.10 per share
- Trading style: long-only intraday volatility harvest
- Execution mode: signal-only

## V8 States

- `HOLD`
- `SELL_WATCH`
- `SELL_READY`
- `SOLD_WAIT`
- `BUYBACK_WATCH`
- `BUYBACK_READY`
- `WAIT`
- `PROTECT`

`SELL_READY` means the engine believes the full round trip has better expected
value than holding. It still does not place an order.

`BUYBACK_READY` means the virtual sell has enough discount and support quality
for a long-only rebuy signal. It still does not place an order.

## Round-Trip Equation

```text
Round Trip EV =
Harvest Profit
+ Expected Buyback Discount
- Missed Upside Risk
- Re-entry Failure Risk
```

The engine compares:

- `hold_ev`
- `sell_buyback_ev`
- `RTE`, which is `sell_buyback_ev - hold_ev`

## SELL_READY Rules

V8 only allows `SELL_READY` when all of these are true:

- position quantity is above zero
- profit per share is at least 0.10
- harvest zone is reached
- missed upside risk is below the threshold
- re-entry probability is acceptable
- Sell->Buyback EV is greater than Hold EV
- market quality is acceptable

## BUYBACK_READY Rules

V8 only allows `BUYBACK_READY` when all of these are true:

- position quantity is zero in the virtual RTIS state
- last sell price exists
- current price is at least the dynamic rebuy gap below last sell price
- price is near VWAP, support, or ATR pullback zone
- bearish continuation risk is not dominant
- MQI is acceptable

## Metrics

- `RTE`: Round Trip Expectancy
- `HE`: Harvest Expectancy
- `RP`: Re-entry Probability
- `MUR`: Missed Upside Risk
- `BC`: Breakout Confidence
- `MQI`: Market Quality Index
- `RS`: Regime Stability
- `hold_ev`: Hold expected value
- `sell_buyback_ev`: Sell->Buyback expected value

## ML Truth Rules

V8 does not create fake ML probabilities.

- HMM is `NOT_READY` unless `hmmlearn` is importable and a real GaussianHMM is
  fitted from actual feature rows.
- Markov is `NOT_READY` unless it is built from the actual HMM regime sequence.
- GARCH uses the real `arch` package only when available.
- If `arch` is unavailable, volatility forecasting is labeled
  `FALLBACK_EWMA`.
- EWMA is never called GARCH.

## HMM Inputs

When a real HMM is available, V8 fits on these actual minute-bar features:

- log return
- rolling volatility
- volume ratio
- VWAP distance
- RSI normalized
- ATR percentage

The HMM output includes:

- `regime_state_id`
- `regime_probabilities`
- `regime_label`
- `model_status`
- `last_fit_time`

## Training Discipline

- Train on 2024 data.
- Validate on 2025 data.
- Walk-forward test on 2026 data.
- The current bar is stored for future fitting only after the current decision is
  made.
- No future bars are used for the current signal.

## Example Alert

```text
UNG V8 ALERT | events=SELL_WATCH->SELL_READY;harvest zone reached |
price=12.14 | bid=12.13 ask=12.14 |
position=30900 avg_cost=11.5453 |
signal=SELL_READY |
reason=round-trip EV beats hold EV and re-entry odds are acceptable |
regime=NOT_READY probs={} |
markov=NONE |
key_level=upper Bollinger |
RTE=0.086 HE=83.4 RP=68.2 MUR=0.091 MQI=82.0 |
EV=SELL_BUYBACK |
logic=long-only signal: harvest only when SELL->WAIT->BUYBACK EV beats HOLD EV; no shorting; no live order
```

## Files

- `main.py`: QuantConnect V8 RTIS signal-only engine
- `MODEL_VALIDATION.md`: model validation and anti-fake-ML rules
- `CHANGELOG.md`: change history

The local Streamlit mobile dashboard files remain in the repository, but V8 RTIS
is implemented first in `main.py` for QuantConnect-style validation.
