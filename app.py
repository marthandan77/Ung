from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math
import os
from statistics import mean, pstdev

import requests
import streamlit as st


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


def secret_or_env(name: str, section: str | None = None, default: str | None = None) -> str | None:
    try:
        if section and section in st.secrets and name in st.secrets[section]:
            return str(st.secrets[section][name])
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


@dataclass
class AlpacaConfig:
    key: str | None
    secret: str | None
    feed: str = "iex"
    data_url: str = "https://data.alpaca.markets"

    @property
    def ready(self) -> bool:
        return bool(self.key and self.secret)


@dataclass
class MarketBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    bid: float | None = None
    ask: float | None = None


@dataclass
class EngineConfig:
    position_qty: int = 30_900
    average_cost: float = 11.5453
    minimum_harvest_profit: float = 0.10
    warmup_bars: int = 35


@dataclass
class Decision:
    state: str
    reason: str
    key_level: str
    price: float
    profit_per_share: float
    unrealized_profit: float
    vwap: float
    atr: float
    rsi: float
    volume_ratio: float
    opening_status: str
    he: float
    hc: float
    mqi: float
    model_status: str = "NOT_READY"
    garch_status: str = "FALLBACK_EWMA"

    @property
    def alert_text(self) -> str:
        return "\n".join(
            [
                f"UNG ALERT {self.state}",
                f"Price: {self.price:.2f}",
                f"Profit/share: {self.profit_per_share:.2f} | Unrealized: {self.unrealized_profit:.2f}",
                f"Reason: {self.reason}",
                "Regime probabilities: NOT_READY",
                "Markov: NOT_READY | Warning: NONE",
                f"Key level: {self.key_level}",
                f"HE {self.he:.1f} | HC {self.hc:.1f} | MQI {self.mqi:.1f}",
                "Logic: Long-only signal. No order sent.",
            ]
        )


class AlpacaDataClient:
    def __init__(self, config: AlpacaConfig, symbol: str = "UNG"):
        self.config = config
        self.symbol = symbol

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.config.key or "",
            "APCA-API-SECRET-KEY": self.config.secret or "",
        }

    def _get(self, path: str, params: dict) -> dict:
        if not self.config.ready:
            raise RuntimeError("Missing Alpaca API keys in Streamlit secrets.")
        url = self.config.data_url.rstrip("/") + path
        response = requests.get(url, headers=self._headers(), params=params, timeout=20)
        response.raise_for_status()
        return response.json()

    def recent_bars(self, limit: int = 120) -> list[MarketBar]:
        payload = self._get(
            f"/v2/stocks/{self.symbol}/bars",
            {"feed": self.config.feed, "timeframe": "1Min", "limit": int(limit), "adjustment": "raw", "sort": "asc"},
        )
        return [self._bar(item, {}) for item in payload.get("bars", [])]

    def latest_bar(self) -> MarketBar:
        bar_payload = self._get(f"/v2/stocks/{self.symbol}/bars/latest", {"feed": self.config.feed})
        quote_payload = self._get(f"/v2/stocks/{self.symbol}/quotes/latest", {"feed": self.config.feed})
        return self._bar(bar_payload.get("bar", {}), quote_payload.get("quote", {}))

    def _bar(self, bar: dict, quote: dict) -> MarketBar:
        return MarketBar(
            timestamp=datetime.fromisoformat(str(bar.get("t", datetime.utcnow().isoformat())).replace("Z", "+00:00")),
            open=float(bar["o"]),
            high=float(bar["h"]),
            low=float(bar["l"]),
            close=float(bar["c"]),
            volume=float(bar.get("v", 0)),
            bid=float(quote["bp"]) if quote.get("bp") is not None else None,
            ask=float(quote["ap"]) if quote.get("ap") is not None else None,
        )


