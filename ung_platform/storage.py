from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo
import json
import math
import sqlite3

from .engine import Decision, EngineConfig, clamp


ET = ZoneInfo("America/New_York")


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
                    rte real not null default 0,
                    he real not null,
                    hc real not null,
                    rp real not null default 0,
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
                    message text not null,
                    delivered_channels text not null default '{}'
                )
                """
            )
            con.execute(
                """
                create table if not exists forecast_ledger (
                    id integer primary key autoincrement,
                    journal_id integer,
                    forecast_timestamp text not null,
                    session_date text not null,
                    forecast_kind text not null,
                    update_label text,
                    symbol text not null,
                    state text not null,
                    expected_direction text not null,
                    price_now real not null,
                    forecast_volatility real not null,
                    expected_move_30m real not null,
                    rte real not null,
                    he real not null,
                    hc real not null,
                    rp real not null,
                    bc real not null,
                    mqi real not null,
                    rs real not null,
                    mur real not null,
                    hold_ev real not null,
                    sell_buyback_ev real not null,
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
                    reviewed integer not null default 0,
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
            con.execute(
                """
                create table if not exists alert_contacts (
                    key text primary key,
                    value text not null,
                    updated_at text not null
                )
                """
            )
            con.execute(
                """
                create table if not exists tuning_runs (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    reviewed_count integer not null,
                    previous_parameters text not null,
                    new_parameters text not null,
                    reason text not null
                )
                """
            )
            con.execute(
                """
                create table if not exists changelog (
                    id integer primary key autoincrement,
                    timestamp text not null,
                    title text not null,
                    details text not null,
                    payload text not null
                )
                """
            )
            self._migrate_columns(con)
            if not con.execute("select 1 from changelog limit 1").fetchone():
                self.add_changelog(con, "V8 RTIS forecast ledger initialized", "Journal, official forecast ledger, scorebook, contacts, and adaptive tuning are ready.", {})

    def _migrate_columns(self, con: sqlite3.Connection) -> None:
        self._add_missing_column(con, "journal", "rte", "real not null default 0")
        self._add_missing_column(con, "journal", "rp", "real not null default 0")
        self._add_missing_column(con, "alerts", "delivered_channels", "text not null default '{}'")
        forecast_columns = {
            "journal_id": "integer",
            "forecast_timestamp": "text not null default ''",
            "session_date": "text",
            "forecast_kind": "text not null default 'BAR_LEGACY'",
            "update_label": "text",
            "symbol": "text not null default 'UNG'",
            "state": "text not null default 'HOLD'",
            "expected_direction": "text not null default 'NEUTRAL'",
            "price_now": "real not null default 0",
            "forecast_volatility": "real not null default 0",
            "expected_move_30m": "real not null default 0",
            "rte": "real not null default 0",
            "he": "real not null default 0",
            "hc": "real not null default 0",
            "rp": "real not null default 0",
            "bc": "real not null default 0",
            "mqi": "real not null default 0",
            "rs": "real not null default 0",
            "mur": "real not null default 0",
            "hold_ev": "real not null default 0",
            "sell_buyback_ev": "real not null default 0",
            "model_status": "text not null default 'legacy'",
            "regime_label": "text not null default 'legacy'",
            "regime_state": "text not null default ''",
            "regime_probabilities": "text not null default '{}'",
            "markov_state": "text not null default ''",
            "markov_next": "text not null default '{}'",
            "markov_warning": "text not null default ''",
            "garch_status": "text not null default 'legacy'",
            "ev_ranking": "text not null default ''",
            "trigger_reason": "text not null default ''",
            "reviewed": "integer not null default 0",
            "actual_5m": "real",
            "return_5m": "real",
            "hit_5m": "integer",
            "actual_15m": "real",
            "return_15m": "real",
            "hit_15m": "integer",
            "actual_30m": "real",
            "return_30m": "real",
            "hit_30m": "integer",
            "actual_60m": "real",
            "return_60m": "real",
            "hit_60m": "integer",
            "payload": "text not null default '{}'",
        }
        for column, spec in forecast_columns.items():
            self._add_missing_column(con, "forecast_ledger", column, spec)

    def _add_missing_column(self, con: sqlite3.Connection, table: str, column: str, spec: str) -> None:
        columns = {row[1] for row in con.execute(f"pragma table_info({table})").fetchall()}
        if column not in columns:
            con.execute(f"alter table {table} add column {column} {spec}")

    def record(self, decision: Decision, delivered_channels: dict[str, Any] | None = None) -> int:
        snap = decision.snapshot
        payload = decision.to_dict()
        with self._connect() as con:
            self._update_forecast_outcomes(con, decision)
            cursor = con.execute(
                """
                insert into journal (
                    timestamp, symbol, state, price, profit_per_share,
                    rte, he, hc, rp, mqi, trigger_reason, payload
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.timestamp.isoformat(),
                    snap.symbol,
                    decision.state,
                    snap.price,
                    snap.profit_per_share,
                    snap.rte,
                    snap.he,
                    snap.hc,
                    snap.rp,
                    snap.mqi,
                    decision.trigger_reason,
                    json.dumps(payload, sort_keys=True),
                ),
            )
            if decision.alert:
                con.execute(
                    "insert into alerts (timestamp, state, price, message, delivered_channels) values (?, ?, ?, ?, ?)",
                    (
                        snap.timestamp.isoformat(),
                        decision.state,
                        snap.price,
                        decision.alert_text,
                        json.dumps(delivered_channels or {}, sort_keys=True),
                    ),
                )
            return int(cursor.lastrowid)

    def record_session_forecast(self, decision: Decision, journal_id: int | None = None, force_update: bool = False) -> dict[str, Any]:
        snap = decision.snapshot
        session_date = self._session_date(snap.timestamp)
        with self._connect() as con:
            official = con.execute(
                "select * from forecast_ledger where session_date = ? and forecast_kind = 'OFFICIAL' order by id desc limit 1",
                (session_date,),
            ).fetchone()
            if official is None:
                blocker = con.execute(
                    """
                    select id, session_date from forecast_ledger
                    where forecast_kind = 'OFFICIAL' and reviewed = 0 and session_date < ?
                    order by session_date asc, id asc limit 1
                    """,
                    (session_date,),
                ).fetchone()
                if blocker:
                    return {"created": False, "reason": f"Review forecast #{blocker['id']} before creating a new official forecast."}
                forecast_id = self._insert_forecast(con, decision, journal_id, "OFFICIAL", None)
                return {"created": True, "forecast_id": forecast_id, "forecast_kind": "OFFICIAL", "update_label": None}

            latest = con.execute(
                "select * from forecast_ledger where session_date = ? order by id desc limit 1",
                (session_date,),
            ).fetchone()
            state_changed = latest and latest["state"] != decision.state
            rte_changed = latest and abs(float(latest["rte"] or 0) - float(snap.rte or 0)) >= 0.04
            if not (force_update or state_changed or rte_changed):
                return {"created": False, "reason": "Official forecast already exists and no material update was detected."}

            update_count = con.execute(
                "select count(*) from forecast_ledger where session_date = ? and forecast_kind = 'UPDATE'",
                (session_date,),
            ).fetchone()[0]
            if update_count >= 3:
                return {"created": False, "reason": "Update A/B/C limit reached for this session."}
            update_label = f"Update {chr(65 + int(update_count))}"
            forecast_id = self._insert_forecast(con, decision, journal_id, "UPDATE", update_label)
            return {"created": True, "forecast_id": forecast_id, "forecast_kind": "UPDATE", "update_label": update_label}

    def _insert_forecast(
        self,
        con: sqlite3.Connection,
        decision: Decision,
        journal_id: int | None,
        forecast_kind: str,
        update_label: str | None,
    ) -> int:
        snap = decision.snapshot
        forecast_vol = float(snap.next_period_volatility_forecast or 0.0)
        cursor = con.execute(
            """
            insert into forecast_ledger (
                journal_id, forecast_timestamp, session_date, forecast_kind, update_label,
                symbol, state, expected_direction, price_now, forecast_volatility, expected_move_30m,
                rte, he, hc, rp, bc, mqi, rs, mur, hold_ev, sell_buyback_ev,
                model_status, regime_label, regime_state, regime_probabilities,
                markov_state, markov_next, markov_warning, garch_status, ev_ranking,
                trigger_reason, payload
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                journal_id,
                snap.timestamp.isoformat(),
                self._session_date(snap.timestamp),
                forecast_kind,
                update_label,
                snap.symbol,
                decision.state,
                self._expected_direction(decision.state),
                snap.price,
                forecast_vol,
                snap.price * forecast_vol * math.sqrt(30),
                snap.rte,
                snap.he,
                snap.hc,
                snap.rp,
                snap.bc,
                snap.mqi,
                snap.rs,
                snap.mur,
                snap.hold_ev,
                snap.sell_buyback_ev,
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
                json.dumps(decision.to_dict(), sort_keys=True),
            ),
        )
        self.add_changelog(
            con,
            f"{forecast_kind.title()} forecast recorded",
            f"{update_label or 'Official'} {decision.state} forecast stored for {self._session_date(snap.timestamp)}.",
            {"state": decision.state, "rte": snap.rte, "price": snap.price},
        )
        return int(cursor.lastrowid)

    def latest_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                select timestamp, symbol, state, price, profit_per_share, rte, he, hc, rp, mqi, trigger_reason
                from journal order by id desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_forecasts(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as con:
            self._mark_reviewed(con)
            rows = con.execute(
                """
                select id, forecast_timestamp, session_date, forecast_kind, update_label, symbol, state,
                       expected_direction, price_now, actual_5m, return_5m, hit_5m,
                       actual_15m, return_15m, hit_15m, actual_30m, return_30m, hit_30m,
                       actual_60m, return_60m, hit_60m, reviewed,
                       rte, he, rp, mqi, regime_label, garch_status, ev_ranking
                from forecast_ledger
                order by id desc limit ?
                """,
                (limit,),
            ).fetchall()
        return [self._format_forecast_row(dict(row)) for row in rows]

    def forecast_scorecard(self, limit: int = 500) -> list[dict[str, Any]]:
        rows = [row for row in self.latest_forecasts(limit) if row.get("forecast_kind") in {"OFFICIAL", "UPDATE"}]
        cards: list[dict[str, Any]] = []
        for horizon in self.FORECAST_HORIZONS:
            hit_key = f"hit_{horizon}m"
            ret_key = f"return_{horizon}m_pct"
            closed = [row for row in rows if row.get(hit_key) is not None]
            if not closed:
                cards.append({"horizon": f"{horizon}m", "closed": 0, "hit_rate_pct": None, "avg_return_pct": None})
                continue
            hits = sum(1 for row in closed if int(row[hit_key]) == 1)
            returns = []
            for row in closed:
                value = row.get(ret_key)
                if value is not None:
                    try:
                        returns.append(float(value))
                    except (TypeError, ValueError):
                        continue
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
                "select timestamp, state, price, message, delivered_channels from alerts order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_alert_contacts(self, contacts: dict[str, str]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as con:
            for key, value in contacts.items():
                con.execute(
                    "insert or replace into alert_contacts (key, value, updated_at) values (?, ?, ?)",
                    (key, value.strip(), now),
                )

    def alert_contacts(self) -> dict[str, str]:
        with self._connect() as con:
            rows = con.execute("select key, value from alert_contacts").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def tune_from_scorebook(self, current: EngineConfig) -> dict[str, Any]:
        with self._connect() as con:
            rows = con.execute(
                """
                select state, hit_30m, hit_60m, return_30m, return_60m
                from forecast_ledger
                where actual_60m is not null and forecast_kind in ('OFFICIAL', 'UPDATE')
                order by id desc limit 120
                """
            ).fetchall()
            if len(rows) < 10:
                return {"changed": False, "reason": "Adaptive tuning needs at least 10 completed official/update forecast reviews.", "reviewed_count": len(rows)}

            hits = []
            returns = []
            for row in rows:
                if row["hit_30m"] is not None:
                    hits.append(int(row["hit_30m"]))
                if row["hit_60m"] is not None:
                    hits.append(int(row["hit_60m"]))
                for key in ("return_30m", "return_60m"):
                    if row[key] is not None:
                        returns.append(float(row[key]))
            hit_rate = sum(hits) / len(hits) if hits else 0.0
            avg_return = sum(returns) / len(returns) if returns else 0.0

            previous = self._parameter_dict(current)
            new = previous.copy()
            if hit_rate < 0.55:
                new["mqi_min"] = clamp(new["mqi_min"] + 2.0, 45.0, 75.0)
                new["reentry_min_probability"] = clamp(new["reentry_min_probability"] + 2.0, 50.0, 80.0)
                new["minimum_harvest_profit"] = clamp(new["minimum_harvest_profit"] + 0.01, 0.05, 0.30)
                new["mur_max_dollars"] = clamp(new["mur_max_dollars"] - 0.01, 0.05, 0.30)
                reason = "Recent hit rate is weak, so V8 tightened market quality, re-entry, harvest, and missed-upside filters."
            elif hit_rate > 0.68 and avg_return >= 0:
                new["mqi_min"] = clamp(new["mqi_min"] - 1.0, 45.0, 75.0)
                new["reentry_min_probability"] = clamp(new["reentry_min_probability"] - 1.0, 50.0, 80.0)
                reason = "Recent hit rate is strong, so V8 slightly loosened entry filters."
            else:
                reason = "Recent scorebook is neutral; parameters stayed unchanged."

            changed = new != previous
            con.execute(
                "insert into tuning_runs (timestamp, reviewed_count, previous_parameters, new_parameters, reason) values (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), len(rows), json.dumps(previous, sort_keys=True), json.dumps(new, sort_keys=True), reason),
            )
            if changed:
                self.add_changelog(con, "Adaptive tuning updated RTIS parameters", reason, {"previous": previous, "new": new})
            return {"changed": changed, "reason": reason, "reviewed_count": len(rows), "previous": previous, "new": new, "hit_rate": round(hit_rate * 100, 1), "avg_return": round(avg_return * 100, 4)}

    def latest_tuned_parameters(self, defaults: EngineConfig) -> dict[str, float]:
        with self._connect() as con:
            row = con.execute("select new_parameters from tuning_runs order by id desc limit 1").fetchone()
        if not row:
            return self._parameter_dict(defaults)
        try:
            data = json.loads(row["new_parameters"])
        except json.JSONDecodeError:
            data = {}
        merged = self._parameter_dict(defaults)
        merged.update({key: float(value) for key, value in data.items() if key in merged})
        return merged

    def latest_tuning(self) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("select * from tuning_runs order by id desc limit 1").fetchone()
        if not row:
            return None
        item = dict(row)
        item["previous_parameters"] = json.loads(item["previous_parameters"])
        item["new_parameters"] = json.loads(item["new_parameters"])
        return item

    def changelog(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("select timestamp, title, details, payload from changelog order by id desc limit ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    def add_changelog(self, con: sqlite3.Connection, title: str, details: str, payload: dict[str, Any]) -> None:
        con.execute(
            "insert into changelog (timestamp, title, details, payload) values (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), title, details, json.dumps(payload, sort_keys=True)),
        )

    def _update_forecast_outcomes(self, con: sqlite3.Connection, decision: Decision) -> None:
        snap = decision.snapshot
        now = self._parse_timestamp(snap.timestamp.isoformat())
        rows = con.execute(
            """
            select id, forecast_timestamp, expected_direction, price_now, forecast_volatility,
                   actual_5m, actual_15m, actual_30m, actual_60m
            from forecast_ledger
            where actual_60m is null and forecast_kind in ('OFFICIAL', 'UPDATE')
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
                updates[actual_key] = snap.price
                updates[f"return_{horizon}m"] = ret
                updates[f"hit_{horizon}m"] = self._forecast_hit(row["expected_direction"], ret, float(row["forecast_volatility"]), horizon)
            if updates:
                set_clause = ", ".join(f"{key} = ?" for key in updates)
                con.execute(f"update forecast_ledger set {set_clause} where id = ?", (*updates.values(), row["id"]))
        self._mark_reviewed(con)

    def _mark_reviewed(self, con: sqlite3.Connection) -> None:
        con.execute("update forecast_ledger set reviewed = 1 where actual_60m is not null")

    def _expected_direction(self, state: str) -> str:
        if state in {"SELL_READY", "SELL_WATCH", "PROTECT"}:
            return "DOWN"
        if state in {"BUYBACK_READY", "BUYBACK_WATCH"}:
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

    def _session_date(self, timestamp: datetime) -> str:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(ET).date().isoformat()

    def _format_forecast_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for horizon in self.FORECAST_HORIZONS:
            ret_key = f"return_{horizon}m"
            value = row.pop(ret_key, None)
            if value is None:
                row[f"{ret_key}_pct"] = None
                continue
            try:
                row[f"{ret_key}_pct"] = round(float(value) * 100, 3)
            except (TypeError, ValueError):
                row[f"{ret_key}_pct"] = None
        for key in {"rte", "he", "rp", "mqi"}:
            if row.get(key) is not None:
                try:
                    row[key] = round(float(row[key]), 3)
                except (TypeError, ValueError):
                    row[key] = None
        row["reviewed"] = bool(row.get("reviewed"))
        return row

    def _parameter_dict(self, config: EngineConfig) -> dict[str, float]:
        values = asdict(config)
        return {
            "minimum_harvest_profit": float(values["minimum_harvest_profit"]),
            "reentry_min_probability": float(values["reentry_min_probability"]),
            "mur_max_dollars": float(values["mur_max_dollars"]),
            "mqi_min": float(values["mqi_min"]),
        }
