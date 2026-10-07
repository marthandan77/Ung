from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import io
import json
import math
import os
from statistics import mean, pstdev
from typing import Any, Protocol


@dataclass(frozen=True)
class MQIBar:
    timestamp: datetime
    close: float
    volume: float = 0.0
    high: float | None = None
    low: float | None = None


@dataclass(frozen=True)
class MQIQuote:
    timestamp: datetime
    bid: float | None
    ask: float | None
    bid_size: float | None = None
    ask_size: float | None = None


@dataclass(frozen=True)
class MQIConfig:
    yahoo_chart_url: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    stooq_url: str = "https://stooq.com/q/d/l/"
    alpaca_data_url: str = "https://data.alpaca.markets"
    eia_api_url: str = "https://api.eia.gov/v2"
    nws_api_url: str = "https://api.weather.gov"
    ung_symbol: str = "UNG"
    ng_symbol: str = "NG=F"
    second_ng_symbol: str | None = None
    alpaca_feed: str = "iex"
    timeout_seconds: float = 15.0
    min_bars: int = 30
    min_intraday_bars: int = 60
    max_age_minutes: float = 30.0
    max_daily_age_hours: float = 36.0
    eia_api_key: str | None = None
    weather_points: tuple[tuple[str, float, float], ...] = ()
    eia_storage_series: str | None = None
    
    @classmethod
    def from_env(cls) -> "MQIConfig":
        points: list[tuple[str, float, float]] = []
        raw = os.getenv("NG_WEATHER_POINTS", "")
        if raw:
            try:
                payload = json.loads(raw)
                for item in payload:
                    points.append((str(item["name"]), float(item["lat"]), float(item["lon"])))
            except (TypeError, ValueError, KeyError, json.JSONDecodeError):
                points = []
        return cls(
            alpaca_feed=os.getenv("ALPACA_DATA_FEED", "iex"),
            second_ng_symbol=os.getenv("NG_SECOND_SYMBOL") or os.getenv("NG_NEXT_SYMBOL"),
            eia_api_key=os.getenv("EIA_API_KEY"),
            weather_points=tuple(points),
            eia_storage_series=os.getenv("EIA_STORAGE_SERIES"),
        )


@dataclass
class MQIObservation:
    score: float
    confidence: float
    source_status: str
    source_names: list[str]
    freshness_hours: float | None
    components: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    coverage: float = 0.0
    missing_components: list[str] = field(default_factory=list)
    component_status: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 3),
            "source_status": self.source_status,
            "source_names": list(self.source_names),
            "freshness_hours": None if self.freshness_hours is None else round(self.freshness_hours, 2),
            "components": {key: round(value, 2) for key, value in self.components.items()},
            "warnings": list(self.warnings),
            "coverage": round(self.coverage, 3),
            "missing_components": list(self.missing_components),
            "component_status": dict(self.component_status),
        }


class FreeMarketDataSource(Protocol):
    name: str

    def history(self, symbol: str, limit: int = 120) -> list[MQIBar]:
        ...


class YahooChartSource:
    name = "YAHOO_CHART"

    def __init__(self, config: MQIConfig | None = None, session: Any = None):
        self.config = config or MQIConfig.from_env()
        self.session = session or self._requests_session()

    @staticmethod
    def _requests_session() -> Any:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError("Install requests with: pip install -r requirements.txt") from exc
        return requests.Session()

    def history(self, symbol: str, limit: int = 120, interval: str = "1d", range_: str = "6mo") -> list[MQIBar]:
        response = self.session.get(
            f"{self.config.yahoo_chart_url.rstrip('/')}/{symbol}",
            params={"range": range_, "interval": interval, "events": "history", "includeAdjustedClose": "true"},
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "UNG-Decision-Engine/2.0"},
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        volumes = quote.get("volume") or []
        bars: list[MQIBar] = []
        for ts, close, high, low, volume in zip(timestamps, closes, highs, lows, volumes):
            if close is None:
                continue
            bars.append(
                MQIBar(
                    datetime.fromtimestamp(float(ts), tz=timezone.utc),
                    float(close),
                    float(volume or 0.0),
                    None if high is None else float(high),
                    None if low is None else float(low),
                )
            )
        return bars[-limit:]


