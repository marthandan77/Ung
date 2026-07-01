from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import os

import streamlit as st

from ung_platform.alerts import TelegramAlerter, TelegramConfig
from ung_platform.alpaca import AlpacaConfig, AlpacaDataClient
from ung_platform.engine import Decision, DecisionEngineV7Lite, EngineConfig, MarketBar
from ung_platform.storage import SQLiteJournal


def secret_or_env(name: str, section: str | None = None, default: str | None = None) -> str | None:
    try:
        if section and section in st.secrets and name in st.secrets[section]:
            return str(st.secrets[section][name])
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


def alpaca_config() -> AlpacaConfig:
    return AlpacaConfig(
        api_key_id=secret_or_env("ALPACA_API_KEY_ID", "alpaca"),
        api_secret_key=secret_or_env("ALPACA_API_SECRET_KEY", "alpaca"),
        feed=secret_or_env("ALPACA_DATA_FEED", "alpaca", "iex") or "iex",
    )


def telegram_config() -> TelegramConfig:
    return TelegramConfig(
        bot_token=secret_or_env("TELEGRAM_BOT_TOKEN", "telegram"),
        chat_id=secret_or_env("TELEGRAM_CHAT_ID", "telegram"),
    )


def new_engine(position_qty: int, average_cost: float, minimum_profit: float) -> DecisionEngineV7Lite:
    return DecisionEngineV7Lite(
        EngineConfig(
            position_qty=position_qty,
            average_cost=average_cost,
            minimum_harvest_profit=minimum_profit,
        )
    )


def demo_bars(avg_cost: float) -> list[MarketBar]:
    bars = []
    base = avg_cost - 0.28
    start = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    for i in range(90):
        wave = math.sin(i / 6) * 0.045
        recovery = i * 0.006
        price = base + recovery + wave
        high = price + 0.025
        low = price - 0.025
        bars.append(
            MarketBar(
                timestamp=start + timedelta(minutes=i),
                open=price - 0.01,
                high=high,
                low=low,
                close=price,
                volume=80_000 + (i % 9) * 9_000,
            )
        )
    return bars


def apply_bar(bar: MarketBar, engine: DecisionEngineV7Lite, db: SQLiteJournal) -> Decision:
    decision = engine.update(bar)
    db.record(decision)
    st.session_state["last_decision"] = decision
    return decision


def reset_engine() -> None:
    st.session_state.pop("engine", None)
    st.session_state.pop("last_decision", None)


st.set_page_config(page_title="UNG V7-Lite", layout="wide")
st.title("UNG Decision Engine V7-Lite")

with st.sidebar:
    position_qty = st.number_input("Position shares", min_value=0, value=30_900, step=100)
    average_cost = st.number_input("Average cost", min_value=0.01, value=11.5453, step=0.0001, format="%.4f")
    minimum_profit = st.number_input("Minimum harvest profit/share", min_value=0.01, value=0.10, step=0.01)
    st.divider()
    alpaca_status = alpaca_config()
    telegram_status = telegram_config()
    st.caption("Alpaca API: ready" if alpaca_status.ready else "Alpaca API: missing keys")
    st.caption("Telegram: ready" if telegram_status.ready else "Telegram: missing keys")
    if st.button("Reset Engine", use_container_width=True):
        reset_engine()

config_key = (int(position_qty), float(average_cost), float(minimum_profit))
if st.session_state.get("config_key") != config_key:
    reset_engine()
    st.session_state["config_key"] = config_key

if "engine" not in st.session_state:
    st.session_state["engine"] = new_engine(*config_key)

engine: DecisionEngineV7Lite = st.session_state["engine"]
db = SQLiteJournal()

left, middle, right = st.columns([1, 1, 1])

with left:
    manual_price = st.number_input("Manual UNG price", min_value=0.01, value=float(average_cost), step=0.01)
    manual_volume = st.number_input("Manual volume", min_value=0, value=100_000, step=10_000)
    if st.button("Add Manual Bar", use_container_width=True):
        bar = MarketBar(
            timestamp=datetime.now(timezone.utc),
            open=manual_price,
            high=manual_price,
            low=manual_price,
            close=manual_price,
            volume=manual_volume,
        )
        apply_bar(bar, engine, db)

with middle:
    if st.button("Load Demo Bars", use_container_width=True):
        last = None
        for bar in demo_bars(float(average_cost)):
            last = apply_bar(bar, engine, db)
        if last:
            st.session_state["last_decision"] = last

with right:
    alpaca_ready = alpaca_config().ready
    st.caption("Alpaca IEX feed: ready" if alpaca_ready else "Alpaca IEX feed: not configured")
    if st.button("Fetch Latest", use_container_width=True):
        try:
            client = AlpacaDataClient(alpaca_config())
            seeded = engine.config.warmup_bars - len(engine.closes)
            if seeded > 0:
                for seed_bar in client.recent_bars(limit=max(60, seeded + 20)):
                    engine.update(seed_bar, emit_alerts=False)
            apply_bar(client.latest_bar(), engine, db)
        except Exception as exc:
            st.error(str(exc))

decision: Decision | None = st.session_state.get("last_decision")

if decision is None:
    st.info("Add a manual bar, load demo bars, or configure Alpaca and fetch latest.")
else:
    s = decision.snapshot
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Signal", decision.state)
    c2.metric("Price", f"{s.price:.2f}")
    c3.metric("Profit/share", f"{s.profit_per_share:.2f}")
    c4.metric("HE", f"{s.he:.1f}")
    c5.metric("MQI", f"{s.mqi:.1f}")

    st.subheader("Decision")
    st.write(decision.trigger_reason)
    st.code(decision.alert_text or engine.format_alert(decision), language="text")

    if st.button("Send Telegram Test", use_container_width=True):
        try:
            sent = TelegramAlerter(telegram_config()).send(decision.alert_text or engine.format_alert(decision))
            st.success("Telegram sent." if sent else "Telegram dry run printed locally.")
        except Exception as exc:
            st.error(str(exc))

    rows = [
        ("VWAP", f"{s.vwap:.2f}"),
        ("ATR", f"{s.atr:.3f}"),
        ("RSI", f"{s.rsi:.1f}"),
        ("Volume ratio", f"{s.volume_ratio:.2f}"),
        ("Opening range", s.opening_range_status),
        ("Model status", s.model_status),
        ("GARCH status", s.garch_status),
        ("EV ranking", s.ev_ranking),
    ]
    st.dataframe(rows, hide_index=True, use_container_width=True)

st.subheader("Journal")
st.dataframe(db.latest_journal(75), hide_index=True, use_container_width=True)

st.subheader("Alerts")
st.dataframe(db.latest_alerts(25), hide_index=True, use_container_width=True)
