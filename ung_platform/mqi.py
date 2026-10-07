from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import csv
import io
import math
from statistics import mean, pstdev
from typing import Any, Protocol


@dataclass(frozen=True)
class MQIBar:
    timestamp: datetime
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class MQIConfig:
    yahoo_chart_url: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    stooq_url: str = "https://stooq.com/q/d/l/"
    ung_symbol: str = "UNG"
    ng_symbol: str = "NG=F"
    timeout_seconds: float = 15.0
    min_bars: int = 30
    max_age_hours: float = 36.0


@dataclass
class MQIObservation:
    score: float
    confidence: float
    source_status: str
    source_names: list[str]
    freshness_hours: float | None
    components: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "confidence": round(self.confidence, 3),
            "source_status": self.source_status,
            "source_names": list(self.source_names),
            "freshness_hours": None if self.freshness_hours is None else round(self.freshness_hours, 2),
            "components": {key: round(value, 2) for key, value in self.components.items()},
            "warnings": list(self.warnings),
        }


class FreeMarketDataSource(Protocol):
    name: str

    def history(self, symbol: str, limit: int = 120) -> list[MQIBar]:
        ...


class YahooChartSource:
    name = "YAHOO_CHART"

    def __init__(self, config: MQIConfig | None = None, session: Any = None):
        self.config = config or MQIConfig()
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise RuntimeError("Install requests with: pip install -r requirements.txt") from exc
            session = requests.Session()
        self.session = session

    def history(self, symbol: str, limit: int = 120) -> list[MQIBar]:
        params = {"range": "6mo", "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
        response = self.session.get(
            f"{self.config.yahoo_chart_url.rstrip('/')}/{symbol}",
            params=params,
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "UNG-Decision-Engine/1.0"},
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []
        bars: list[MQIBar] = []
        for ts, close, volume in zip(timestamps, closes, volumes):
            if close is None:
                continue
            bars.append(MQIBar(datetime.fromtimestamp(float(ts), tz=timezone.utc), float(close), float(volume or 0.0)))
        return bars[-limit:]


class StooqCsvSource:
    name = "STOOQ_CSV"

    def __init__(self, config: MQIConfig | None = None, session: Any = None):
        self.config = config or MQIConfig()
        if session is None:
            try:
                import requests
            except ImportError as exc:
                raise RuntimeError("Install requests with: pip install -r requirements.txt") from exc
            session = requests.Session()
        self.session = session

    def history(self, symbol: str, limit: int = 120) -> list[MQIBar]:
        response = self.session.get(
            self.config.stooq_url,
            params={"s": symbol, "d1": "20200101", "i": "d"},
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "UNG-Decision-Engine/1.0"},
        )
        response.raise_for_status()
        rows = csv.DictReader(io.StringIO(response.text))
        bars: list[MQIBar] = []
        for row in rows:
            try:
                close = float(row["Close"])
                volume = float(row.get("Volume") or 0.0)
                timestamp = datetime.fromisoformat(row["Date"]).replace(tzinfo=timezone.utc)
            except (KeyError, TypeError, ValueError):
                continue
            bars.append(MQIBar(timestamp, close, volume))
        return bars[-limit:]


