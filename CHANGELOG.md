# Changelog

## 2026-07-02

- Automated WhatsApp alert setup by normalizing phone numbers and showing the webhook payload preview in the GUI.
- Added tests for WhatsApp alert ID normalization and webhook payload generation.
- Removed the GitHub dashboard button and moved the private heart button to the top middle of the GUI.
- Added a live UNG TradingView chart to the dashboard.
- Made Telegram, WhatsApp, and Email alert contact fields clearer for high-quality forecast alert delivery.
- Added a dashboard test-alert button for Telegram, WhatsApp, and Email delivery checks.
- Aligned the local Streamlit engine with the V8 RTIS state vocabulary: `HOLD`, `SELL_WATCH`, `SELL_READY`, `SOLD_WAIT`, `BUYBACK_WATCH`, `BUYBACK_READY`, `WAIT`, and `PROTECT`.
- Removed the local V7-style state drift from the dashboard path.
- Reworked SQLite storage so every decision is journaled, but only one official forecast is created per US session.
- Added material intraday forecast updates as `Update A`, `Update B`, and `Update C`.
- Added forecast review outcomes at `+5m`, `+15m`, `+30m`, and `+60m` for the official/update ledger.
- Added adaptive tuning records from the completed scorebook after at least 10 reviewed forecasts.
- Added GUI prompt boxes for Telegram, WhatsApp, and Email alert destinations.
- Added Telegram, WhatsApp webhook, and SMTP email delivery plumbing.
- Removed `Add Manual Bar` and `Load Demo Bars` from the dashboard flow.
- Added tests for V8 state vocabulary, duplicate official forecast prevention, and tuning lockout.
- Added GitHub Actions CI and `.gitignore` for local runtime files.

## 2026-07-01

- Added forecast-vs-actual ledger with +5m, +15m, +30m, and +60m outcomes.
- Added dashboard forecast scorecard for hit rate and average return review.
- Added real optional `hmmlearn` GaussianHMM support to the local/mobile engine.
- Added Markov transition probabilities from actual inferred HMM state history.
- Added real optional `arch` GARCH(1,1) support with honest EWMA fallback.
- Added `numpy`, `hmmlearn`, and `arch` to `requirements.txt`.
- Updated the dashboard to show HMM, Markov, and GARCH outputs.
- Upgraded `main.py` to UNG Decision Engine V8 RTIS.
- Added signal-only Round-Trip Intelligence System states:
  `HOLD`, `SELL_WATCH`, `SELL_READY`, `SOLD_WAIT`, `BUYBACK_WATCH`,
  `BUYBACK_READY`, `WAIT`, and `PROTECT`.
- Added explicit Hold EV versus Sell->Buyback EV ranking.
- Added V8 metrics: `RTE`, `HE`, `RP`, `MUR`, `BC`, `MQI`, and `RS`.
- Added anti-fake-ML handling for HMM, Markov, and GARCH/EWMA fallback.
- Added full decision journal fields and meaningful state-change alerts.
- Kept the engine long-only, signal-only, and no-live-orders.
