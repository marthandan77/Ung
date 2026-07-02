from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
import json
import math
import os
import warnings
from statistics import mean, pstdev
from typing import Any

os.environ.setdefault("LOKY_MAX_CPU_COUNT", "1")

try:
    import numpy as np
except Exception:  # pragma: no cover - optional dependency
    np = None

try:
    from hmmlearn.hmm import GaussianHMM
except Exception:  # pragma: no cover - optional dependency
    GaussianHMM = None

try:
    from arch import arch_model
except Exception:  # pragma: no cover - optional dependency
    arch_model = None


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
    reentry_min_probability: float = 62.0
    mur_max_dollars: float = 0.16
    mqi_min: float = 55.0
    meaningful_rebuy_drop: float = 0.15
    opening_range_minutes: int = 30
    warmup_bars: int = 35
    ewma_lambda: float = 0.94
    spread_warning_pct: float = 0.006
    hmm_components: int = 4
    hmm_min_samples: int = 240
    hmm_fit_interval_bars: int = 60
    markov_min_transitions: int = 20
    garch_min_returns: int = 90
    garch_fit_interval_bars: int = 30


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
    markov_status: str = "NOT_READY"
    markov_current_state: str = "NOT_READY"
    markov_next_state_probabilities: dict[str, float] = field(default_factory=dict)
    regime_persistence_probability: float | None = None
    transition_warning: str = "NONE"
    garch_status: str = "FALLBACK_EWMA"
    next_period_volatility_forecast: float = 0.0
    volatility_percentile: float = 0.0
    harvest_zone: bool = False
    near_rebuy_zone: bool = False
    bearish_continuation_risk: bool = False
    key_level_hit: str = "none"
    dynamic_rebuy_gap: float = 0.0
    expected_rebuy_price: float | None = None
    he: float = 0.0
    hc: float = 0.0
    rs: float = 0.0
    rp: float = 0.0
    bc: float = 0.0
    mqi: float = 0.0
    mur: float = 0.0
    rte: float = 0.0
    hold_ev: float = 0.0
    sell_buyback_ev: float = 0.0
    ev_ranking: str = "HOLD"

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


