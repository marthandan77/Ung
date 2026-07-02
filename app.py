from __future__ import annotations

import json
import os

import streamlit as st
import streamlit.components.v1 as components

from ung_platform.alerts import AlertDeliveryConfig, MultiChannelAlerter, normalize_whatsapp_id, whatsapp_payload
from ung_platform.alpaca import AlpacaConfig, AlpacaDataClient
from ung_platform.charts import tradingview_ung_chart_html
from ung_platform.engine import Decision, DecisionEngineV8RTIS, EngineConfig
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


def tuned_engine_config(db: SQLiteJournal, position_qty: int, average_cost: float, learning_enabled: bool) -> EngineConfig:
    base = EngineConfig(position_qty=position_qty, average_cost=average_cost)
    if not learning_enabled:
        return base
    params = db.latest_tuned_parameters(base)
    return EngineConfig(
        position_qty=position_qty,
        average_cost=average_cost,
        minimum_harvest_profit=float(params["minimum_harvest_profit"]),
        reentry_min_probability=float(params["reentry_min_probability"]),
        mur_max_dollars=float(params["mur_max_dollars"]),
        mqi_min=float(params["mqi_min"]),
    )


def new_engine(config: EngineConfig) -> DecisionEngineV8RTIS:
    return DecisionEngineV8RTIS(config)


def reset_engine() -> None:
    st.session_state.pop("engine", None)
    st.session_state.pop("last_decision", None)
    st.session_state.pop("forecast_status", None)
    st.session_state.pop("delivery_status", None)


def decision_alert_text(decision: Decision, engine: DecisionEngineV8RTIS) -> str:
    return decision.alert_text or engine.format_alert(decision)


def send_forecast_alert(
    db: SQLiteJournal,
    decision: Decision,
    engine: DecisionEngineV8RTIS,
    forecast_status: dict[str, object],
) -> dict[str, str]:
    if not forecast_status.get("created"):
        return {}
    contacts = db.alert_contacts()
    alerter = MultiChannelAlerter(AlertDeliveryConfig.from_contacts(contacts))
    label = forecast_status.get("update_label") or forecast_status.get("forecast_kind") or "FORECAST"
    message = f"UNG V8 RTIS {label} #{forecast_status.get('forecast_id')}\n\n" + decision_alert_text(decision, engine)
    return alerter.send(message)


def test_alert_message() -> str:
    return "\n".join(
        [
            "UNG Forecast Machine test alert",
            "Signal-only dashboard alert channel check.",
            "Future high-quality alerts include signal, price, reason, RTE, HE, RP, MQI, and EV ranking.",
        ]
    )


st.set_page_config(page_title="UNG V8 RTIS", layout="wide")

db = SQLiteJournal()

st.title("UNG Decision Engine V8 RTIS")
st.caption("Forecast and alert machine. Signal-only. No live orders.")

heart_left, heart_center, heart_right = st.columns([2.4, 1.2, 2.4])
with heart_center:
    if "heart_clicks" not in st.session_state:
        st.session_state["heart_clicks"] = 0
    if st.button("♥ Heart", key="heart_button", use_container_width=True, help="Private heart"):
        st.session_state["heart_clicks"] += 1

heart_clicks = st.session_state.get("heart_clicks", 0)
if heart_clicks >= 10:
    st.markdown(
        "<div style='font-family: \"Brush Script MT\", \"Segoe Script\", cursive; font-size: 36px; color: #c2185b; text-align: center; margin: 0.25rem 0 1rem;'>Juliany i love you</div>",
        unsafe_allow_html=True,
    )
elif heart_clicks >= 3:
    st.markdown(
        "<div style='font-family: \"Brush Script MT\", \"Segoe Script\", cursive; font-size: 36px; color: #c2185b; text-align: center; margin: 0.25rem 0 1rem;'>Juliany i miss you</div>",
        unsafe_allow_html=True,
    )

