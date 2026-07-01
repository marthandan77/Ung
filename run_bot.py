from __future__ import annotations

import argparse
import time

from ung_platform.alerts import TelegramAlerter
from ung_platform.alpaca import AlpacaConfig, AlpacaDataClient
from ung_platform.engine import DecisionEngineV8RTIS, EngineConfig
from ung_platform.storage import SQLiteJournal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the UNG V8 RTIS alert monitor.")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--seed-bars", type=int, default=320)
    parser.add_argument("--position-qty", type=int, default=30_900)
    parser.add_argument("--average-cost", type=float, default=11.5453)
    parser.add_argument("--minimum-profit", type=float, default=0.10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    engine = DecisionEngineV8RTIS(
        EngineConfig(
            position_qty=args.position_qty,
            average_cost=args.average_cost,
            minimum_harvest_profit=args.minimum_profit,
        )
    )
    if not AlpacaConfig.from_env().ready:
        raise SystemExit("Missing Alpaca keys. Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY first.")
    client = AlpacaDataClient()
    journal = SQLiteJournal()
    alerter = TelegramAlerter()

    last_timestamp = None
    for bar in client.recent_bars(limit=args.seed_bars):
        last_timestamp = bar.timestamp
        journal.record(engine.update(bar, emit_alerts=False))

    while True:
        bar = client.latest_bar()
        if bar.timestamp != last_timestamp:
            last_timestamp = bar.timestamp
            decision = engine.update(bar)
            journal.record(decision)
            print(f"{bar.timestamp} {decision.state} price={decision.snapshot.price:.2f} reason={decision.trigger_reason}")
            if decision.alert:
                alerter.send(decision.alert_text)

        if args.once:
            break
        time.sleep(max(15, args.poll_seconds))


if __name__ == "__main__":
    main()
