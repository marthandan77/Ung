from datetime import datetime, timedelta, timezone

from ung_platform.mqi import MQIBar, MQIConfig, MQIQuote, NonIBKRMQI


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


def test_mqi_tracks_missing_components_and_coverage():
    ung = series(10, 0.001, 1_000_000)
    ng = series(3, 0.001, 200_000)
    result = NonIBKRMQI(MQIConfig(min_bars=30)).calculate(
        ung, ng, source_names=["YAHOO_CHART"], as_of=ung[-1].timestamp
    )
    assert 0 <= result.coverage <= 1
    assert "microstructure" in result.missing_components
    assert "term_structure" in result.missing_components
    assert result.component_status["volume_price_quality"] == "OK"


def test_mqi_quote_improves_microstructure_coverage():
    ung = series(10, 0.001, 1_000_000)
    ng = series(3, 0.001, 200_000)
    quote = MQIQuote(ung[-1].timestamp, 9.99, 10.01, 1000, 1000)
    result = NonIBKRMQI(MQIConfig(min_bars=30)).calculate(
        ung, ng, quote=quote, source_names=["ALPACA_IEX"], as_of=ung[-1].timestamp
    )
    assert result.component_status["microstructure"] == "OK"
    assert result.coverage > 0.35


def test_mqi_uses_timestamp_alignment_for_mixed_frequency_data():
    start = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ung = [
        MQIBar(start + timedelta(minutes=i), 10.0 + 0.0005 * i, 1000)
        for i in range(180)
    ]
    ng = [
        MQIBar(start + timedelta(hours=i), 3.0 + 0.001 * i, 2000)
        for i in range(3)
    ]
    result = NonIBKRMQI(MQIConfig(min_bars=3, min_intraday_bars=30)).calculate(
        ung, ng, as_of=ung[-1].timestamp, source_names=["TEST"]
    )
    assert result.component_status["ng_coherence"] == "OK"
    assert result.missing_components.count("ng_coherence") == 0
