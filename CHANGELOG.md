# Changelog

## 2026-07-01

- Upgraded `main.py` to UNG Decision Engine V8 RTIS.
- Added signal-only Round-Trip Intelligence System states:
  `HOLD`, `SELL_WATCH`, `SELL_READY`, `SOLD_WAIT`, `BUYBACK_WATCH`,
  `BUYBACK_READY`, `WAIT`, and `PROTECT`.
- Added explicit Hold EV versus Sell->Buyback EV ranking.
- Added V8 metrics: `RTE`, `HE`, `RP`, `MUR`, `BC`, `MQI`, and `RS`.
- Added anti-fake-ML handling for HMM, Markov, and GARCH/EWMA fallback.
- Added full decision journal fields and meaningful state-change alerts.
- Kept the engine long-only, signal-only, and no-live-orders.