with st.sidebar:
    st.header("Engine setup")
    position_qty = st.number_input("Position shares", min_value=0, value=30_900, step=100)
    average_cost = st.number_input("Average cost", min_value=0.01, value=11.5453, step=0.0001, format="%.4f")
    learning_enabled = st.toggle("Use adaptive tuning", value=True)
    st.caption("No manual bars and no demo bars. Forecasts come from configured market data.")

    st.divider()
    st.header("High-quality alert contacts")
    saved_contacts = db.alert_contacts()
    delivery_config = AlertDeliveryConfig.from_contacts(saved_contacts)
    st.caption(
        " | ".join(
            [
                "Telegram ready" if delivery_config.telegram_bot_token and delivery_config.telegram_chat_id else "Telegram waiting",
                "WhatsApp ready" if delivery_config.whatsapp_id and delivery_config.whatsapp_webhook_url else "WhatsApp waiting",
                "Email ready" if delivery_config.email_id and os.getenv("SMTP_HOST") else "Email waiting",
            ]
        )
    )
    with st.form("alert_contacts_form"):
        telegram_bot_token = st.text_input("Telegram bot token", value=saved_contacts.get("telegram_bot_token", ""), type="password")
        telegram_chat_id = st.text_input("Telegram chat ID", value=saved_contacts.get("telegram_chat_id", ""))
        whatsapp_id = st.text_input("WhatsApp number / ID", value=saved_contacts.get("whatsapp_id", ""), placeholder="+6591234567")
        normalized_whatsapp_id = normalize_whatsapp_id(whatsapp_id)
        if normalized_whatsapp_id:
            st.caption(f"Automated WhatsApp send-to: {normalized_whatsapp_id}")
        whatsapp_webhook_url = st.text_input("WhatsApp webhook URL", value=saved_contacts.get("whatsapp_webhook_url", ""), type="password", placeholder="https://hook.provider.com/...")
        email_id = st.text_input("Email ID", value=saved_contacts.get("email_id", ""))
        if st.form_submit_button("Save alert IDs", use_container_width=True):
            db.save_alert_contacts(
                {
                    "telegram_bot_token": telegram_bot_token,
                    "telegram_chat_id": telegram_chat_id,
                    "whatsapp_id": normalized_whatsapp_id,
                    "whatsapp_webhook_url": whatsapp_webhook_url,
                    "email_id": email_id,
                }
            )
            st.success("Alert IDs saved locally.")

    preview_contacts = db.alert_contacts()
    preview_config = AlertDeliveryConfig.from_contacts(preview_contacts)
    if preview_config.whatsapp_id:
        st.caption("WhatsApp payload: " + json.dumps(whatsapp_payload(preview_config.whatsapp_id, "UNG test alert"), sort_keys=True))

    if st.button("Send Test Alert", use_container_width=True):
        status = MultiChannelAlerter(AlertDeliveryConfig.from_contacts(db.alert_contacts())).send(test_alert_message())
        st.session_state["test_alert_status"] = status
    if st.session_state.get("test_alert_status"):
        st.caption("Test delivery: " + json.dumps(st.session_state["test_alert_status"], sort_keys=True))

    st.divider()
    st.header("Data status")
    alpaca_status = alpaca_config()
    st.caption("Alpaca API: ready" if alpaca_status.ready else "Alpaca API: missing keys")
    st.caption("Email SMTP: ready" if os.getenv("SMTP_HOST") else "Email SMTP: waiting for SMTP_HOST")
    if st.button("Reset Engine", use_container_width=True):
        reset_engine()

config = tuned_engine_config(db, int(position_qty), float(average_cost), bool(learning_enabled))
config_key = (
    int(position_qty),
    float(average_cost),
    bool(learning_enabled),
    config.minimum_harvest_profit,
    config.reentry_min_probability,
    config.mur_max_dollars,
    config.mqi_min,
)
if st.session_state.get("config_key") != config_key:
    reset_engine()
    st.session_state["config_key"] = config_key

if "engine" not in st.session_state:
    st.session_state["engine"] = new_engine(config)

engine: DecisionEngineV8RTIS = st.session_state["engine"]

fetch_col, tune_col, status_col = st.columns([1, 1, 2])
with fetch_col:
    fetch_latest = st.button("Fetch Latest Forecast", type="primary", use_container_width=True)
with tune_col:
    run_tune = st.button("Run Adaptive Tuning", use_container_width=True)
with status_col:
    st.caption("Official forecast: one per US session. Intraday material changes become Update A/B/C.")

st.subheader("UNG Live Chart")
components.html(tradingview_ung_chart_html(), height=560, scrolling=False)

if run_tune:
    tuning = db.tune_from_scorebook(config)
    st.session_state["latest_tuning"] = tuning
    if tuning.get("changed"):
        st.success(tuning["reason"])
        reset_engine()
    else:
        st.info(tuning["reason"])

if fetch_latest:
    try:
        client = AlpacaDataClient(alpaca_config())
        seed_needed = max(engine.config.hmm_min_samples + 40, engine.config.garch_min_returns + 40, engine.config.warmup_bars + 20)
        for seed_bar in client.recent_bars(limit=seed_needed):
            engine.update(seed_bar, emit_alerts=False)
        decision = engine.update(client.latest_bar())
        delivery_status: dict[str, str] = {}
        if decision.alert:
            delivery_status = MultiChannelAlerter(AlertDeliveryConfig.from_contacts(db.alert_contacts())).send(decision_alert_text(decision, engine))
        journal_id = db.record(decision, delivery_status)
        forecast_status = db.record_session_forecast(decision, journal_id=journal_id)
        if forecast_status.get("created"):
            delivery_status = send_forecast_alert(db, decision, engine, forecast_status) or delivery_status
        st.session_state["last_decision"] = decision
        st.session_state["forecast_status"] = forecast_status
        st.session_state["delivery_status"] = delivery_status
        st.success("Latest UNG forecast processed.")
    except Exception as exc:
        st.error(str(exc))

