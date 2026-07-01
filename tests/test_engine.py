from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ung_platform.engine import DecisionEngineV8RTIS, EngineConfig, MarketBar
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


def make_bar(index: int, price: float) -> MarketBar:
    ts = datetime(2026, 7, 1, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=index)
    return MarketBar(timestamp=ts, open=price - 0.01, high=price + 0.03, low=price - 0.03, close=price, volume=120_000 + index * 100)


def warmed_engine() -> tuple[DecisionEngineV8RTIS, object]:
    engine = DecisionEngineV8RTIS(EngineConfig(position_qty=100, average_cost=10.0, minimum_harvest_profit=0.10))
    decision = None
    for i in range(80):
        price = 10.0 + i * 0.01
        decision = engine.update(make_bar(i, price), emit_alerts=False)
    assert decision is not None
    return engine, decision


def test_local_engine_uses_v8_state_vocabulary() -> None:
    _, decision = warmed_engine()
    assert decision.state in V8_STATES
    assert decision.state not in {"BUY", "ACCUMULATE", "HARVEST", "HARVEST_WATCH", "REBUY", "REBUY_WATCH"}


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
    result = db.tune_from_scorebook(decision.snapshot and EngineConfig())
    assert result["changed"] is False
    assert result["reviewed_count"] < 10