class DecisionEngineV8RTIS:
    """Signal-only UNG Round-Trip Intelligence System.

    V8 asks whether a full SELL -> WAIT -> BUYBACK cycle has better expected
    value than holding. It never places orders and never shorts UNG.
    """

    HOLD = "HOLD"
    SELL_WATCH = "SELL_WATCH"
    SELL_READY = "SELL_READY"
    SOLD_WAIT = "SOLD_WAIT"
    BUYBACK_WATCH = "BUYBACK_WATCH"
    BUYBACK_READY = "BUYBACK_READY"
    WAIT = "WAIT"
    PROTECT = "PROTECT"

    ACTION_STATES = {SELL_WATCH, SELL_READY, BUYBACK_WATCH, BUYBACK_READY, PROTECT}

    def __init__(self, config: EngineConfig | None = None):
        self.config = config or EngineConfig()
        self.bars: deque[MarketBar] = deque(maxlen=1200)
        self.closes: deque[float] = deque(maxlen=1200)
        self.highs: deque[float] = deque(maxlen=1200)
        self.lows: deque[float] = deque(maxlen=1200)
        self.volumes: deque[float] = deque(maxlen=1200)
        self.true_ranges: deque[float] = deque(maxlen=420)
        self.returns: deque[float] = deque(maxlen=2500)
        self.realized_vol_history: deque[float] = deque(maxlen=800)
        self.feature_rows: deque[tuple[datetime, list[float]]] = deque(maxlen=5000)
        self.regime_sequence: deque[int] = deque(maxlen=1500)

        self.current_day = None
        self.bar_count = 0
        self.session_bar_count = 0
        self.session_vwap_value = 0.0
        self.session_volume = 0.0
        self.opening_range_high: float | None = None
        self.opening_range_low: float | None = None
        self.ema_fast: float | None = None
        self.ema_slow: float | None = None
        self.last_close: float | None = None
        self.ewma_variance: float | None = None

        self.virtual_position_qty = int(self.config.position_qty)
        self.starting_position_qty = int(self.config.position_qty)
        self.last_sell_price: float | None = None
        self.expected_rebuy_price: float | None = None
        self.last_state: str | None = None
        self.last_alert_key: str | None = None

        self.hmm_model: Any = None
        self.hmm_state_labels: dict[int, str] = {}
        self.hmm_status = "NOT_READY"
        self.hmm_last_fit_time: str = "NOT_READY"
        self.markov_status = "NOT_READY"
        self.markov_transition_matrix: dict[int, dict[int, float]] = {}
        self.garch_status = "NOT_READY"
        self.garch_forecast = 0.0

    def update(self, bar: MarketBar, emit_alerts: bool = True) -> Decision:
        self._update_indicators(bar)
        snapshot = self._build_feature_snapshot(bar)
        self._fit_hmm_if_ready(bar.timestamp)
        self._apply_real_hmm(snapshot)
        self._apply_real_markov(snapshot)
        self._apply_real_garch_or_ewma(snapshot)
        self._score(snapshot)
        decision = self._decide(snapshot)
        if emit_alerts:
            self._mark_alert(decision)
        self._apply_virtual_signal(decision)
        self._store_feature_for_training(snapshot)
        self.last_state = decision.state
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

        self.bar_count += 1
        price = float(bar.close)
        volume = max(float(bar.volume), 0.0)
        self.session_bar_count += 1
        self.session_vwap_value += price * volume
        self.session_volume += volume

        if self.session_bar_count <= self.config.opening_range_minutes:
            self.opening_range_high = price if self.opening_range_high is None else max(self.opening_range_high, bar.high)
            self.opening_range_low = price if self.opening_range_low is None else min(self.opening_range_low, bar.low)

        if self.last_close is not None and self.last_close > 0 and price > 0:
            ret = math.log(price / self.last_close)
            self.returns.append(ret)
            self.ewma_variance = self._update_ewma_variance(ret)

        previous = self.last_close or price
        true_range = max(float(bar.high) - float(bar.low), abs(float(bar.high) - previous), abs(float(bar.low) - previous))
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
        spread = None if bar.bid is None or bar.ask is None else max(0.0, bar.ask - bar.bid)
        profit_per_share = price - self.config.average_cost
        dynamic_gap = max(self.config.meaningful_rebuy_drop, atr * 0.65, price * 0.006)
        expected_rebuy = None if self.last_sell_price is None else self.last_sell_price - dynamic_gap

        return FeatureSnapshot(
            timestamp=bar.timestamp,
            symbol=self.config.symbol,
            price=price,
            bid=bar.bid,
            ask=bar.ask,
            spread=spread,
            position_qty=self.virtual_position_qty,
            average_cost=self.config.average_cost,
            unrealized_profit=profit_per_share * self.virtual_position_qty,
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
            opening_range_status=self._opening_range_status(price),
            vwap_distance=safe_div(price - vwap, price),
            dynamic_rebuy_gap=dynamic_gap,
            expected_rebuy_price=expected_rebuy,
        )

    def _score(self, s: FeatureSnapshot) -> None:
        price = s.price
        atr = max(s.atr, price * 0.002)
        profit_gate = s.profit_per_share >= self.config.minimum_harvest_profit
        harvest_profit = max(0.0, s.profit_per_share)
        trend_strength = clamp(s.ema_slope / atr, -1.0, 1.0)
        volume_strength = clamp((s.volume_ratio - 1.0) / 1.5, 0.0, 1.0)
        rsi_hot = clamp((s.rsi - 55.0) / 20.0, 0.0, 1.0)
        extended_from_vwap = price >= s.vwap + max(atr * 0.35, price * 0.003)
        near_upper_band = s.bb_position >= 0.72
        opening_strength = s.opening_range_status in {"ABOVE_OPENING_RANGE", "NEAR_OPENING_HIGH"}
        support = min(s.vwap, s.bb_mid, s.opening_range_low)
        near_support = price <= support + max(atr * 0.35, price * 0.003)
        pullback_ok = price <= s.vwap - max(atr * 0.20, price * 0.002) or s.bb_position <= 0.45
        bearish_continuation = s.ema_slope < -atr * 0.20 and price < s.vwap - atr * 0.50 and s.volume_ratio > 1.25

        s.harvest_zone = profit_gate and (extended_from_vwap or near_upper_band or opening_strength)
        s.near_rebuy_zone = near_support or pullback_ok
        s.bearish_continuation_risk = bearish_continuation
        s.key_level_hit = self._key_level_hit(s)

        s.bc = round(100.0 * clamp(
            0.30 * max(trend_strength, 0.0)
            + 0.25 * volume_strength
            + 0.25 * clamp((s.vwap_distance - 0.002) / 0.012, 0.0, 1.0)
            + 0.20 * (1.0 if opening_strength else 0.0),
            0.0,
            1.0,
        ), 2)
        s.mur = round(clamp(s.bc / 100.0, 0.0, 1.0) * max(atr * 0.75, self.config.minimum_harvest_profit), 4)
        s.rp = round(100.0 * clamp(
            0.30 * (1.0 if near_support else 0.0)
            + 0.25 * (1.0 if pullback_ok else 0.0)
            + 0.20 * clamp((55.0 - s.rsi) / 25.0, 0.0, 1.0)
            + 0.15 * clamp(s.atr_pct / 0.018, 0.0, 1.0)
            + 0.10 * (0.0 if bearish_continuation else 1.0),
            0.0,
            1.0,
        ), 2)
        s.he = round(100.0 * clamp(
            0.45 * clamp((s.profit_per_share - self.config.minimum_harvest_profit) / 0.35, 0.0, 1.0)
            + 0.20 * (1.0 if s.harvest_zone else 0.0)
            + 0.15 * rsi_hot
            + 0.10 * volume_strength
            + 0.10 * s.bb_position,
            0.0,
            1.0,
        ), 2)
        s.hc = round(100.0 * clamp((1.0 if profit_gate else 0.0) * (0.35 + 0.25 * max(trend_strength, 0) + 0.20 * volume_strength + 0.20 * (1 if s.harvest_zone else 0)), 0.0, 1.0), 2)

        spread_penalty = 0.0
        if s.spread is not None and price > 0:
            spread_penalty = clamp((s.spread / price) / self.config.spread_warning_pct, 0.0, 1.0) * 35.0
        s.mqi = round(clamp(80.0 + volume_strength * 15.0 - spread_penalty - (20.0 if bearish_continuation else 0.0), 0.0, 100.0), 2)
        s.rs = round(100.0 * clamp(
            0.45 * (1.0 - clamp(s.realized_volatility / 0.025, 0.0, 1.0))
            + 0.25 * (1.0 if abs(trend_strength) <= 0.65 else 0.5)
            + 0.20 * (1.0 if s.model_status == "READY" else 0.5)
            + 0.10 * (1.0 if s.transition_warning == "NONE" else 0.2),
            0.0,
            1.0,
        ), 2)

        expected_buyback_discount = s.dynamic_rebuy_gap * (s.rp / 100.0)
        reentry_failure_risk = (1.0 - s.rp / 100.0) * max(s.dynamic_rebuy_gap, atr * 0.50)
        s.hold_ev = round(max(0.0, s.bc / 100.0 * atr * 0.75) + max(0.0, trend_strength) * atr * 0.20, 4)
        s.sell_buyback_ev = round(harvest_profit + expected_buyback_discount - s.mur - reentry_failure_risk, 4)
        s.rte = round(s.sell_buyback_ev - s.hold_ev, 4)
        s.ev_ranking = "SELL_BUYBACK" if s.sell_buyback_ev > s.hold_ev else "HOLD"

    def _decide(self, s: FeatureSnapshot) -> Decision:
        if len(self.closes) < self.config.warmup_bars:
            return Decision(self.WAIT, f"warming up {len(self.closes)}/{self.config.warmup_bars} bars", "warmup", s)
        if s.mqi < 40:
            return Decision(self.WAIT, "market quality too weak for a clean forecast alert", "MQI", s)
        if s.position_qty > 0 and s.profit_per_share < 0:
            return Decision(self.PROTECT, "cost-basis protection: below average cost; no harvest sell signal allowed", "cost basis", s)

        if s.position_qty > 0:
            sell_ready = (
                s.profit_per_share >= self.config.minimum_harvest_profit
                and s.harvest_zone
                and s.mur <= self.config.mur_max_dollars
                and s.rp >= self.config.reentry_min_probability
                and s.sell_buyback_ev > s.hold_ev
                and s.mqi >= self.config.mqi_min
            )
            if sell_ready:
                return Decision(self.SELL_READY, "round-trip EV beats hold EV and re-entry odds are acceptable", "RTE", s)
            if s.profit_per_share >= self.config.minimum_harvest_profit:
                if s.harvest_zone:
                    return Decision(self.SELL_WATCH, "harvest zone reached but RTIS filters have not all cleared", "harvest zone", s)
                return Decision(self.SELL_WATCH, "minimum profit reached but harvest zone rejected", "harvest rejected", s)
            return Decision(self.HOLD, "holding: no positive round-trip edge over hold", "hold EV", s)

        if self.last_sell_price is None:
            return Decision(self.WAIT, "flat state without prior sell price; buyback logic locked", "last sell", s)
        price_discount = self.last_sell_price - s.price
        buyback_ready = (
            price_discount >= s.dynamic_rebuy_gap
            and s.near_rebuy_zone
            and not s.bearish_continuation_risk
            and s.mqi >= self.config.mqi_min
        )
        if buyback_ready:
            return Decision(self.BUYBACK_READY, "buyback discount reached near support/VWAP with acceptable market quality", "buyback gap", s)
        if price_discount >= s.dynamic_rebuy_gap * 0.45:
            return Decision(self.BUYBACK_WATCH, "sold and waiting for cleaner support/VWAP buyback confirmation", "buyback watch", s)
        return Decision(self.SOLD_WAIT, "sold state: waiting for discount and market-quality confirmation", "sold wait", s)

    def _apply_virtual_signal(self, decision: Decision) -> None:
        if decision.state == self.SELL_READY and self.virtual_position_qty > 0:
            self.last_sell_price = decision.snapshot.price
            self.expected_rebuy_price = decision.snapshot.price - decision.snapshot.dynamic_rebuy_gap
            self.virtual_position_qty = 0
        elif decision.state == self.BUYBACK_READY and self.virtual_position_qty == 0:
            self.virtual_position_qty = self.starting_position_qty

    def _mark_alert(self, decision: Decision) -> None:
        prev = self.last_state
        state_changed = prev is not None and state_changed_meaningful(prev, decision.state)
        first_action = prev is None and decision.state in self.ACTION_STATES
        alert_key = f"{decision.state}:{decision.key_level}:{decision.trigger_reason}:{decision.snapshot.price:.2f}"
        decision.alert = bool((first_action or state_changed) and alert_key != self.last_alert_key)
        if decision.alert:
            self.last_alert_key = alert_key
            decision.alert_text = self.format_alert(decision)

    def format_alert(self, decision: Decision) -> str:
        s = decision.snapshot
        bid_ask = "not available" if s.bid is None or s.ask is None else f"{s.bid:.2f} / {s.ask:.2f}"
        regime_probs = "NOT_READY" if not s.regime_probabilities else json.dumps(s.regime_probabilities, sort_keys=True)
        return "\n".join(
            [
                f"UNG V8 RTIS ALERT | signal={decision.state}",
                f"Price: {s.price:.2f} | Bid/Ask: {bid_ask}",
                f"Virtual position: {s.position_qty} shares @ {s.average_cost:.4f}",
                f"Profit/share: {s.profit_per_share:.2f} | Unrealized: {s.unrealized_profit:.2f}",
                f"Reason: {decision.trigger_reason}",
                f"Regime: {s.regime_label} | probs={regime_probs}",
                f"Markov: {s.markov_current_state} | warning={s.transition_warning}",
                f"RTE {s.rte:.3f} | HE {s.he:.1f} | RP {s.rp:.1f} | MUR {s.mur:.3f} | MQI {s.mqi:.1f}",
                f"EV ranking: {s.ev_ranking}",
                "Logic: forecast signal only; no live order, no shorting.",
            ]
        )

    def _fit_hmm_if_ready(self, timestamp: datetime) -> None:
        if GaussianHMM is None or np is None:
            self.hmm_status = "NOT_READY"
            self.hmm_model = None
            return
        if len(self.feature_rows) < self.config.hmm_min_samples:
            self.hmm_status = "NOT_READY"
            return
        if self.bar_count % max(1, self.config.hmm_fit_interval_bars) != 0 and self.hmm_model is not None:
            return
        try:
            matrix = np.array([row for _, row in self.feature_rows], dtype=float)
            if not np.isfinite(matrix).all():
                self.hmm_status = "NOT_READY"
                return
            components = min(max(2, int(self.config.hmm_components)), max(2, len(self.feature_rows) // 60))
            model = GaussianHMM(n_components=components, covariance_type="diag", n_iter=100, random_state=7)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                warnings.simplefilter("ignore", UserWarning)
                model.fit(matrix)
                states = model.predict(matrix)
            self.hmm_model = model
            self.hmm_state_labels = self._label_hmm_states(matrix, states)
            self.hmm_status = "READY"
            self.hmm_last_fit_time = timestamp.isoformat()
        except Exception:
            self.hmm_status = "NOT_READY"
            self.hmm_model = None

    def _apply_real_hmm(self, s: FeatureSnapshot) -> None:
        if self.hmm_model is None or self.hmm_status != "READY" or np is None:
            s.model_status = "NOT_READY"
            s.regime_state_id = "NOT_READY"
            s.regime_probabilities = {}
            s.regime_label = "NOT_READY"
            s.last_fit_time = self.hmm_last_fit_time
            return
        try:
            vector = np.array([self._feature_vector(s)], dtype=float)
            probabilities = self.hmm_model.predict_proba(vector)[0]
            if not np.isfinite(probabilities).all():
                raise ValueError("non-finite HMM probabilities")
            state_id = int(self.hmm_model.predict(vector)[0])
            s.model_status = "READY"
            s.regime_state_id = f"STATE_{state_id}"
            s.regime_label = self.hmm_state_labels.get(state_id, f"STATE_{state_id}")
            s.last_fit_time = self.hmm_last_fit_time
            s.regime_probabilities = {
                f"state_{idx}_{self.hmm_state_labels.get(idx, f'STATE_{idx}')}": round(float(prob), 4)
                for idx, prob in enumerate(probabilities)
            }
            self.regime_sequence.append(state_id)
        except Exception:
            s.model_status = "NOT_READY"
            s.regime_state_id = "NOT_READY"
            s.regime_probabilities = {}
            s.regime_label = "NOT_READY"

    def _apply_real_markov(self, s: FeatureSnapshot) -> None:
        if s.model_status != "READY" or len(self.regime_sequence) < self.config.markov_min_transitions:
            self.markov_status = "NOT_READY"
            s.markov_status = "NOT_READY"
            s.markov_current_state = "NOT_READY"
            s.markov_next_state_probabilities = {}
            s.transition_warning = "NONE"
            return
        counts: dict[int, dict[int, int]] = {}
        seq = list(self.regime_sequence)
        for current, nxt in zip(seq[:-1], seq[1:]):
            counts.setdefault(current, {})
            counts[current][nxt] = counts[current].get(nxt, 0) + 1
        matrix = {current: {nxt: count / sum(row.values()) for nxt, count in row.items()} for current, row in counts.items() if sum(row.values())}
        self.markov_transition_matrix = matrix
        self.markov_status = "READY"
        current_state = seq[-1]
        transitions = matrix.get(current_state, {})
        s.markov_status = "READY"
        s.markov_current_state = f"STATE_{current_state}"
        s.markov_next_state_probabilities = {
            f"STATE_{state}_{self.hmm_state_labels.get(state, f'STATE_{state}')}": round(prob, 4)
            for state, prob in transitions.items()
        }
        s.regime_persistence_probability = transitions.get(current_state)
        s.transition_warning = "NONE"
        if transitions:
            next_state, probability = max(transitions.items(), key=lambda item: item[1])
            next_label = self.hmm_state_labels.get(next_state, f"STATE_{next_state}")
            current_label = self.hmm_state_labels.get(current_state, f"STATE_{current_state}")
            if next_state != current_state and probability >= 0.55:
                s.transition_warning = f"SHIFT_TO_{next_label}_{probability:.2f}"
            elif current_label == "HIGH_VOL" and probability >= 0.55:
                s.transition_warning = f"HIGH_VOL_PERSISTENCE_{probability:.2f}"

    def _apply_real_garch_or_ewma(self, s: FeatureSnapshot) -> None:
        if len(self.returns) < 30:
            self.garch_status = "NOT_READY"
            self.garch_forecast = 0.0
            s.garch_status = "NOT_READY"
            s.next_period_volatility_forecast = 0.0
            return
        fallback = math.sqrt(max(self.ewma_variance or 0.0, 0.0))
        s.garch_status = "FALLBACK_EWMA"
        s.next_period_volatility_forecast = fallback
        self.garch_status = "FALLBACK_EWMA"
        self.garch_forecast = fallback
        if arch_model is None or np is None or len(self.returns) < self.config.garch_min_returns:
            return
        if self.garch_status == "READY" and self.bar_count % max(1, self.config.garch_fit_interval_bars) != 0:
            s.garch_status = "READY"
            s.next_period_volatility_forecast = self.garch_forecast
            return
        try:
            returns_pct = np.array(list(self.returns)[-500:], dtype=float) * 100.0
            model = arch_model(returns_pct, vol="Garch", p=1, q=1, mean="Zero", rescale=False)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                warnings.simplefilter("ignore", UserWarning)
                fit = model.fit(disp="off")
            forecast = fit.forecast(horizon=1)
            variance = float(forecast.variance.values[-1, 0])
            self.garch_forecast = math.sqrt(max(variance, 0.0)) / 100.0
            self.garch_status = "READY"
            s.garch_status = "READY"
            s.next_period_volatility_forecast = self.garch_forecast
        except Exception:
            self.garch_status = "FALLBACK_EWMA"
            self.garch_forecast = fallback
            s.garch_status = "FALLBACK_EWMA"
            s.next_period_volatility_forecast = fallback

    def _store_feature_for_training(self, s: FeatureSnapshot) -> None:
        row = self._feature_vector(s)
        if all(self._is_finite(value) for value in row):
            self.feature_rows.append((s.timestamp, row))

    def _feature_vector(self, s: FeatureSnapshot) -> list[float]:
        log_return = self.returns[-1] if self.returns else 0.0
        return [log_return, s.realized_volatility, s.volume_ratio, s.vwap_distance, (s.rsi - 50.0) / 50.0, s.atr_pct]

    def _label_hmm_states(self, matrix: Any, states: Any) -> dict[int, str]:
        if np is None:
            return {}
        labels: dict[int, str] = {}
        vol_75 = float(np.percentile(matrix[:, 1], 75))
        for state_id in sorted({int(value) for value in states}):
            rows = matrix[states == state_id]
            mean_return = float(np.mean(rows[:, 0]))
            mean_vol = float(np.mean(rows[:, 1]))
            mean_vwap_distance = float(np.mean(rows[:, 3]))
            if mean_vol >= vol_75:
                label = "HIGH_VOL"
            elif mean_return > 0 and mean_vwap_distance > 0:
                label = "UP_DRIFT"
            elif mean_return < 0 and mean_vwap_distance < 0:
                label = "DOWN_DRIFT"
            else:
                label = "SIDEWAYS"
            labels[state_id] = label
        return labels

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
        return mid, upper, lower, clamp(safe_div(price - lower, upper - lower, 0.5), 0, 1)

    def _realized_volatility(self) -> float:
        returns = list(self.returns)[-30:]
        if len(returns) < 2:
            return 0.0
        return pstdev(returns) * math.sqrt(390)

    def _volatility_percentile(self, current: float) -> float:
        history = [x for x in self.realized_vol_history if x > 0]
        if len(history) < 20 or current <= 0:
            return 50.0
        return round(100 * sum(1 for x in history if x <= current) / len(history), 2)

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
        width = max(high - low, 0.0001)
        if self.session_bar_count <= self.config.opening_range_minutes:
            return "BUILDING"
        if price > high:
            return "ABOVE_OPENING_RANGE"
        if price >= high - width * 0.20:
            return "NEAR_OPENING_HIGH"
        if price < low:
            return "BELOW_OPENING_RANGE"
        if price <= low + width * 0.20:
            return "NEAR_OPENING_LOW"
        return "INSIDE_OPENING_RANGE"

    def _key_level_hit(self, s: FeatureSnapshot) -> str:
        if s.price >= s.bb_upper:
            return "upper Bollinger"
        if s.price <= s.bb_lower:
            return "lower Bollinger"
        if abs(s.price - s.vwap) <= max(s.atr * 0.15, s.price * 0.0015):
            return "VWAP"
        if s.opening_range_status in {"ABOVE_OPENING_RANGE", "BELOW_OPENING_RANGE"}:
            return s.opening_range_status
        if self.last_sell_price is not None and s.price <= self.last_sell_price - s.dynamic_rebuy_gap:
            return "rebuy gap"
        return "none"

    def _short_rebound(self) -> bool:
        values = list(self.closes)
        if len(values) < 6:
            return False
        recent_low = min(values[-6:])
        return values[-1] > recent_low and values[-1] >= values[-2]

    def _update_ewma_variance(self, ret: float) -> float:
        if self.ewma_variance is None:
            return ret * ret
        lam = clamp(self.config.ewma_lambda, 0.01, 0.99)
        return lam * self.ewma_variance + (1 - lam) * ret * ret

    def _is_finite(self, value: float) -> bool:
        try:
            number = float(value)
            return not math.isnan(number) and not math.isinf(number)
        except Exception:
            return False


def state_changed_meaningful(previous: str, current: str) -> bool:
    actionable = DecisionEngineV8RTIS.ACTION_STATES
    return previous != current and (previous in actionable or current in actionable)


DecisionEngineV7Lite = DecisionEngineV8RTIS
