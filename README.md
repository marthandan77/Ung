# UNG Decision Engine V8 RTIS

V8 RTIS is a signal-only UNG forecast and alert platform. It does not place live orders and it does not short UNG.

RTIS means Round-Trip Intelligence System. The engine asks one practical question:

```text
Is SELL -> WAIT -> BUYBACK expected to beat HOLD?
```

The local Streamlit GUI uses the same V8 RTIS state vocabulary as the QuantConnect-style `main.py` engine.

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Configure market data with Alpaca environment variables or Streamlit secrets:

```text
ALPACA_API_KEY_ID
ALPACA_API_SECRET_KEY
ALPACA_DATA_FEED=iex
```

Optional alert delivery settings:

```text
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
WHATSAPP_WEBHOOK_URL
SMTP_HOST
SMTP_PORT=587
SMTP_USERNAME
SMTP_PASSWORD
SMTP_FROM
```

The GUI has high-quality alert prompt boxes for Telegram bot token, Telegram chat ID, WhatsApp phone or ID, WhatsApp webhook URL, and Email ID. These are saved locally in SQLite and used for forecast alerts.

## Dashboard Workflow

- The top GitHub box is removed.
- The heart button is centered near the top of the dashboard.
- The dashboard includes a live UNG TradingView chart.
- Click `Fetch Latest Forecast` to pull real UNG market data from Alpaca.
- Manual bar entry and demo bars are removed.
- The app records every decision in the journal.
- The app creates one `OFFICIAL` forecast per US trading session.
- Material intraday changes become `Update A`, `Update B`, or `Update C`.
- Forecast outcomes are filled at `+5m`, `+15m`, `+30m`, and `+60m` as later market bars arrive.
- The scorecard reviews hit rate and average return by horizon.
- Adaptive tuning starts only after at least 10 completed forecast reviews.
- The alert contact panel includes a test-alert button for Telegram, WhatsApp, and Email delivery checks.

## V8 States

- `HOLD`
- `SELL_WATCH`
- `SELL_READY`
- `SOLD_WAIT`
- `BUYBACK_WATCH`
- `BUYBACK_READY`
- `WAIT`
- `PROTECT`

`SELL_READY` means the engine believes the full round trip has better expected value than holding. It still does not place an order.

`BUYBACK_READY` means the virtual sell has enough discount and support quality for a long-only rebuy signal. It still does not place an order.

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
- profit per share is at least the configured minimum harvest profit
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

- HMM is `READY` only when `hmmlearn` imports and a real GaussianHMM is fitted from actual feature rows.
- Markov is `READY` only when it is built from the actual HMM regime sequence.
- GARCH is `READY` only when the real `arch` package fits a GARCH(1,1) model on actual returns.
- If `arch` is unavailable, volatility forecasting is labeled `FALLBACK_EWMA`.
- EWMA is never called GARCH.

## Files

- `app.py`: Streamlit dashboard, alert contact prompts, live chart, forecast workflow, easter egg.
- `ung_platform/charts.py`: TradingView UNG chart embed helper.
- `ung_platform/engine.py`: local V8 RTIS forecast engine.
- `ung_platform/storage.py`: SQLite journal, official forecast ledger, scorebook, tuning runs.
- `ung_platform/alerts.py`: Telegram, WhatsApp webhook, and email alert delivery.
- `main.py`: QuantConnect-style V8 RTIS signal-only engine.
- `MODEL_VALIDATION.md`: model validation and anti-fake-ML rules.
- `.github/workflows/ci.yml`: Python test workflow.

## Tests

```bash
pytest -q
```
