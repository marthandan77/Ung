from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
from statistics import mean, pstdev
from typing import Any


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return numerator / denominator if denominator else default


@dataclass
class EngineConfig:
    symbol: str = "UNG"
    position_qty: int = 30_900
    average_cost: float = 11.5453
    minimum_harvest_profit: float = 0.10
    preferred_harvest_min: float = 0.10
    preferred_harvest_max: float = 0.50
    meaningful_rebuy_drop: float = 0.15
    opening_range_minutes: int = 30
    warmup_bars: int = 35
    ewma_lambda: float = 0.94
    spread_warning_pct: float = 0.006


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
class FeatureSnapshot:
    timestamp: datetime
    symbol: str
    price: float
    bid: float | None
    ask: float | None
    spread: float | None
    position_qty: int
    average_cost: float
    unrealized_profit: float
    profit_per_share: float
    vwap: float
    atr: float
    atr_pct: float
    rsi: float
    ema_fast: float
    ema_slow: float
    ema_slope: float
    bb_mid: float
    bb_upper: float
    bb_lower: float
    bb_position: float
    realized_volatility: float
    volume_ratio: float
    opening_range_high: float
    opening_range_low: float
    opening_range_status: str
    vwap_distance: float
    model_status: str = "NOT_READY"
    regime_state_id: str = "NOT_READY"
    regime_probabilities: dict[str, float] = field(default_factory=dict)
    regime_label: str = "NOT_READY"
    last_fit_time: str = "NOT_READY"
    markov_current_state: str = "NOT_READY"
    markov_next_state_probabilities: dict[str, float] = field(default_factory=dict)
    regime_persistence_probability: float | None = None
    transition_warning: str = "NONE"
    garch_status: str = "FALLBACK_EWMA"
    next_period_volatility_forecast: float = 0.0
    volatility_percentile: float = 0.0
    he: float = 0.0
    hc: float = 0.0
    rs: float = 0.0
    rp: float = 0.0
    bc: float = 0.0
    mqi: float = 0.0
    ev_ranking: str = "WAIT"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = self.timestamp.isoformat()
        return payload


@dataclass
class Decision:
    state: str
    trigger_reason: str
    key_level: str
    snapshot: FeatureSnapshot
    alert: bool = False
    alert_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "trigger_reason": self.trigger_reason,
            "key_level": self.key_level,
            "alert": self.alert,
            "alert_text": self.alert_text,
            "snapshot": self.snapshot.to_dict(),
        }


