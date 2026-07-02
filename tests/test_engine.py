from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ung_platform.alerts import normalize_whatsapp_id, whatsapp_payload
from ung_platform.charts import tradingview_ung_chart_html
from ung_platform.engine import Decision, DecisionEngineV8RTIS, EngineConfig, MarketBar
from ung_platform.storage import SQLiteJournal


V8_STATES = {
    "HOLD",
    "SELL_WATCH",
    "SELL_READY",
    "SOLD_WAIT",
    "BUYBACK_WATCH",
    "BUYBACK_READY",
    "WAIT",
    "PROTECT",
}
LEGACY_STATES = {"BUY", "ACCUMULATE", "HARVEST", "HARVEST_WATCH", "REBUY", "REBUY_WATCH"}


def make_bar(index: int, price: float) -> MarketBar:
    ts = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=index)
    return MarketBar(timestamp=ts, open=price - 0.01, high=price + 0.03, low=price - 0.03, close=price, volume=120_000 + index * 100)


def warmed_engine() -> tuple[DecisionEngineV8RTIS, Decision]:
    engine = DecisionEngineV8RTIS(EngineConfig(position_qty=100, average_cost=10.0, minimum_harvest_profit=0.10))
    decision: Decision | None = None
    for i in range(80):
        price = 10.0 + i * 0.01
        decision = engine.update(make_bar(i, price), emit_alerts=False)
    assert decision is not None
    return engine, decision


def test_local_engine_uses_v8_state_vocabulary() -> None:
    _, decision = warmed_engine()
    assert decision.state in V8_STATES
    assert decision.state not in LEGACY_STATES


def test_official_forecast_is_not_duplicated(tmp_path) -> None:
    _, decision = warmed_engine()
    db = SQLiteJournal(tmp_path / "ung.sqlite3")
    journal_id = db.record(decision)
    first = db.record_session_forecast(decision, journal_id=journal_id)
    second = db.record_session_forecast(decision, journal_id=journal_id)

    assert first["created"] is True
    assert second["created"] is False
    forecasts = db.latest_forecasts()
    official = [row for row in forecasts if row["forecast_kind"] == "OFFICIAL"]
    assert len(official) == 1


def test_tuning_waits_for_enough_reviewed_forecasts(tmp_path) -> None:
    _, decision = warmed_engine()
    db = SQLiteJournal(tmp_path / "ung.sqlite3")
    db.record(decision)
    result = db.tune_from_scorebook(EngineConfig())

    assert result["changed"] is False
    assert result["reviewed_count"] < 10


def test_scorecard_migrates_legacy_forecast_ledger(tmp_path) -> None:
    path = tmp_path / "ung.sqlite3"
    with sqlite3.connect(path) as con:
        con.execute("create table forecast_ledger (id integer primary key autoincrement)")

    db = SQLiteJournal(path)
    scorecard = db.forecast_scorecard()

    assert [row["horizon"] for row in scorecard] == ["5m", "15m", "30m", "60m"]
    assert all(row["closed"] == 0 for row in scorecard)
    assert all(row["hit_rate_pct"] is None for row in scorecard)


def test_tradingview_chart_targets_ung() -> None:
    html = tradingview_ung_chart_html()

    assert "AMEX:UNG" in html
    assert "TradingView" in html
    assert "github" not in html.lower()


def test_whatsapp_alert_id_and_payload_are_automated() -> None:
    assert normalize_whatsapp_id(" +65 9123-4567 ") == "+6591234567"
    assert normalize_whatsapp_id("whatsapp:+65 9123 4567") == "whatsapp:+6591234567"
    assert normalize_whatsapp_id("0065 9123 4567") == "+6591234567"
    assert whatsapp_payload("+65 9123 4567", "UNG test alert") == {
        "to": "+6591234567",
        "message": "UNG test alert",
    }
