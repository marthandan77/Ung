# UNG Decision Engine V7-Lite

Signal-only UNG alert platform.

This project keeps Phase 1 simple:

- Front end: Streamlit dashboard
- Back end: Python decision engine
- Data: Alpaca free IEX feed when credentials are supplied
- Journal: SQLite
- Alerts: Telegram Bot API
- QuantConnect: separate backtest validation file

No live orders are placed in this version.

## Free Stack Truth

- Alpaca free market data is IEX-only, not full SIP consolidated tape.
- QuantConnect free plan is useful for backtesting, but free live Telegram/email/webhook notifications are not the base assumption.
- Telegram alerts are sent directly from this local Python monitor when configured.

## Setup

Install packages if `.venv` is not already present:

```powershell
C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run everything locally:

```powershell
.\start_engine.ps1
```

The engine dashboard opens at:

```text
http://localhost:8501
```

Keep the PowerShell window open while using the dashboard.

For a phone-viewable public page, use [DEPLOY_MOBILE.md](DEPLOY_MOBILE.md).

Run the dashboard directly:

```powershell
.\start_dashboard.ps1
```

Run one live data check:

```powershell
.\start_monitor_once.ps1
```

Run the monitor:

```powershell
.\.venv\Scripts\python.exe run_bot.py --poll-seconds 60
```

## Optional Live Data

Set these environment variables for Alpaca:

```powershell
$env:ALPACA_API_KEY_ID="your_key"
$env:ALPACA_API_SECRET_KEY="your_secret"
$env:ALPACA_DATA_FEED="iex"
```

## Optional Telegram Alerts

Set these environment variables:

```powershell
$env:TELEGRAM_BOT_TOKEN="your_bot_token"
$env:TELEGRAM_CHAT_ID="your_chat_id"
```

If Telegram is not configured, alerts print as dry runs.

## Files

- `app.py`: dashboard
- `run_bot.py`: local alert monitor
- `ung_platform/engine.py`: V7-Lite decision engine
- `ung_platform/alpaca.py`: Alpaca IEX data adapter
- `ung_platform/alerts.py`: Telegram alert sender
- `ung_platform/storage.py`: SQLite journal
- `quantconnect_v7_lite.py`: signal-only QuantConnect backtest file

## Phase 1 Rules

- UNG only
- Long-only
- Alert-first
- No shorting
- No margin logic
- No order placement
- No fake HMM probabilities
- GARCH status is `FALLBACK_EWMA` unless a real package is added later

## Phase 2 TODO

- Add real HMM only if the package is available and fitted on historical features.
- Add true Markov transitions only after HMM state history exists.
- Add live broker integration only after signal-only behavior is proven.
- Add deployment only after the free local monitor proves reliable.
