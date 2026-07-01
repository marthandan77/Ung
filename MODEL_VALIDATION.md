# Model Validation

## Phase 1 Status

V7-Lite does not claim fitted machine learning.

Current truth:

- HMM: `NOT_READY`
- HMM probabilities: empty
- Markov state: `NOT_READY`
- Markov transition warning: `NONE`
- GARCH: `FALLBACK_EWMA` after returns exist, otherwise `NOT_READY`

The engine uses observable indicators only:

- VWAP
- ATR
- RSI
- EMA fast/slow
- Bollinger Bands
- realized volatility
- rolling volume ratio
- opening range
- profit per share versus average cost

## Score Meaning

HE, HC, RS, RP, BC, and MQI are observable scores, not trained ML outputs.

- HE: Harvest Expectancy from profit zone, VWAP extension, RSI, volume, trend, and opening range.
- HC: Harvest Confidence from cost-basis protection and price structure.
- RS: Regime Stability proxy from observable trend and volatility percentile.
- RP: Re-entry Probability from prior harvest gap, discount to VWAP, RSI, and rebound.
- BC: Breakout Confidence from opening range, trend, volume, and VWAP distance.
- MQI: Market Quality Index from warmup, volume, and spread.

## Anti-Lookahead Rule

The local monitor only uses bars already received from the data source.

The QuantConnect file should be backtested separately. A clean local Python syntax check is not the same as a QuantConnect backtest.

## Phase 2 Requirements

Real HMM can be added only when:

- `hmmlearn` is actually importable in the target environment.
- The model is fitted on historical features.
- Regime labels are derived from hidden-state statistics.
- Probabilities come from actual model inference.

Real GARCH can be added only when:

- `arch` is actually importable in the target environment.
- The model is fitted on historical returns.
- Forecasts come from the fitted model.

Until then, the engine must say `NOT_READY` or `FALLBACK_EWMA`.
