from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from typing import Any

from .engine import Decision


class SQLiteJournal:
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

    def record(self, decision: Decision) -> None:
        snap = decision.snapshot
        payload = decision.to_dict()
        with self._connect() as con:
            con.execute(
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

    def latest_alerts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                "select timestamp, state, price, message from alerts order by id desc limit ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