decision: Decision | None = st.session_state.get("last_decision")

if decision is None:
    st.info("Configure Alpaca data keys, then fetch the latest forecast. Manual and demo bars have been removed.")
else:
    s = decision.snapshot
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Signal", decision.state)
    c2.metric("Price", f"{s.price:.2f}")
    c3.metric("RTE", f"{s.rte:.3f}")
    c4.metric("HE / RP", f"{s.he:.1f} / {s.rp:.1f}")
    c5.metric("MQI", f"{s.mqi:.1f}")
    c6.metric("Model", s.model_status)

    forecast_status = st.session_state.get("forecast_status")
    if forecast_status:
        if forecast_status.get("created"):
            st.success(f"{forecast_status.get('update_label') or forecast_status.get('forecast_kind')} forecast #{forecast_status.get('forecast_id')} stored.")
        else:
            st.warning(str(forecast_status.get("reason")))

    delivery_status = st.session_state.get("delivery_status") or {}
    if delivery_status:
        st.caption("Alert delivery: " + json.dumps(delivery_status, sort_keys=True))

    st.subheader("Decision")
    st.write(decision.trigger_reason)
    st.code(decision_alert_text(decision, engine), language="text")

    rows = [
        ("VWAP", f"{s.vwap:.2f}"),
        ("ATR", f"{s.atr:.3f}"),
        ("RSI", f"{s.rsi:.1f}"),
        ("Volume ratio", f"{s.volume_ratio:.2f}"),
        ("Opening range", s.opening_range_status),
        ("HMM", s.model_status),
        ("Regime", s.regime_label),
        ("Markov", s.transition_warning),
        ("GARCH", s.garch_status),
        ("Vol forecast", f"{s.next_period_volatility_forecast:.5f}"),
        ("RTE / HE / RP / MQI", f"{s.rte:.3f} / {s.he:.1f} / {s.rp:.1f} / {s.mqi:.1f}"),
        ("EV ranking", s.ev_ranking),
    ]
    st.dataframe(rows, hide_index=True, use_container_width=True)

st.subheader("Adaptive Tuning")
latest_tuning = st.session_state.get("latest_tuning") or db.latest_tuning()
params = db.latest_tuned_parameters(config)
st.write(
    {
        "learning_enabled": learning_enabled,
        "minimum_harvest_profit": params["minimum_harvest_profit"],
        "reentry_min_probability": params["reentry_min_probability"],
        "mur_max_dollars": params["mur_max_dollars"],
        "mqi_min": params["mqi_min"],
    }
)
if latest_tuning:
    st.caption(str(latest_tuning.get("reason")))

st.subheader("Forecast Scorecard")
st.caption("Hit rate compares official/update forecasts against actual price movement after 5, 15, 30, and 60 minutes.")
try:
    scorecard = db.forecast_scorecard(500)
    if scorecard:
        score_cols = st.columns(len(scorecard))
        for col, card in zip(score_cols, scorecard):
            hit_rate = card.get("hit_rate_pct")
            avg_return = card.get("avg_return_pct")
            hit_label = "Pending" if hit_rate is None else f"{float(hit_rate):.1f}%"
            if avg_return is None:
                col.metric(str(card.get("horizon", "--")), hit_label)
            else:
                col.metric(str(card.get("horizon", "--")), hit_label, delta=f"{float(avg_return):+.3f}% avg")
    else:
        st.info("No official/update forecasts scored yet.")
    st.dataframe(scorecard, hide_index=True, use_container_width=True)
except Exception as exc:
    st.warning("Forecast Scorecard is rebuilding. Older saved data will be migrated automatically on restart.")
    st.caption(str(exc))

st.subheader("Official Forecast Ledger")
st.dataframe(db.latest_forecasts(100), hide_index=True, use_container_width=True)

st.subheader("Journal")
st.dataframe(db.latest_journal(100), hide_index=True, use_container_width=True)

st.subheader("Alerts")
st.dataframe(db.latest_alerts(50), hide_index=True, use_container_width=True)

st.subheader("Changelog")
st.dataframe(db.changelog(50), hide_index=True, use_container_width=True)