class DecisionEngineV7Lite:
    """Signal-only UNG decision engine.

    This class intentionally keeps HMM and GARCH truth separate from observable
    indicator scoring. Phase 1 never emits fake model probabilities.
    """

    ACTION_STATES = {"BUY", "ACCUMULATE", "HARVEST_WATCH", "HARVEST", "REBUY_WATCH", "REBUY", "WAIT"}

    def __init__(self, config: EngineConfig | None = None):
        self.config = config or EngineConfig()
        self.bars: deque[MarketBar] = deque(maxlen=420)
        self.closes: deque[float] = deque(maxlen=420)
        self.highs: deque[float] = deque(maxlen=420)
        self.lows: deque[float] = deque(maxlen=420)
        self.volumes: deque[float] = deque(maxlen=420)
        self.true_ranges: deque[float] = deque(maxlen=80)
        self.returns: deque[float] = deque(maxlen=160)
        self.realized_vol_history: deque[float] = deque(maxlen=500)

        self.current_day = None
        self.session_bar_count = 0
        self.session_vwap_value = 0.0
        self.session_volume = 0.0
        self.opening_range_high: float | None = None
        self.opening_range_low: float | None = None
        self.ema_fast: float | None = None
        self.ema_slow: float | None = None
        self.last_close: float | None = None
        self.ewma_variance: float | None = None
        self.last_state: str | None = None
        self.last_alert_key: str | None = None
        self.prior_harvest_price: float | None = None

    def update(self, bar: MarketBar, emit_alerts: bool = True) -> Decision:
        self._update_indicators(bar)
        snapshot = self._build_feature_snapshot(bar)
        self._score(snapshot)
        decision = self._decide(snapshot)
        if emit_alerts:
            self._mark_alert(decision)
        self.last_state = decision.state
        if decision.state == "HARVEST":
            self.prior_harvest_price = snapshot.price
        return decision

    def _reset_session(self, bar: MarketBar) -> None:
        self.current_day = bar.timestamp.date()
        self.session_bar_count = 0
        self.session_vwap_value = 0.0
        self.session_volume = 0.0
        self.opening_range_high = None
        self.opening_range_low = None
        self.ema_fast = None
        self.ema_slow = None

    def _update_indicators(self, bar: MarketBar) -> None:
        if self.current_day != bar.timestamp.date():
            self._reset_session(bar)

        price = float(bar.close)
        volume = max(float(bar.volume), 0.0)
        self.session_bar_count += 1
        self.session_vwap_value += price * volume
        self.session_volume += volume

        if self.session_bar_count <= self.config.opening_range_minutes:
            self.opening_range_high = price if self.opening_range_high is None else max(self.opening_range_high, bar.high)
            self.opening_range_low = price if self.opening_range_low is None else min(self.opening_range_low, bar.low)

        if self.last_close is not None and self.last_close > 0:
            ret = math.log(price / self.last_close)
            self.returns.append(ret)
            self.ewma_variance = self._update_ewma_variance(ret)

        true_range = max(
            float(bar.high) - float(bar.low),
            abs(float(bar.high) - (self.last_close or float(bar.close))),
            abs(float(bar.low) - (self.last_close or float(bar.close))),
        )
        self.true_ranges.append(true_range)
        self.ema_fast = self._ema(self.ema_fast, price, 9)
        self.ema_slow = self._ema(self.ema_slow, price, 21)

        self.bars.append(bar)
        self.closes.append(price)
        self.highs.append(float(bar.high))
        self.lows.append(float(bar.low))
        self.volumes.append(volume)
        self.last_close = price

    def _build_feature_snapshot(self, bar: MarketBar) -> FeatureSnapshot:
        price = float(bar.close)
        vwap = safe_div(self.session_vwap_value, self.session_volume, price)
        atr = mean(list(self.true_ranges)[-14:]) if self.true_ranges else 0.0
        atr_pct = safe_div(atr, price)
        rsi = self._rsi(14)
        bb_mid, bb_upper, bb_lower, bb_position = self._bollinger(price)
        realized_vol = self._realized_volatility()
        self.realized_vol_history.append(realized_vol)
        volume_ratio = self._volume_ratio()
        opening_status = self._opening_range_status(price)
        spread = None if bar.bid is None or bar.ask is None else max(0.0, bar.ask - bar.bid)
        profit_per_share = price - self.config.average_cost
        unrealized_profit = profit_per_share * self.config.position_qty
        forecast = math.sqrt(max(self.ewma_variance or 0.0, 0.0))
        garch_status = "FALLBACK_EWMA" if self.returns else "NOT_READY"

        return FeatureSnapshot(
            timestamp=bar.timestamp,
            symbol=self.config.symbol,
            price=price,
            bid=bar.bid,
            ask=bar.ask,
            spread=spread,
            position_qty=self.config.position_qty,
            average_cost=self.config.average_cost,
            unrealized_profit=unrealized_profit,
            profit_per_share=profit_per_share,
            vwap=vwap,
            atr=atr,
            atr_pct=atr_pct,
            rsi=rsi,
            ema_fast=self.ema_fast or price,
            ema_slow=self.ema_slow or price,
            ema_slope=(self.ema_fast or price) - (self.ema_slow or price),
            bb_mid=bb_mid,
            bb_upper=bb_upper,
            bb_lower=bb_lower,
            bb_position=bb_position,
            realized_volatility=realized_vol,
            volume_ratio=volume_ratio,
            opening_range_high=self.opening_range_high or price,
            opening_range_low=self.opening_range_low or price,
            opening_range_status=opening_status,
            vwap_distance=safe_div(price - vwap, price),
            garch_status=garch_status,
            next_period_volatility_forecast=forecast,
            volatility_percentile=self._volatility_percentile(realized_vol),
        )

    def _score(self, s: FeatureSnapshot) -> None:
        ready_factor = 1.0 if len(self.closes) >= self.config.warmup_bars else 0.35
        profit_range = max(0.01, self.config.preferred_harvest_max - self.config.preferred_harvest_min)
        profit_score = clamp((s.profit_per_share - self.config.minimum_harvest_profit) / profit_range, 0, 1) * 45
        extension_score = clamp(s.vwap_distance / max(0.004, s.atr_pct * 0.9), 0, 1) * 18
        rsi_harvest_score = clamp((s.rsi - 55) / 18, 0, 1) * 14
        volume_score = clamp((s.volume_ratio - 0.9) / 1.4, 0, 1) * 10
        trend_score = 8 if s.ema_fast >= s.ema_slow else 0
        opening_score = 5 if s.opening_range_status == "ABOVE_OPENING_RANGE" else 0
        s.he = round((profit_score + extension_score + rsi_harvest_score + volume_score + trend_score + opening_score) * ready_factor, 2)

        cost_ok = 1.0 if s.profit_per_share >= self.config.minimum_harvest_profit else 0.0
        structure_ok = 0.35
        if s.price >= s.vwap and s.ema_fast >= s.ema_slow:
            structure_ok += 0.35
        if s.opening_range_status in {"ABOVE_OPENING_RANGE", "INSIDE_OPENING_RANGE"}:
            structure_ok += 0.15
        if s.rsi >= 52:
            structure_ok += 0.15
        s.hc = round(100 * cost_ok * clamp(structure_ok, 0, 1) * ready_factor, 2)

        calm_score = 100 - clamp(s.volatility_percentile, 0, 100) * 0.35
        trend_stability = 20 if s.ema_fast >= s.ema_slow else 5
        s.rs = round(clamp(calm_score + trend_stability, 0, 100) * ready_factor, 2)

        rebuy_drop = self._rebuy_gap(s)
        below_vwap = clamp(-s.vwap_distance / max(0.004, s.atr_pct), 0, 1) * 30
        rsi_rebuy = clamp((48 - s.rsi) / 18, 0, 1) * 25
        bounce = 20 if self._short_rebound() else 0
        s.rp = round(clamp(rebuy_drop + below_vwap + rsi_rebuy + bounce, 0, 100) * ready_factor, 2)

        breakout = 35 if s.opening_range_status == "ABOVE_OPENING_RANGE" else 0
        s.bc = round(clamp(breakout + trend_score * 3 + volume_score * 2 + max(0, s.vwap_distance) * 700, 0, 100) * ready_factor, 2)

        spread_penalty = 0.0
        if s.spread is not None:
            spread_pct = safe_div(s.spread, s.price)
            spread_penalty = clamp(spread_pct / self.config.spread_warning_pct, 0, 1) * 25
        s.mqi = round(clamp(45 + min(25, s.volume_ratio * 10) + min(20, len(self.closes)) - spread_penalty, 0, 100), 2)
        s.ev_ranking = self._rank_ev(s)

    def _decide(self, s: FeatureSnapshot) -> Decision:
        if len(self.closes) < self.config.warmup_bars:
            return Decision("WAIT", f"warming up {len(self.closes)}/{self.config.warmup_bars} bars", "warmup", s)

        if s.mqi < 40:
            return Decision("WAIT", "market quality too weak for a clean alert", "MQI", s)

        if s.position_qty <= 0:
            gap = self.prior_harvest_price - s.price if self.prior_harvest_price else 0.0
            if self.prior_harvest_price and gap >= max(self.config.meaningful_rebuy_drop, s.atr * 0.6):
                if s.rp >= 70 and self._short_rebound():
                    return Decision("REBUY", "price is below prior harvest and rebound confirmed", f"prior harvest {self.prior_harvest_price:.2f}", s)
                return Decision("REBUY_WATCH", "price is below prior harvest but confirmation is incomplete", f"prior harvest {self.prior_harvest_price:.2f}", s)
            return Decision("WAIT", "no position and no confirmed rebuy setup", "flat", s)

        if s.profit_per_share >= self.config.minimum_harvest_profit:
            if s.he >= 70 and s.hc >= 65:
                return Decision("HARVEST", "profit zone confirmed by VWAP, trend, and volume structure", f"profit/share {s.profit_per_share:.2f}", s)
            return Decision("HARVEST_WATCH", "profit zone reached but confirmation is not strong enough", f"profit/share {s.profit_per_share:.2f}", s)

        if s.price < s.average_cost:
            deep_discount = s.vwap_distance <= -max(0.006, s.atr_pct * 0.7)
            if deep_discount and s.rsi <= 42 and self._short_rebound():
                return Decision("BUY", "discount below VWAP with rebound confirmation", f"VWAP {s.vwap:.2f}", s)
            if deep_discount or s.rsi <= 38:
                return Decision("ACCUMULATE", "discount forming but rebound confirmation is incomplete", f"VWAP {s.vwap:.2f}", s)
            return Decision("HOLD", "below cost basis; no profit harvest allowed", f"average cost {s.average_cost:.4f}", s)

        if s.bc >= 70 and s.profit_per_share < self.config.minimum_harvest_profit:
            return Decision("HOLD", "breakout is improving but minimum harvest profit is not reached", f"profit/share {s.profit_per_share:.2f}", s)

        return Decision("HOLD", "no actionable harvest or buy setup", f"profit/share {s.profit_per_share:.2f}", s)

    def _mark_alert(self, decision: Decision) -> None:
        state = decision.state
        prev = self.last_state
        actionable_first = prev is None and state in {"BUY", "ACCUMULATE", "HARVEST_WATCH", "HARVEST", "REBUY_WATCH", "REBUY"}
        state_changed = prev is not None and state != prev
        meaningful = state in self.ACTION_STATES or prev in self.ACTION_STATES if prev else actionable_first
        alert_key = f"{state}:{decision.key_level}:{decision.trigger_reason}"
        decision.alert = bool((actionable_first or (state_changed and meaningful)) and alert_key != self.last_alert_key)
        if decision.alert:
            self.last_alert_key = alert_key
            decision.alert_text = self.format_alert(decision)

    def format_alert(self, decision: Decision) -> str:
        s = decision.snapshot
        bid_ask = "not available" if s.bid is None or s.ask is None else f"{s.bid:.2f} / {s.ask:.2f}"
        regime_probs = "NOT_READY" if not s.regime_probabilities else json.dumps(s.regime_probabilities, sort_keys=True)
        return "\n".join(
            [
                f"UNG ALERT {decision.state}",
                f"Price: {s.price:.2f} | Bid/Ask: {bid_ask}",
                f"Position: {s.position_qty} shares @ {s.average_cost:.4f}",
                f"Profit/share: {s.profit_per_share:.2f} | Unrealized: {s.unrealized_profit:.2f}",
                f"Reason: {decision.trigger_reason}",
                f"Regime probabilities: {regime_probs}",
                f"Markov: {s.markov_current_state} | Warning: {s.transition_warning}",
                f"Key level: {decision.key_level}",
                f"HE {s.he:.1f} | HC {s.hc:.1f} | MQI {s.mqi:.1f}",
                f"EV ranking: {s.ev_ranking}",
                f"Logic: {self._plain_logic(decision.state)}",
            ]
        )

    def _plain_logic(self, state: str) -> str:
        if state == "HARVEST":
            return "Long-only harvest alert. Signal only. No order sent."
        if state == "HARVEST_WATCH":
            return "Profit zone reached. Wait for stronger confirmation."
        if state in {"BUY", "ACCUMULATE"}:
            return "Long-only dip alert. Buy only if trader confirms."
        if state in {"REBUY", "REBUY_WATCH"}:
            return "Re-entry watch after prior harvest. Signal only."
        return "Hold or wait. No shorting."

    def _ema(self, current: float | None, price: float, period: int) -> float:
        if current is None:
            return price
        k = 2 / (period + 1)
        return price * k + current * (1 - k)

    def _rsi(self, period: int) -> float:
        values = list(self.closes)
        if len(values) <= period:
            return 50.0
        changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
        gains = [x for x in changes if x > 0]
        losses = [-x for x in changes if x < 0]
        avg_gain = mean(gains) if gains else 0.0
        avg_loss = mean(losses) if losses else 0.0
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _bollinger(self, price: float) -> tuple[float, float, float, float]:
        values = list(self.closes)[-20:]
        if len(values) < 20:
            return price, price, price, 0.5
        mid = mean(values)
        sd = pstdev(values) or 0.0
        upper = mid + 2 * sd
        lower = mid - 2 * sd
        position = safe_div(price - lower, upper - lower, 0.5)
        return mid, upper, lower, position

    def _realized_volatility(self) -> float:
        returns = list(self.returns)[-30:]
        if len(returns) < 2:
            return 0.0
        return pstdev(returns) * math.sqrt(390)

    def _volatility_percentile(self, current: float) -> float:
        history = [x for x in self.realized_vol_history if x > 0]
        if len(history) < 20 or current <= 0:
            return 50.0
        below = sum(1 for x in history if x <= current)
        return round(100 * below / len(history), 2)

    def _volume_ratio(self) -> float:
        values = list(self.volumes)
        if len(values) < 20:
            return 1.0
        baseline = mean(values[-21:-1]) or 1.0
        return values[-1] / baseline if baseline else 1.0

    def _opening_range_status(self, price: float) -> str:
        high = self.opening_range_high
        low = self.opening_range_low
        if high is None or low is None:
            return "NOT_READY"
        if self.session_bar_count <= self.config.opening_range_minutes:
            return "BUILDING"
        if price > high:
            return "ABOVE_OPENING_RANGE"
        if price < low:
            return "BELOW_OPENING_RANGE"
        return "INSIDE_OPENING_RANGE"

    def _short_rebound(self) -> bool:
        values = list(self.closes)
        if len(values) < 6:
            return False
        recent_low = min(values[-6:])
        return values[-1] > recent_low and values[-1] >= values[-2]

    def _rebuy_gap(self, s: FeatureSnapshot) -> float:
        if not self.prior_harvest_price:
            return 0.0
        needed = max(self.config.meaningful_rebuy_drop, s.atr * 0.6, 0.01)
        return clamp((self.prior_harvest_price - s.price) / needed, 0, 1) * 25

    def _rank_ev(self, s: FeatureSnapshot) -> str:
        pairs = [
            ("HARVEST", s.he * 0.55 + s.hc * 0.45),
            ("REBUY", s.rp),
            ("BREAKOUT", s.bc),
            ("MARKET_QUALITY", s.mqi),
        ]
        pairs.sort(key=lambda item: item[1], reverse=True)
        return " > ".join(name for name, _ in pairs)

    def _update_ewma_variance(self, ret: float) -> float:
        if self.ewma_variance is None:
            return ret * ret
        lam = clamp(self.config.ewma_lambda, 0.01, 0.99)
        return lam * self.ewma_variance + (1 - lam) * ret * ret
