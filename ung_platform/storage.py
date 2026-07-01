from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import math
import sqlite3
from typing import Any

from .engine import Decision


class SQLiteJournal:
    FORECAST_HORIZONS = (5, 15, 30, 60)

    def __init__(self, path: str | Path = "data/ung_platform.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                create table if not exists journal (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    symbol text not null,
                    state text not null,
                    price real not null,
                    profit_per_share real not null,
                    he real not null,
                    hc real not null,
                    mqi real not null,
                    trigger_reason text not null,
                    payload text not null
                )
                """
            )
            con.execute(
                """
                create table if not exists alerts (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    state text not null,
                    price real not null,
                    message text not null
                )
                """
            )
            con.execute(
                """
                create table if not exists forecast_ledger (
                    id integer primary key autoincrement,
                    journal_id integer,
                    forecast_timestamp text not null,
                    symbol text not null,
                    state text not null,
                    expected_direction text not null,
                    price_now real not null,
                    forecast_volatility real not null,
                    expected_move_30m real not null,
                    he real not null,
                    hc real not null,
                    rp real not null,
                    bc real not null,
                    mqi real not null,
                    rs real not null,
                    model_status text not null,
                    regime_label text not null,
                    regime_state text not null,
                    regime_probabilities text not null,
                    markov_state text not null,
                    markov_next text not null,
                    markov_warning text not null,
                    garch_status text not null,
                    ev_ranking text not null,
                    trigger_reason text not null,
                    actual_5m real,
                    return_5m real,
                    hit_5m integer,
                    actual_15m real,
                    return_15m real,
                    hit_15m integer,
                    actual_30m real,
                    return_30m real,
                    hit_30m integer,
                    actual_60m real,
                    return_60m real,
                    hit_60m integer,
                    payload text not null
                )
                """
            )

    def record(self, decision: Decision) -> None:
        snap = decision.snapshot
        payload = decision.to_dict()
        with self._connect() as con:
            self._update_forecast_outcomes(con, decision)
            cursor = con.execute(
                """
                insert into journal (
                    timestamp, symbol, state, price, profit_per_share,
                    he, hc, mqi, trigger_reason, payload
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.timestamp.isoformat(),
                    snap.symbol,
                    decision.state,
                    snap.price,
                    snap.profit_per_share,
                    snap.he,
                    snap.hc,
                    snap.mqi,
                    decision.trigger_reason,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            self._insert_forecast(con, decision, cursor.lastrowid, payload)
            if decision.alert:
                con.execute(
                    "insert into alerts (timestamp, state, price, message) values (?, ?, ?, ?)",
                    (snap.timestamp.isoformat(), decision.state, snap.price, decision.alert_text),
                )

    def latest_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                select timestamp, symbol, state, price, profit_per_share, he, hc, mqi, trigger_reason
                from journal
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_forecasts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                select forecast_timestamp, symbol, state, expected_direction, price_now,
                       actual_5m, return_5m, hit_5m,
                       actual_15m, return_15m, hit_15m,
                       actual_30m, return_30m, hit_30m,
                       actual_60m, return_60m, hit_60m,
                       he, rp, mqi, regime_label, garch_status, ev_ranking
                from forecast_ledger
                order by id desc
                limit ?
                """,
                (limit,),
            ).fetchall()
        return [self._format_forecast_row(dict(row)) for row in rows]

    def forecast_scorecard(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.latest_forecasts(limit)
        cards: list[dict[str, Any]] = []
        for horizon in self.FORECAST_HORIZONS:
            hit_key = f"hit_{horizon}m"
            ret_key = f"return_{horizon}m_pct"
            closed = [row for row in rows if row.get(hit_key) is not None]
            if not closed:
                cards.append({"horizon": f"{horizon}m", "closed": 0, "hit_rate_pct": None, "avg_return_pct": None})
                continue
            hits = sum(1 for row in closed if int(row[hit_key]) == 1)
            returns = [float(row[ret_key]) for row in closed if row.get(ret_key) is not None]
            avg_return = sum(returns) / len(returns) if returns else None
            cards.append(
                {
                    "horizon": f"{horizon}m",
                    "closed": len(closed),
                    "hit_rate_pct": round(100 * hits / len(closed), 1),
                    "avg_return_pct": None if avg_return is None else round(avg_return, 4),
                }
            )
        return cards

    def latest_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "select timestamp, state, price, message from alerts order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _insert_forecast(self, con: sqlite3.Connection, decision: Decision, journal_id: int, payload: dict[str, Any]) -> None:
        snap = decision.snapshot
        forecast_vol = float(snap.next_period_volatility_forecast or 0.0)
        con.execute(
            """
            insert into forecast_ledger (
                journal_id, forecast_timestamp, symbol, state, expected_direction,
                price_now, forecast_volatility, expected_move_30m,
                he, hc, rp, bc, mqi, rs,
                model_status, regime_label, regime_state, regime_probabilities,
                markov_state, markov_next, markov_warning,
                garch_status, ev_ranking, trigger_reason, payload
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                journal_id,
                snap.timestamp.isoformat(),
                snap.symbol,
                decision.state,
                self._expected_direction(decision.state),
                snap.price,
                forecast_vol,
                snap.price * forecast_vol * math.sqrt(30),
                snap.he,
                snap.hc,
                snap.rp,
                snap.bc,
                snap.mqi,
                snap.rs,
                snap.model_status,
                snap.regime_label,
                snap.regime_state_id,
                json.dumps(snap.regime_probabilities, sort_keys=True),
                snap.markov_current_state,
                json.dumps(snap.markov_next_state_probabilities, sort_keys=True),
                snap.transition_warning,
                snap.garch_status,
                snap.ev_ranking,
                decision.trigger_reason,
                json.dumps(payload, sort_keys=True),
            ),
        )

    def _update_forecast_outcomes(self, con: sqlite3.Connection, decision: Decision) -> None:
        snap = decision.snapshot
        now = self._parse_timestamp(snap.timestamp.isoformat())
        rows = con.execute(
            """
            select id, forecast_timestamp, expected_direction, price_now, forecast_volatility,
                   actual_5m, actual_15m, actual_30m, actual_60m
            from forecast_ledger
            where actual_60m is null
            order by id asc
            """
        ).fetchall()
        for row in rows:
            forecast_time = self._parse_timestamp(row["forecast_timestamp"])
            elapsed_minutes = (now - forecast_time).total_seconds() / 60
            updates: dict[str, Any] = {}
            for horizon in self.FORECAST_HORIZONS:
                actual_key = f"actual_{horizon}m"
                if row[actual_key] is not None or elapsed_minutes < horizon:
                    continue
                ret = (snap.price - float(row["price_now"])) / float(row["price_now"])
                hit = self._forecast_hit(row["expected_direction"], ret, float(row["forecast_volatility"]), horizon)
                updates[actual_key] = snap.price
                updates[f"return_{horizon}m"] = ret
                updates[f"hit_{horizon}m"] = hit
            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                con.execute(f"update forecast_ledger set {set_clause} where id = ?", (*updates.values(), row["id"]))

    def _expected_direction(self, state: str) -> str:
        if state in {"HARVEST", "HARVEST_WATCH", "SELL_READY", "SELL_WATCH", "PROTECT"}:
            return "DOWN"
        if state in {"BUY", "ACCUMULATE", "REBUY", "REBUY_WATCH", "BUYBACK_READY", "BUYBACK_WATCH"}:
            return "UP"
        return "NEUTRAL"

    def _forecast_hit(self, expected_direction: str, actual_return: float, forecast_volatility: float, horizon: int) -> int:
        if expected_direction == "UP":
            return int(actual_return >= 0)
        if expected_direction == "DOWN":
            return int(actual_return <= 0)
        neutral_band = max(0.0015, forecast_volatility * math.sqrt(max(1, horizon)))
        return int(abs(actual_return) <= neutral_band)

    def _parse_timestamp(self, value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _format_forecast_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for horizon in self.FORECAST_HORIZONS:
            ret_key = f"return_{horizon}m"
            value = row.pop(ret_key, None)
            row[f"{ret_key}_pct"] = None if value is None else round(float(value) * 100, 3)
        for key in {"he", "rp", "mqi"}:
            if row.get(key) is not None:
                row[key] = round(float(row[key]), 2)
        return row
