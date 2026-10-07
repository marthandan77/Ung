from datetime import datetime, timedelta, timezone

from ung_platform.mqi import MQIBar, MQIConfig, NonIBKRMQI


def series(start, drift, volume, n=90):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    return [MQIBar(base + timedelta(days=i), start * (1 + drift * i), volume) for i in range(n)]


def test_mqi_bounded_and_confident():
    ung = series(10, 0.002, 1_000_000)
    ng = series(3, 0.002, 200_000)
    result = NonIBKRMQI(MQIConfig(min_bars=30)).calculate(
        ung, ng,
        source_names=["YAHOO_CHART", "STOOQ_CSV"],
        as_of=ung[-1].timestamp + timedelta(hours=1),
    )
    assert 0 <= result.score <= 100
    assert 0 <= result.confidence <= 1
    assert result.source_status in {"OK", "DEGRADED"}


def test_mqi_requires_ng():
    ung = series(10, 0.001, 1_000_000, 40)
    result = NonIBKRMQI(MQIConfig(min_bars=30)).calculate(
        ung, [], source_names=["YAHOO_CHART"]
    )
    assert result.score == 0
    assert result.confidence == 0
    assert result.source_status == "INSUFFICIENT_NG_DATA"


def test_optional_term_structure_is_reported():
    ung = series(10, 0.001, 1_000_000)
    front = series(3, 0.001, 200_000)
    second = series(3.2, 0.001, 200_000)
    result = NonIBKRMQI(MQIConfig(min_bars=30)).calculate(
        ung, front,
        second_ng_bars=second,
        source_names=["YAHOO_CHART"],
        as_of=front[-1].timestamp,
    )
    assert any("term_structure" in warning for warning in result.warnings)
    assert "cross_market_coherence" in result.components
