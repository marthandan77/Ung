# Mobile Deployment Plan

Goal: view the UNG engine from a phone.

## Best Free Path

Use Streamlit Community Cloud.

Why:

- Free Streamlit hosting.
- Public mobile URL.
- Works with this Python dashboard.
- Keeps Alpaca and Telegram keys in private app secrets.

## Required Accounts

1. GitHub account.
2. Streamlit Community Cloud account.
3. Alpaca account for free IEX market data.
4. Telegram bot only if phone alerts are wanted.

## Deploy Steps

1. Push this project to GitHub.
2. Open Streamlit Community Cloud.
3. Create a new app from the GitHub repo.
4. Set main file path to:

```text
app.py
```

5. In Advanced settings, paste secrets using this shape:

```toml
[alpaca]
ALPACA_API_KEY_ID = "your_key"
ALPACA_API_SECRET_KEY = "your_secret"
ALPACA_DATA_FEED = "iex"

[telegram]
TELEGRAM_BOT_TOKEN = "optional_bot_token"
TELEGRAM_CHAT_ID = "optional_chat_id"
```

6. Deploy.
7. Open the Streamlit URL on your phone.

## Important Truth

The free Alpaca feed is IEX. It is enough for a free first live engine, but it is not full SIP consolidated tape.

The webpage is live when a browser session is open. For always-on alerts, run `run_bot.py` locally or add a later always-on worker.