class DecisionEngine:
    def __init__(self, config: EngineConfig):
        self.config = config
        self.closes = deque(maxlen=420)
        self.highs = deque(maxlen=420)
        self.lows = deque(maxlen=420)
        self.volumes = deque(maxlen=420)
        self.returns = deque(maxlen=160)
        self.true_ranges = deque(maxlen=80)
        self.day = None
        self.session_count = 0
        self.vwap_value = 0.0
        self.vwap_volume = 0.0
        self.opening_high = None
        self.opening_low = None
        self.ema_fast = None
        self.ema_slow = None
        self.last_close = None

    def update(self, bar: MarketBar) -> Decision:
        self._update_indicators(bar)
        return self._decide(bar)

    def _update_indicators(self, bar: MarketBar) -> None:
        if self.day != bar.timestamp.date():
            self.day = bar.timestamp.date()
            self.session_count = 0
            self.vwap_value = 0.0
            self.vwap_volume = 0.0
            self.opening_high = None
            self.opening_low = None
            self.ema_fast = None
            self.ema_slow = None

        price = float(bar.close)
        volume = max(float(bar.volume), 0.0)
        self.session_count += 1
        self.vwap_value += price * volume
        self.vwap_volume += volume
        if self.session_count <= 30:
            self.opening_high = price if self.opening_high is None else max(self.opening_high, bar.high)
            self.opening_low = price if self.opening_low is None else min(self.opening_low, bar.low)
        if self.last_close and self.last_close > 0:
            self.returns.append(math.log(price / self.last_close))
        true_range = max(
            float(bar.high) - float(bar.low),
            abs(float(bar.high) - (self.last_close or price)),
            abs(float(bar.low) - (self.last_close or price)),
        )
        self.true_ranges.append(true_range)
        self.ema_fast = self._ema(self.ema_fast, price, 9)
        self.ema_slow = self._ema(self.ema_slow, price, 21)
        self.closes.append(price)
        self.highs.append(float(bar.high))
        self.lows.append(float(bar.low))
        self.volumes.append(volume)
        self.last_close = price

    def _decide(self, bar: MarketBar) -> Decision:
        price = float(bar.close)
        vwap = safe_div(self.vwap_value, self.vwap_volume, price)
        atr = mean(list(self.true_ranges)[-14:]) if self.true_ranges else 0.0
        atr_pct = safe_div(atr, price)
        rsi = self._rsi()
        volume_ratio = self._volume_ratio()
        opening_status = self._opening_status(price)
        pps = price - self.config.average_cost
        unrealized = pps * self.config.position_qty
        vwap_distance = safe_div(price - vwap, price)

        ready = 1.0 if len(self.closes) >= self.config.warmup_bars else 0.35
        profit_score = clamp((pps - self.config.minimum_harvest_profit) / 0.40, 0, 1) * 45
        extension_score = clamp(vwap_distance / max(0.004, atr_pct * 0.9), 0, 1) * 18
        rsi_score = clamp((rsi - 55) / 18, 0, 1) * 14
        volume_score = clamp((volume_ratio - 0.9) / 1.4, 0, 1) * 10
        trend_score = 8 if (self.ema_fast or price) >= (self.ema_slow or price) else 0
        opening_score = 5 if opening_status == "ABOVE_OPENING_RANGE" else 0
        he = round((profit_score + extension_score + rsi_score + volume_score + trend_score + opening_score) * ready, 2)

        structure = 0.35
        if price >= vwap and (self.ema_fast or price) >= (self.ema_slow or price):
            structure += 0.35
        if opening_status in {"ABOVE_OPENING_RANGE", "INSIDE_OPENING_RANGE"}:
            structure += 0.15
        if rsi >= 52:
            structure += 0.15
        hc = round(100 * (1 if pps >= self.config.minimum_harvest_profit else 0) * clamp(structure, 0, 1) * ready, 2)
        mqi = round(clamp(45 + min(25, volume_ratio * 10) + min(20, len(self.closes)), 0, 100), 2)

        if len(self.closes) < self.config.warmup_bars:
            state, reason, key = "WAIT", f"warming up {len(self.closes)}/{self.config.warmup_bars} bars", "warmup"
        elif pps >= self.config.minimum_harvest_profit and he >= 70 and hc >= 65:
            state, reason, key = "HARVEST", "profit zone confirmed by VWAP, trend, and volume", f"profit/share {pps:.2f}"
        elif pps >= self.config.minimum_harvest_profit:
            state, reason, key = "HARVEST_WATCH", "profit zone reached but confirmation is not strong enough", f"profit/share {pps:.2f}"
        elif price < self.config.average_cost and vwap_distance <= -max(0.006, atr_pct * 0.7) and rsi <= 42 and self._short_rebound():
            state, reason, key = "BUY", "discount below VWAP with rebound confirmation", f"VWAP {vwap:.2f}"
        elif price < self.config.average_cost and (vwap_distance <= -max(0.006, atr_pct * 0.7) or rsi <= 38):
            state, reason, key = "ACCUMULATE", "discount forming but rebound confirmation is incomplete", f"VWAP {vwap:.2f}"
        elif price < self.config.average_cost:
            state, reason, key = "HOLD", "below cost basis; no profit harvest allowed", f"average cost {self.config.average_cost:.4f}"
        else:
            state, reason, key = "HOLD", "no actionable harvest or buy setup", f"profit/share {pps:.2f}"

        return Decision(state, reason, key, price, pps, unrealized, vwap, atr, rsi, volume_ratio, opening_status, he, hc, mqi)

    def _ema(self, current: float | None, price: float, period: int) -> float:
        if current is None:
            return price
        k = 2 / (period + 1)
        return price * k + current * (1 - k)

    def _rsi(self) -> float:
        values = list(self.closes)
        if len(values) <= 14:
            return 50.0
        changes = [values[i] - values[i - 1] for i in range(len(values) - 14, len(values))]
        gains = [x for x in changes if x > 0]
        losses = [-x for x in changes if x < 0]
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100 - (100 / (1 + avg_gain / avg_loss))

    def _volume_ratio(self) -> float:
        values = list(self.volumes)
        if len(values) < 20:
            return 1.0
        return values[-1] / (mean(values[-21:-1]) or 1.0)

    def _opening_status(self, price: float) -> str:
        if self.opening_high is None or self.opening_low is None:
            return "NOT_READY"
        if self.session_count <= 30:
            return "BUILDING"
        if price > self.opening_high:
            return "ABOVE_OPENING_RANGE"
        if price < self.opening_low:
            return "BELOW_OPENING_RANGE"
        return "INSIDE_OPENING_RANGE"

    def _short_rebound(self) -> bool:
        values = list(self.closes)
        return len(values) >= 6 and values[-1] > min(values[-6:]) and values[-1] >= values[-2]


