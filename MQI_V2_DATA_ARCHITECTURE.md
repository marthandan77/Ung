# MQI V2 Data Architecture

MQI V2 is a market-quality layer for the UNG V8 RTIS engine. It does not create orders, change long-only authority, or manufacture missing evidence.

## Source hierarchy

| Data | Primary | Fallback / context | Required? |
|---|---|---|---|
| UNG 1-minute OHLCV | Alpaca | Yahoo/Stooq historical recovery | Yes |
| UNG bid/ask | Alpaca Level 1 quote | None | Strongly preferred |
| UNG trade/volume quality | Alpaca | Historical fallback | Yes |
| Henry Hub NG | Yahoo chart | Stooq / other configured source | Strongly preferred |
| Second NG contract | Yahoo when explicitly configured | None | Preferred |
| Storage | EIA API | None | Context |
| Weather | NWS API | None | Context |
| Level 2/order book | Not required | None | No |

Alpaca provides minute bars and Level-1 bid/ask quotes for US equities/ETFs. The free IEX feed is not the same as a consolidated SIP feed, so the engine records feed identity and does not treat IEX as full-market NBBO evidence.

EIA provides natural-gas storage, production, consumption, imports/exports, and Henry Hub datasets. EIA API access requires an API key, so the integration is optional and explicitly marked missing when no key is configured.

NWS provides hourly forecast data through the public weather API. Weather is contextual demand-pressure evidence, not a standalone directional signal.

## V2 components

- Microstructure: bid/ask spread and quote sizes.
- Intraday volatility quality: realized volatility from current UNG minute bars.
- UNG/NG coherence: relationship between UNG and Henry Hub returns.
- Volume-price quality: participation confirmation.
- Term structure: front/next NG relationship when a second contract is explicitly configured.
- Context quality: EIA storage and NWS weather when configured.

## Coverage governance

MQI score, MQI confidence, and data coverage are separate values.

Missing data is never converted into a neutral score. Missing components reduce coverage and confidence. If primary intraday UNG data is unavailable or stale, MQI becomes unavailable/degraded rather than using an old daily score as a substitute for live evidence.

The engine records score, confidence, coverage, source status, source names, missing components, component status, and freshness.

## Environment variables

Required for primary non-IBKR market data:

- ALPACA_API_KEY_ID
- ALPACA_API_SECRET_KEY
- ALPACA_DATA_FEED (iex by default; sip requires the appropriate Alpaca subscription)

Optional context:

- EIA_API_KEY
- NG_WEATHER_POINTS as JSON, for example:
  [{"name":"Chicago","lat":41.88,"lon":-87.63},{"name":"Houston","lat":29.76,"lon":-95.37}]

## V8 integration

Before each official forecast, the Streamlit app refreshes the external MQI bundle and attaches it to the decision engine. MQI remains a quality/confirmation layer only. It does not override cost-basis protection, create orders, or authorize shorting.