class StooqCsvSource:
    name = "STOOQ_CSV"

    def __init__(self, config: MQIConfig | None = None, session: Any = None):
        self.config = config or MQIConfig.from_env()
        self.session = session or YahooChartSource._requests_session()

    def history(self, symbol: str, limit: int = 120) -> list[MQIBar]:
        response = self.session.get(
            self.config.stooq_url,
            params={"s": symbol, "d1": "20200101", "i": "d"},
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "UNG-Decision-Engine/2.0"},
        )
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        bars: list[MQIBar] = []
        for row in rows:
            try:
                close = float(row["Close"])
                high = float(row.get("High") or close)
                low = float(row.get("Low") or close)
                volume = float(row.get("Volume") or 0.0)
                timestamp = datetime.fromisoformat(row["Date"]).replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            bars.append(MQIBar(timestamp, close, volume, high, low))
        return bars[-limit:]


class AlpacaMQISource:
    @property
    def name(self) -> str:
        return f"ALPACA_{self.config.alpaca_feed.upper()}"

    def __init__(self, config: MQIConfig | None = None, api_key_id: str | None = None, api_secret_key: str | None = None, session: Any = None):
        self.config = config or MQIConfig.from_env()
        self.api_key_id = api_key_id or os.getenv("ALPACA_API_KEY_ID")
        self.api_secret_key = api_secret_key or os.getenv("ALPACA_API_SECRET_KEY")
        self.session = session or YahooChartSource._requests_session()

    @property
    def ready(self) -> bool:
        return bool(self.api_key_id and self.api_secret_key)

    def history(self, symbol: str, limit: int = 180) -> list[MQIBar]:
        if not self.ready:
            return []
        response = self.session.get(
            f"{self.config.alpaca_data_url.rstrip('/')}/v2/stocks/{symbol}/bars",
            headers=self._headers(),
            params={
                "feed": self.config.alpaca_feed,
                "timeframe": "1Min",
                "limit": int(limit),
                "adjustment": "raw",
                "sort": "asc",
            },
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("bars") or []
        if isinstance(raw, dict):
            raw = raw.get(symbol) or []
        bars = [self._bar(item) for item in raw if item]
        return sorted({bar.timestamp: bar for bar in bars}.values(), key=lambda bar: bar.timestamp)

    def latest_quote(self, symbol: str) -> MQIQuote | None:
        if not self.ready:
            return None
        response = self.session.get(
            f"{self.config.alpaca_data_url.rstrip('/')}/v2/stocks/{symbol}/quotes/latest",
            headers=self._headers(),
            params={"feed": self.config.alpaca_feed},
            timeout=self.config.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("quote") or payload.get(symbol) or {}
        if not raw:
            return None
        return MQIQuote(
            self._time(raw.get("t")),
            self._num(raw.get("bp")),
            self._num(raw.get("ap")),
            self._num(raw.get("bs")),
            self._num(raw.get("as")),
        )

    def _headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key_id or "",
            "APCA-API-SECRET-KEY": self.api_secret_key or "",
        }

    @staticmethod
    def _time(value: str | None) -> datetime:
        if not value:
            return datetime.now(timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @classmethod
    def _bar(cls, item: dict[str, Any]) -> MQIBar:
        return MQIBar(
            cls._time(item.get("t")),
            float(item["c"]),
            float(item.get("v") or 0.0),
            float(item.get("h") or item["c"]),
            float(item.get("l") or item["c"]),
        )

    @staticmethod
    def _num(value: Any) -> float | None:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None


class EIAStorageSource:
    name = "EIA_STORAGE"

    def __init__(self, config: MQIConfig | None = None, session: Any = None, api_key: str | None = None):
        self.config = config or MQIConfig.from_env()
        self.api_key = api_key or self.config.eia_api_key
        self.session = session or YahooChartSource._requests_session()

    def latest(self) -> float | None:
        if not self.api_key or not self.config.eia_storage_series:
            return None
        response = self.session.get(
            f"{self.config.eia_api_url.rstrip('/')}/seriesid/{self.config.eia_storage_series}",
            params={"api_key": self.api_key},
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "UNG-Decision-Engine/2.0"},
        )
        response.raise_for_status()
        payload = response.json()
        data = ((payload.get("response") or {}).get("data") or [])
        for row in reversed(data):
            value = row.get("value")
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None


class NWSWeatherSource:
    name = "NWS_WEATHER"

    def __init__(self, config: MQIConfig | None = None, session: Any = None):
        self.config = config or MQIConfig.from_env()
        self.session = session or YahooChartSource._requests_session()

    def demand_pressure(self) -> float | None:
        if not self.config.weather_points:
            return None
        values: list[float] = []
        for _, lat, lon in self.config.weather_points:
            point = self.session.get(
                f"{self.config.nws_api_url.rstrip('/')}/points/{lat},{lon}",
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": "UNG-Decision-Engine/2.0"},
            )
            point.raise_for_status()
            hourly_url = point.json().get("properties", {}).get("forecastHourly")
            if not hourly_url:
                continue
            forecast = self.session.get(
                hourly_url,
                timeout=self.config.timeout_seconds,
                headers={"User-Agent": "UNG-Decision-Engine/2.0"},
            )
            forecast.raise_for_status()
            periods = (forecast.json().get("properties", {}).get("periods") or [])[:24]
            if not periods:
                continue
            pressure = 0.0
            for period in periods:
                temp = period.get("temperature")
                if not isinstance(temp, (int, float)):
                    continue
                if temp < 65:
                    pressure += min(1.0, (65.0 - temp) / 35.0)
                elif temp > 65:
                    pressure += min(1.0, (temp - 65.0) / 35.0)
            values.append(100.0 * pressure / max(len(periods), 1))
        return mean(values) if values else None


class NonIBKRMQI:
    WEIGHTS = {
        "microstructure": 0.25,
        "intraday_volatility_quality": 0.20,
        "ng_coherence": 0.20,
        "volume_price_quality": 0.15,
        "term_structure": 0.10,
        "context_quality": 0.10,
    }

    def __init__(self, config: MQIConfig | None = None):
        self.config = config or MQIConfig.from_env()

    def calculate(
        self,
        ung_bars: list[MQIBar],
        ng_bars: list[MQIBar],
        *,
        second_ng_bars: list[MQIBar] | None = None,
        quote: MQIQuote | None = None,
        eia_storage: float | None = None,
        weather_pressure: float | None = None,
        source_names: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> MQIObservation:
        source_names = list(source_names or [])
        warnings: list[str] = []
        ung = self._clean(ung_bars)
        ng = self._clean(ng_bars)
        as_of = as_of or datetime.now(timezone.utc)

        if len(ung) < self.config.min_bars:
            return self._unavailable("INSUFFICIENT_UNG_DATA", source_names, f"UNG requires {self.config.min_bars} bars; received {len(ung)}")
        ng_ok = len(ng) >= self.config.min_bars
        if not ng_ok:
            warnings.append(f"NG reference requires {self.config.min_bars} bars; received {len(ng)}")

        latest_age = max(0.0, (as_of - ung[-1].timestamp).total_seconds() / 3600.0)
        daily_age = max(0.0, (as_of - ng[-1].timestamp).total_seconds() / 3600.0) if ng else float("inf")
        quote_age = None if quote is None else max(0.0, (as_of - quote.timestamp).total_seconds() / 3600.0)
        intraday_ok = len(ung) >= self.config.min_intraday_bars and latest_age <= self.config.max_age_minutes / 60.0
        if not intraday_ok:
            warnings.append("UNG intraday data is stale or incomplete")
        if quote_age is not None and quote_age + (5.0 / 60.0) < latest_age:
            warnings.append("UNG quote is materially newer than the latest minute bar; feed freshness is inconsistent")
        if ng_ok and daily_age > self.config.max_daily_age_hours:
            warnings.append("NG reference data is stale")

        components: dict[str, float] = {}
        component_status: dict[str, str] = {}

        if quote and quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid > 0 and (quote_age is None or quote_age <= self.config.max_age_minutes / 60.0):
            mid = (quote.bid + quote.ask) / 2.0
            spread_pct = (quote.ask - quote.bid) / mid if mid else 1.0
            size_score = 50.0
            if quote.bid_size and quote.ask_size:
                size_score = min(100.0, 50.0 + 5.0 * math.log10(max(1.0, quote.bid_size + quote.ask_size)))
            components["microstructure"] = max(0.0, min(100.0, 100.0 - 6000.0 * spread_pct + 0.25 * size_score))
            component_status["microstructure"] = "OK"
        else:
            components["microstructure"] = 0.0
            component_status["microstructure"] = "MISSING"
            warnings.append("UNG bid/ask quote unavailable")

        if intraday_ok:
            components["intraday_volatility_quality"] = self._volatility_quality(ung)
            component_status["intraday_volatility_quality"] = "OK"
        else:
            components["intraday_volatility_quality"] = 0.0
            component_status["intraday_volatility_quality"] = "MISSING"

        if ng_ok and not self._is_stale(ng[-1].timestamp, as_of, self.config.max_daily_age_hours):
            coherence = self._correlation_quality(ung, ng)
            if coherence > 0:
                components["ng_coherence"] = coherence
                component_status["ng_coherence"] = "OK"
            else:
                components["ng_coherence"] = 0.0
                component_status["ng_coherence"] = "MISSING"
                warnings.append("UNG/NG have insufficient timestamp overlap for coherence")
        else:
            components["ng_coherence"] = 0.0
            component_status["ng_coherence"] = "MISSING"
            if not ng_ok:
                warnings.append("NG coherence omitted because the reference feed is unavailable")

        components["volume_price_quality"] = self._volume_price_quality(ung)
        component_status["volume_price_quality"] = "OK"

        if second_ng_bars:
            term = self._term_structure_quality(ng, second_ng_bars)
            if term is not None:
                components["term_structure"] = term
                component_status["term_structure"] = "OK"
            else:
                components["term_structure"] = 0.0
                component_status["term_structure"] = "MISSING"
        else:
            components["term_structure"] = 0.0
            component_status["term_structure"] = "MISSING"
            warnings.append("second NG contract unavailable; term structure omitted")

        context_parts: list[float] = []
        if eia_storage is not None:
            context_parts.append(self._storage_context_score(eia_storage))
        if weather_pressure is not None:
            context_parts.append(weather_pressure)
        if context_parts:
            components["context_quality"] = mean(context_parts)
            component_status["context_quality"] = "OK"
        else:
            components["context_quality"] = 0.0
            component_status["context_quality"] = "MISSING"
            warnings.append("EIA/weather context unavailable")

        available_weight = sum(self.WEIGHTS[k] for k, status in component_status.items() if status == "OK")
        score = 0.0 if available_weight == 0 else sum(components[k] * self.WEIGHTS[k] for k in self.WEIGHTS if component_status.get(k) == "OK") / available_weight
        missing = [k for k in self.WEIGHTS if component_status.get(k) != "OK"]
        coverage = available_weight
        freshness_score = max(0.0, min(100.0, 100.0 * (1.0 - max(0.0, latest_age) / (self.config.max_age_minutes / 60.0)))) if latest_age >= 0 else 100.0
        freshness_factor = freshness_score / 100.0
        confidence = coverage * (0.75 + 0.25 * freshness_factor)
        if quote_age is not None and quote_age > self.config.max_age_minutes / 60.0:
            confidence *= 0.75
        if quote_age is not None and quote_age + (5.0 / 60.0) < latest_age:
            confidence *= 0.60
        if not intraday_ok:
            confidence *= 0.50
        if daily_age > self.config.max_daily_age_hours:
            confidence *= 0.75
        confidence = round(max(0.0, min(1.0, confidence)), 3)
        if coverage < 0.60:
            warnings.append("MQI coverage below 60%; output is degraded evidence")
        status = "OK" if confidence >= 0.70 and coverage >= 0.75 else "DEGRADED"

        return MQIObservation(
            round(score, 2),
            confidence,
            status,
            source_names,
            max(0.0, latest_age),
            components,
            warnings,
            coverage,
            missing,
            component_status,
        )

    def fetch_live_bundle(
        self,
        *,
        alpaca_source: AlpacaMQISource | None = None,
        ng_source: YahooChartSource | None = None,
        second_ng_source: YahooChartSource | None = None,
        eia_source: EIAStorageSource | None = None,
        weather_source: NWSWeatherSource | None = None,
    ) -> MQIObservation:
        alpaca = alpaca_source or AlpacaMQISource(self.config)
        yahoo = ng_source or YahooChartSource(self.config)
        names: list[str] = []
        if not alpaca.ready:
            return self._unavailable("UNAVAILABLE", names, "Alpaca credentials unavailable for primary intraday UNG feed")
        try:
            ung = alpaca.history(self.config.ung_symbol, 240)
            quote = alpaca.latest_quote(self.config.ung_symbol)
            names.append(alpaca.name)
        except Exception as exc:
            return self._unavailable("UNAVAILABLE", names, f"Alpaca UNG source failed: {type(exc).__name__}: {exc}")
        try:
            ng = yahoo.history(self.config.ng_symbol, 180, interval="1h", range_="3mo")
            names.append(yahoo.name)
        except Exception as exc:
            ng = []
            names.append(f"{yahoo.name}:FAILED")
        second = None
        if self.config.second_ng_symbol:
            second_source = second_ng_source or yahoo
            try:
                second = second_source.history(self.config.second_ng_symbol, 180, interval="1h", range_="3mo")
                if second:
                    names.append(f"{second_source.name}:{self.config.second_ng_symbol}")
            except Exception:
                second = None
        eia = None
        if eia_source:
            try:
                eia = eia_source.latest()
                if eia is not None:
                    names.append(eia_source.name)
            except Exception:
                pass
        weather = None
        if weather_source:
            try:
                weather = weather_source.demand_pressure()
                if weather is not None:
                    names.append(weather_source.name)
            except Exception:
                pass
        return self.calculate(
            ung,
            ng,
            second_ng_bars=second,
            quote=quote,
            eia_storage=eia,
            weather_pressure=weather,
            source_names=names,
        )

    def fetch_and_calculate(self, source: FreeMarketDataSource) -> MQIObservation:
        ung = source.history(self.config.ung_symbol, 180)
        ng = source.history(self.config.ng_symbol, 180)
        return self.calculate(ung, ng, source_names=[source.name])

    def fetch_with_fallbacks(self, sources: list[FreeMarketDataSource]) -> MQIObservation:
        last_error: Exception | None = None
        for source in sources:
            try:
                result = self.fetch_and_calculate(source)
                if result.source_status not in {"INSUFFICIENT_UNG_DATA", "INSUFFICIENT_NG_DATA", "UNAVAILABLE"}:
                    return result
            except Exception as exc:
                last_error = exc
        warning = "all non-IBKR sources failed"
        if last_error:
            warning += f": {type(last_error).__name__}: {last_error}"
        return self._unavailable("UNAVAILABLE", [], warning)

    def _unavailable(self, status: str, source_names: list[str], warning: str) -> MQIObservation:
        return MQIObservation(0.0, 0.0, status, source_names, None, warnings=[warning], coverage=0.0, missing_components=list(self.WEIGHTS), component_status={k: "MISSING" for k in self.WEIGHTS})

    @staticmethod
    def _clean(bars: list[MQIBar]) -> list[MQIBar]:
        return [b for b in bars if b.close > 0 and math.isfinite(b.close)]

    @staticmethod
    def _is_stale(timestamp: datetime, as_of: datetime, max_age_hours: float) -> bool:
        return (as_of - timestamp).total_seconds() / 3600.0 > max_age_hours

    def _returns(self, bars: list[MQIBar]) -> list[float]:
        return [
            math.log(bars[i].close / bars[i - 1].close)
            for i in range(1, len(bars))
            if bars[i - 1].close > 0 and bars[i].close > 0
        ]

    def _volatility_quality(self, bars: list[MQIBar]) -> float:
        returns = self._returns(bars)
        if len(returns) < 30:
            return 0.0
        current = pstdev(returns[-20:])
        history = [pstdev(returns[i - 20:i]) for i in range(20, len(returns))]
        if not history or current <= 0:
            return 50.0
        percentile = sum(v <= current for v in history) / len(history)
        return max(0.0, min(100.0, 100.0 - 55.0 * abs(percentile - 0.45) / 0.55))

    def _volume_price_quality(self, bars: list[MQIBar]) -> float:
        returns = self._returns(bars)
        if len(returns) < 21:
            return 45.0
        volumes = [max(0.0, b.volume) for b in bars[-20:]]
        if not any(volumes):
            return 0.0
        avg = mean(volumes) or 1.0
        confirmation = sum((1 if r > 0 else -1 if r < 0 else 0) * min(v / avg, 3.0) for r, v in zip(returns[-20:], volumes))
        return max(0.0, min(100.0, 50.0 + 50.0 * confirmation / 60.0))

    def _correlation_quality(self, ung: list[MQIBar], ng: list[MQIBar]) -> float:
        aligned = self._aligned_returns(ung, ng)
        if len(aligned) < 20:
            return 0.0
        a, b = zip(*aligned[-60:])
        ma, mb = mean(a), mean(b)
        da, db = [x - ma for x in a], [x - mb for x in b]
        denom = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db))
        if denom == 0:
            return 50.0
        corr = sum(x * y for x, y in zip(da, db)) / denom
        return max(0.0, min(100.0, 50.0 + 50.0 * corr))

    @staticmethod
    def _aligned_returns(ung: list[MQIBar], ng: list[MQIBar]) -> list[tuple[float, float]]:
        """Align UNG/NG causally at a common frequency; never positional-truncate."""
        def median_interval_minutes(bars: list[MQIBar]) -> float:
            if len(bars) < 3:
                return 1440.0
            deltas = [(bars[i].timestamp - bars[i - 1].timestamp).total_seconds() / 60.0 for i in range(1, min(len(bars), 40)) if bars[i].timestamp > bars[i - 1].timestamp]
            return sorted(deltas)[len(deltas) // 2] if deltas else 1440.0
        def aggregate_hourly(bars: list[MQIBar]) -> list[MQIBar]:
            buckets: dict[datetime, MQIBar] = {}
            for bar in bars:
                key = bar.timestamp.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                buckets[key] = bar
            return [buckets[key] for key in sorted(buckets)]
        u = sorted(ung, key=lambda bar: bar.timestamp)
        n = sorted(ng, key=lambda bar: bar.timestamp)
        if median_interval_minutes(u) < 45.0 and median_interval_minutes(n) >= 45.0:
            u = aggregate_hourly(u)
        if median_interval_minutes(n) < 45.0 and median_interval_minutes(u) >= 45.0:
            n = aggregate_hourly(n)
        ur = {bar.timestamp: bar.close for bar in u}
        nr = {bar.timestamp: bar.close for bar in n}
        common = sorted(set(ur).intersection(nr))
        if len(common) < 2:
            return []
        pairs: list[tuple[float, float]] = []
        for previous, current in zip(common[:-1], common[1:]):
            up, uc = ur[previous], ur[current]
            np_, nc = nr[previous], nr[current]
            if up > 0 and uc > 0 and np_ > 0 and nc > 0:
                pairs.append((math.log(uc / up), math.log(nc / np_)))
        return pairs

    @staticmethod
    def _term_structure_quality(front: list[MQIBar], second: list[MQIBar]) -> float | None:
        front, second = [b for b in front if b.close > 0], [b for b in second if b.close > 0]
        if not front or not second:
            return None
        f, s = front[-1].close, second[-1].close
        if f <= 0 or s <= 0:
            return None
        spread_pct = abs((f - s) / s)
        return max(50.0, min(100.0, 75.0 + 500.0 * spread_pct))

    @staticmethod
    def _storage_context_score(value: float) -> float:
        # Presence is evidence quality only. Directional storage surprise belongs
        # in the fundamental feature layer once a point-in-time baseline exists.
        return 75.0