def alpaca_config() -> AlpacaConfig:
    return AlpacaConfig(
        key=secret_or_env("ALPACA_API_KEY_ID", "alpaca"),
        secret=secret_or_env("ALPACA_API_SECRET_KEY", "alpaca"),
        feed=secret_or_env("ALPACA_DATA_FEED", "alpaca", "iex") or "iex",
    )


def demo_bars(avg_cost: float) -> list[MarketBar]:
    bars = []
    start = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
    for i in range(90):
        price = avg_cost - 0.28 + i * 0.006 + math.sin(i / 6) * 0.045
        bars.append(MarketBar(start + timedelta(minutes=i), price - 0.01, price + 0.025, price - 0.025, price, 80_000 + (i % 9) * 9_000))
    return bars


def reset_engine() -> None:
    st.session_state.pop("engine", None)
    st.session_state.pop("last_decision", None)
    st.session_state.pop("journal", None)


st.set_page_config(page_title="UNG V7-Lite", layout="wide")
st.title("UNG Decision Engine V7-Lite")

with st.sidebar:
    position_qty = st.number_input("Position shares", min_value=0, value=30_900, step=100)
    average_cost = st.number_input("Average cost", min_value=0.01, value=11.5453, step=0.0001, format="%.4f")
    minimum_profit = st.number_input("Minimum harvest profit/share", min_value=0.01, value=0.10, step=0.01)
    st.divider()
    st.caption("Alpaca API: ready" if alpaca_config().ready else "Alpaca API: missing keys")
    if st.button("Reset Engine", use_container_width=True):
        reset_engine()

config_key = (int(position_qty), float(average_cost), float(minimum_profit))
if st.session_state.get("config_key") != config_key:
    reset_engine()
    st.session_state["config_key"] = config_key

if "engine" not in st.session_state:
    st.session_state["engine"] = DecisionEngine(EngineConfig(*config_key))
if "journal" not in st.session_state:
    st.session_state["journal"] = []

engine = st.session_state["engine"]

left, middle, right = st.columns(3)
with left:
    manual_price = st.number_input("Manual UNG price", min_value=0.01, value=float(average_cost), step=0.01)
    manual_volume = st.number_input("Manual volume", min_value=0, value=100_000, step=10_000)
    if st.button("Add Manual Bar", use_container_width=True):
        bar = MarketBar(datetime.now(timezone.utc), manual_price, manual_price, manual_price, manual_price, manual_volume)
        st.session_state["last_decision"] = engine.update(bar)
with middle:
    if st.button("Load Demo Bars", use_container_width=True):
        for bar in demo_bars(float(average_cost)):
            st.session_state["last_decision"] = engine.update(bar)
with right:
    if st.button("Fetch Latest", use_container_width=True):
        try:
            client = AlpacaDataClient(alpaca_config())
            for bar in client.recent_bars(limit=max(60, engine.config.warmup_bars + 20)):
                st.session_state["last_decision"] = engine.update(bar)
            st.session_state["last_decision"] = engine.update(client.latest_bar())
        except Exception as exc:
            st.error(str(exc))

decision = st.session_state.get("last_decision")
if decision is None:
    st.info("Add a manual bar, load demo bars, or configure Alpaca secrets and fetch latest.")
else:
    st.session_state["journal"].append(
        {
            "time": datetime.now(timezone.utc).isoformat(),
            "state": decision.state,
            "price": decision.price,
            "profit/share": decision.profit_per_share,
            "HE": decision.he,
            "HC": decision.hc,
            "MQI": decision.mqi,
            "reason": decision.reason,
        }
    )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Signal", decision.state)
    c2.metric("Price", f"{decision.price:.2f}")
    c3.metric("Profit/share", f"{decision.profit_per_share:.2f}")
    c4.metric("HE", f"{decision.he:.1f}")
    c5.metric("MQI", f"{decision.mqi:.1f}")
    st.subheader("Decision")
    st.write(decision.reason)
    st.code(decision.alert_text, language="text")
    st.dataframe(
        [
            ("VWAP", f"{decision.vwap:.2f}"),
            ("ATR", f"{decision.atr:.3f}"),
            ("RSI", f"{decision.rsi:.1f}"),
            ("Volume ratio", f"{decision.volume_ratio:.2f}"),
            ("Opening range", decision.opening_status),
            ("Model status", decision.model_status),
            ("GARCH status", decision.garch_status),
        ],
        hide_index=True,
        use_container_width=True,
    )

st.subheader("Journal")
st.dataframe(list(reversed(st.session_state["journal"][-75:])), hide_index=True, use_container_width=True)
