from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

from .engine import MarketBar


@dataclass
class AlpacaConfig:
    api_key_id: str | None
    api_secret_key: str | None
    feed: str = "iex"
    data_url: str = "https://data.alpaca.markets"

    @classmethod
    def from_env(cls) -> "AlpacaConfig":
        return cls(
            api_key_id=os.getenv("ALPACA_API_KEY_ID"),
            api_secret_key=os.getenv("ALPACA_API_SECRET_KEY"),
            feed=os.getenv("ALPACA_DATA_FEED", "iex"),
            data_url=os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets"),
        )

    @property
    def ready(self) -> bool:
        return bool(self.api_key_id and self.api_secret_key)


class AlpacaDataClient:
    def __init__(self, config: AlpacaConfig | None = None, symbol: str = "UNG"):
        self.config = config or AlpacaConfig.from_env()
        self.symbol = symbol
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("Install requests with: pip install -r requirements.txt") from exc
        self.requests = requests

    def latest_bar(self) -> MarketBar:
        self._require_ready()
        bar_payload = self._get_json(f"/v2/stocks/{self.symbol}/bars/latest", {"feed": self.config.feed})
        quote_payload = self._get_json(f"/v2/stocks/{self.symbol}/quotes/latest", {"feed": self.config.feed})
        bar = bar_payload.get("bar") or bar_payload.get(self.symbol) or bar_payload
        quote = quote_payload.get("quote") or quote_payload.get(self.symbol) or {}
        return self._parse_bar(bar, quote)

    def recent_bars(self, limit: int = 120) -> list[MarketBar]:
        self._require_ready()
        payload = self._get_json(
            f"/v2/stocks/{self.symbol}/bars",
            {
                "feed": self.config.feed,
                "timeframe": "1Min",
                "limit": int(limit),
                "adjustment": "raw",
                "sort": "asc",
            },
        )
        bars = payload.get("bars") or []
        return [self._parse_bar(item, {}) for item in bars if item]

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = self.config.data_url.rstrip("/") + path
        response = self.requests.get(url, headers=self._headers(), params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.api_key_id or "",
            "APCA-API-SECRET-KEY": self.config.api_secret_key or "",
        }

    def _require_ready(self) -> None:
        if not self.config.ready:
            raise RuntimeError("Missing ALPACA_API_KEY_ID or ALPACA_API_SECRET_KEY.")

    def _parse_bar(self, bar: dict[str, Any], quote: dict[str, Any]) -> MarketBar:
        timestamp = self._parse_time(bar.get("t"))
        bid = self._num(quote.get("bp"))
        ask = self._num(quote.get("ap"))
        return MarketBar(
            timestamp=timestamp,
            open=float(bar["o"]),
            high=float(bar["h"]),
            low=float(bar["l"]),
            close=float(bar["c"]),
            volume=float(bar.get("v", 0)),
            bid=bid,
            ask=ask,
        )

    def _parse_time(self, value: str | None) -> datetime:
        if not value:
            return datetime.utcnow()
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _num(self, value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
