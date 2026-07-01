# Model Validation

## V8 RTIS Status

V8 RTIS is signal-only. It does not place live orders and does not short UNG.

The model layer follows one strict rule:

```text
If the model is not actually fitted from real data, the status must say NOT_READY.
```

## Real HMM Rule

HMM can be `READY` only when all of these are true:

- `hmmlearn` is importable in the target environment.
- `GaussianHMM` is fitted on actual feature rows.
- Feature rows come from bars already received.
- The current bar is not used to fit the model before the current signal.
- Probabilities come from real HMM inference.

HMM inputs:

- log return
- rolling volatility
- volume ratio
- VWAP distance
- RSI normalized
- ATR percentage

HMM outputs:

- `regime_state_id`
- `regime_probabilities`
- `regime_label`
- `model_status`
- `last_fit_time`

If the real fit or inference is unavailable, V8 outputs:

```text
model_status = NOT_READY
regime_probabilities = {}
regime_label = NOT_READY
```

No fake bull, sideways, or bear probabilities are generated.

## Markov Rule

Markov can be `READY` only after HMM is `READY` and an actual HMM regime sequence
exists.

The transition matrix is built only from observed HMM regime-state transitions.
No HMM means no Markov.

If HMM is not ready, Markov outputs:

```text
markov_status = NOT_READY
transition_warning = NONE
```

## GARCH Rule

GARCH can be `READY` only when the `arch` package is importable and a real
GARCH(1,1) model is fitted on actual returns.

If `arch` is unavailable or the fit fails, V8 uses EWMA volatility and labels it:

```text
garch_status = FALLBACK_EWMA
```

EWMA is not called GARCH.

## Package Discipline

The local/mobile app installs:

- `numpy`
- `hmmlearn`
- `arch`

QuantConnect may not include these packages. The QuantConnect file therefore
keeps optional imports and explicit fallbacks so unsupported packages do not
cause fake outputs.

## Observable Indicators

V8 uses these observable market inputs:

- VWAP
- ATR
- RSI
- EMA fast and slow slope
- Bollinger position
- volume ratio
- opening range status
- realized volatility
- profit per share versus average cost
- bid, ask, and spread when available

QuantConnect indicators are checked with `IsReady` before the engine emits
decisions.

## RTIS Metrics

- `RTE`: Round Trip Expectancy
- `HE`: Harvest Expectancy
- `RP`: Re-entry Probability
- `MUR`: Missed Upside Risk
- `BC`: Breakout Confidence
- `MQI`: Market Quality Index
- `RS`: Regime Stability
- `hold_ev`: Hold expected value
- `sell_buyback_ev`: Sell->Buyback expected value

The primary ranking is:

```text
SELL->BUYBACK if sell_buyback_ev > hold_ev
otherwise HOLD
```

## Forecast-Vs-Actual Ledger

The local/mobile dashboard records every decision as a forecast row. Later bars
close that row at `+5m`, `+15m`, `+30m`, and `+60m`.

For each horizon the ledger stores:

- actual price
- actual return
- hit/fail result

The hit rule is deliberately simple:

- harvest/sell/protect signals expect lower or flat future price
- buy/rebuy/accumulate signals expect higher or flat future price
- hold/wait signals expect movement to stay inside the volatility band

This ledger is for parameter tuning and model validation. It is not a trade
execution engine.

## Anti-Lookahead Discipline

- Backtest dates are 2024-01-01 through 2026-06-15.
- 2024 is the training period.
- 2025 is the validation period.
- 2026 is the walk-forward test period.
- Current-bar features are stored only after the current decision.
- The engine never uses future bars for current decisions.

Local syntax validation is not the same as a full QuantConnect backtest. The
file is written in LEAN style so it can be tested on the QuantConnect platform.