class NonIBKRMQI:
    WEIGHTS = {
        "liquidity": 0.25,
        "volatility_quality": 0.20,
        "volume_price_quality": 0.20,
        "cross_market_coherence": 0.20,
        "trend_structure": 0.10,
        "data_quality": 0.05,
    }

    def __init__(self, config: MQIConfig | None = None):
        self.config = config or MQIConfig()

    def calculate(
        self,
        ung_bars: list[MQIBar],
        ng_bars: list[MQIBar],
        *,
        second_ng_bars: list[MQIBar] | None = None,
        source_names: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> MQIObservation:
        source_names = source_names or []
        warnings: list[str] = []
        ung = self._clean(ung_bars)
        ng = self._clean(ng_bars)

        if len(ung) < self.config.min_bars:
            return MQIObservation(0.0, 0.0, "INSUFFICIENT_UNG_DATA", source_names, None,
                                  warnings=[f"UNG requires {self.config.min_bars} bars; received {len(ung)}"])
        if len(ng) < self.config.min_bars:
            return MQIObservation(0.0, 0.0, "INSUFFICIENT_NG_DATA", source_names, None,
                                  warnings=[f"NG requires {self.config.min_bars} bars; received {len(ng)}"])

        freshness_score, age_hours = self._freshness(ung, ng, as_of)
        coherence = self._correlation_quality(ung, ng)
        if second_ng_bars:
            term = self._term_structure_quality(ng, second_ng_bars)
            if term is not None:
                coherence = 0.5 * coherence + 0.5 * term
                warnings.append("term_structure included from optional second NG contract")

        components = {
            "liquidity": self._liquidity(ung),
            "volatility_quality": self._volatility_quality(ng),
            "volume_price_quality": self._volume_price_quality(ng),
            "cross_market_coherence": coherence,
            "trend_structure": self._trend_structure(ng),
            "data_quality": freshness_score,
        }
        score = sum(components[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        confidence = 0.55 + min(0.20, 0.05 * max(0, len(set(source_names)) - 1))
        confidence += 0.15 if freshness_score >= 80 else 0.0
        confidence += 0.10 if len(ung) >= 90 and len(ng) >= 90 else 0.0
        confidence = max(0.0, min(1.0, confidence))

        if freshness_score < 50:
            warnings.append("stale free-feed data; MQI confidence reduced")
        if not source_names:
            warnings.append("source identity missing")

        status = "OK" if confidence >= 0.70 and freshness_score >= 50 else "DEGRADED"
        return MQIObservation(score, confidence, status, source_names, age_hours, components, warnings)

    def fetch_and_calculate(self, source: FreeMarketDataSource) -> MQIObservation:
        ung = source.history(self.config.ung_symbol, 180)
        ng = source.history(self.config.ng_symbol, 180)
        return self.calculate(ung, ng, source_names=[source.name])

    def fetch_with_fallbacks(self, sources: list[FreeMarketDataSource]) -> MQIObservation:
        last_error: Exception | None = None
        for source in sources:
            try:
                result = self.fetch_and_calculate(source)
                if result.source_status not in {"INSUFFICIENT_UNG_DATA", "INSUFFICIENT_NG_DATA"}:
                    return result
            except Exception as exc:
                last_error = exc
        warning = "all non-IBKR sources failed"
        if last_error:
            warning += f": {type(last_error).__name__}: {last_error}"
        return MQIObservation(0.0, 0.0, "UNAVAILABLE", [], None, warnings=[warning])

    def _clean(self, bars):
        return [b for b in bars if b.close > 0 and math.isfinite(b.close)]

    def _returns(self, bars):
        return [math.log(bars[i].close / bars[i - 1].close) for i in range(1, len(bars))
                if bars[i - 1].close > 0 and bars[i].close > 0]

    def _liquidity(self, bars):
        values = [max(0.0, b.volume) for b in bars[-60:]]
        positive = [v for v in values if v > 0]
        if len(positive) < 10:
            return 45.0
        recent = mean(positive[-10:])
        baseline = mean(positive[:-10]) if len(positive) > 10 else recent
        ratio = recent / baseline if baseline else 1.0
        return 100.0 * max(0.0, min(1.0, 0.55 + 0.25 * math.tanh(ratio - 1.0)))

    def _volatility_quality(self, bars):
        returns = self._returns(bars)
        if len(returns) < 20:
            return 45.0
        current = pstdev(returns[-20:])
        history = [pstdev(returns[i - 20:i]) for i in range(20, len(returns))]
        if not history or current <= 0:
            return 50.0
        percentile = sum(v <= current for v in history) / len(history)
        return max(0.0, min(100.0, 100.0 - 55.0 * abs(percentile - 0.45) / 0.55))

    def _volume_price_quality(self, bars):
        returns = self._returns(bars)
        if len(returns) < 21:
            return 45.0
        volumes = [max(0.0, b.volume) for b in bars[-20:]]
        if not any(volumes):
            return 45.0
        avg = mean(volumes) or 1.0
        confirmation = sum(
            (1 if r > 0 else -1 if r < 0 else 0) * min(v / avg, 3.0)
            for r, v in zip(returns[-20:], volumes)
        )
        return max(0.0, min(100.0, 50.0 + 50.0 * confirmation / 60.0))

    def _correlation_quality(self, ung, ng):
        a, b = self._returns(ung), self._returns(ng)
        n = min(len(a), len(b), 60)
        if n < 20:
            return 50.0
        a, b = a[-n:], b[-n:]
        ma, mb = mean(a), mean(b)
        da, db = [x - ma for x in a], [x - mb for x in b]
        denom = math.sqrt(sum(x * x for x in da) * sum(x * x for x in db))
        if denom == 0:
            return 50.0
        corr = sum(x * y for x, y in zip(da, db)) / denom
        return max(0.0, min(100.0, 50.0 + 50.0 * corr))

    def _trend_structure(self, bars):
        closes = [b.close for b in bars]
        if len(closes) < 30:
            return 45.0
        slow = mean(closes[-30:])
        spread = (mean(closes[-10:]) - slow) / slow if slow else 0.0
        return max(0.0, min(100.0, 50.0 + 1000.0 * spread))

    def _term_structure_quality(self, front, second):
        front, second = self._clean(front), self._clean(second)
        if not front or not second:
            return None
        f, s = front[-1].close, second[-1].close
        if f <= 0 or s <= 0:
            return None
        spread_pct = (f - s) / s
        return max(0.0, min(100.0, 50.0 + 1250.0 * spread_pct))

    def _freshness(self, ung, ng, as_of):
        as_of = as_of or datetime.now(timezone.utc)
        latest = max(ung[-1].timestamp, ng[-1].timestamp)
        age = max(0.0, (as_of - latest).total_seconds() / 3600.0)
        return max(0.0, min(100.0, 100.0 * (1.0 - age / self.config.max_age_hours))), age
