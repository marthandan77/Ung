from AlgorithmImports import *
from datetime import datetime
import math


class UngDecisionEngineV8RTIS(QCAlgorithm):
    """UNG V8 RTIS: signal-only round-trip expectancy engine.

    RTIS = Round-Trip Intelligence System. The engine asks one plain question:
    is a full SELL -> WAIT -> BUYBACK cycle worth more than simply holding?
    No live orders are submitted by this file.
    """

    HOLD = "HOLD"
    SELL_WATCH = "SELL_WATCH"
    SELL_READY = "SELL_READY"
    SOLD_WAIT = "SOLD_WAIT"
    BUYBACK_WATCH = "BUYBACK_WATCH"
    BUYBACK_READY = "BUYBACK_READY"
    WAIT = "WAIT"
    PROTECT = "PROTECT"

    def Initialize(self):
        self.SetStartDate(2024, 1, 1)
        self.SetEndDate(2026, 6, 15)
        self.SetCash(100000)

        equity = self.AddEquity("UNG", Resolution.Minute)
        self.symbol = equity.Symbol
        self.Securities[self.symbol].SetDataNormalizationMode(DataNormalizationMode.Adjusted)

        self.starting_position_qty = int(float(self.GetParameter("position_qty") or 30900))
        self.position_qty = self.starting_position_qty
        self.average_cost = float(self.GetParameter("average_cost") or 11.5453)
        self.min_harvest_profit = float(self.GetParameter("min_harvest_profit") or 0.10)
        self.reentry_min_probability = float(self.GetParameter("reentry_min_probability") or 62.0)
        self.mur_max_dollars = float(self.GetParameter("mur_max_dollars") or 0.16)
        self.mqi_min = float(self.GetParameter("mqi_min") or 55.0)
        self.hmm_min_samples = int(float(self.GetParameter("hmm_min_samples") or 240))
        self.hmm_fit_every_bars = int(float(self.GetParameter("hmm_fit_every_bars") or 120))

        self.SetWarmUp(80, Resolution.Minute)
        self.rsi_indicator = self.RSI(self.symbol, 14, MovingAverageType.Wilders, Resolution.Minute)
        self.atr_indicator = self.ATR(self.symbol, 14, MovingAverageType.Wilders, Resolution.Minute)
        self.ema_fast_indicator = self.EMA(self.symbol, 9, Resolution.Minute)
        self.ema_slow_indicator = self.EMA(self.symbol, 21, Resolution.Minute)
        self.bb_indicator = self.BB(self.symbol, 20, 2, MovingAverageType.Simple, Resolution.Minute)

        self.day = None
        self.bar_count = 0
        self.session_bar_count = 0
        self.closes = []
        self.highs = []
        self.lows = []
        self.volumes = []
        self.returns = []
        self.true_ranges = []
        self.vwap_value = 0.0
        self.vwap_volume = 0.0
        self.opening_high = None
        self.opening_low = None
        self.last_close = None
        self.ewma_var = None

        self.last_sell_price = None
        self.dynamic_rebuy_gap = self.min_harvest_profit
        self.expected_rebuy_price = None
        self.last_decision_state = None
        self.last_alert_key = None
        self.last_regime_label = None
        self.last_markov_warning = "NONE"
        self.last_harvest_zone = False
        self.last_ev_rank = None
        self.last_rte = None
        self.last_mqi = None
        self.last_bc = None
        self.last_garch_status = "NOT_READY"

        self.feature_rows = []
        self.hmm_model = None
        self.hmm_state_labels = {}
        self.hmm_status = "NOT_READY"
        self.hmm_last_fit_time = None
        self.regime_sequence = []
        self.markov_transition_matrix = {}
        self.markov_status = "NOT_READY"
        self.garch_status = "NOT_READY"
        self.garch_vol_forecast = None

        self.signal_journal = []
        self.Debug(
            "UNG V8 RTIS initialized. Signal-only, long-only, no live orders. "
            "RTIS compares SELL->WAIT->BUYBACK EV against HOLD EV."
        )

    def OnData(self, data):
        bar = self.GetBar(data)
        if bar is None:
            return

        self.UpdateIndicators(bar)
        if self.IsWarmingUp or not self.IndicatorsReady():
            return

        snapshot = self.BuildFeatureSnapshot(bar)
        self.UpdateRealHMM(snapshot)
        self.UpdateMarkovTransitionMatrix(snapshot)
        self.UpdateRealGARCHOrFallbackVol(snapshot)
        self.ScoreSnapshot(snapshot)

        state, reason, key = self.DecisionEngineV8(snapshot)
        self.RecordSignalJournal(snapshot, state, reason)
        self.EmitAlertLog(snapshot, state, reason, key)
        self.ApplyVirtualSignal(state, snapshot)
        self.StoreFeatureForTraining(snapshot)
        self.FitOrLoadModels(snapshot)

    def GetBar(self, data):
        try:
            if data.Bars.ContainsKey(self.symbol):
                return data.Bars[self.symbol]
        except Exception:
            pass
        try:
            return data.Bars.get(self.symbol)
        except Exception:
            return None

    def UpdateIndicators(self, bar):
        self.bar_count += 1
        price = float(bar.Close)
        high = float(bar.High)
        low = float(bar.Low)
        volume = max(0.0, float(bar.Volume))

        if self.day != self.Time.date():
            self.day = self.Time.date()
            self.session_bar_count = 0
            self.vwap_value = 0.0
            self.vwap_volume = 0.0
            self.opening_high = None
            self.opening_low = None

        self.session_bar_count += 1
        if self.session_bar_count <= 30:
            self.opening_high = high if self.opening_high is None else max(self.opening_high, high)
            self.opening_low = low if self.opening_low is None else min(self.opening_low, low)

        if self.last_close and self.last_close > 0 and price > 0:
            ret = math.log(price / self.last_close)
            self.returns = (self.returns + [ret])[-1200:]
            self.ewma_var = ret * ret if self.ewma_var is None else 0.94 * self.ewma_var + 0.06 * ret * ret

        prev_close = self.last_close or price
        true_range = max(high - low, abs(high - prev_close), abs(low - prev_close))
        self.true_ranges = (self.true_ranges + [true_range])[-420:]
        self.vwap_value += price * volume
        self.vwap_volume += volume
        self.closes = (self.closes + [price])[-1200:]
        self.highs = (self.highs + [high])[-1200:]
        self.lows = (self.lows + [low])[-1200:]
        self.volumes = (self.volumes + [volume])[-1200:]
        self.last_close = price

    def IndicatorsReady(self):
        checks = [
            self.rsi_indicator.IsReady,
            self.atr_indicator.IsReady,
            self.ema_fast_indicator.IsReady,
            self.ema_slow_indicator.IsReady,
            self.bb_indicator.IsReady,
            len(self.closes) >= 35,
            len(self.volumes) >= 20,
            self.vwap_volume > 0,
        ]
        return all(checks)

    def BuildFeatureSnapshot(self, bar):
        price = float(bar.Close)
        bid, ask, spread = self.BidAskSpread()
        vwap = self.vwap_value / self.vwap_volume if self.vwap_volume else price
        atr = max(0.0, float(self.atr_indicator.Current.Value))
        rsi = float(self.rsi_indicator.Current.Value)
        ema_fast = float(self.ema_fast_indicator.Current.Value)
        ema_slow = float(self.ema_slow_indicator.Current.Value)
        bb_mid = float(self.bb_indicator.MiddleBand.Current.Value)
        bb_upper = float(self.bb_indicator.UpperBand.Current.Value)
        bb_lower = float(self.bb_indicator.LowerBand.Current.Value)
        bb_width = max(bb_upper - bb_lower, 0.0001)
        bb_position = self.Clamp((price - bb_lower) / bb_width, 0.0, 1.0)
        volume_ratio = self.VolumeRatio()
        realized_vol = self.RealizedVol()
        atr_pct = atr / price if price > 0 else 0.0
        vwap_distance = (price - vwap) / price if price > 0 else 0.0
        profit_per_share = price - self.average_cost
        opening_range_status = self.OpeningRangeStatus(price)
        log_return = self.returns[-1] if self.returns else 0.0

        dynamic_rebuy_gap = max(self.min_harvest_profit, atr * 0.65, price * 0.006)
        self.dynamic_rebuy_gap = dynamic_rebuy_gap
        expected_rebuy_price = None
        if self.last_sell_price is not None:
            expected_rebuy_price = self.last_sell_price - dynamic_rebuy_gap
        self.expected_rebuy_price = expected_rebuy_price

        return {
            "timestamp": self.Time,
            "price": price,
            "bid": bid,
            "ask": ask,
            "spread": spread,
            "position_qty": self.position_qty,
            "average_cost": self.average_cost,
            "unrealized_profit": profit_per_share * self.position_qty,
            "profit_per_share": profit_per_share,
            "vwap": vwap,
            "atr": atr,
            "atr_pct": atr_pct,
            "rsi": rsi,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_slope": ema_fast - ema_slow,
            "bb_mid": bb_mid,
            "bb_upper": bb_upper,
            "bb_lower": bb_lower,
            "bb_position": bb_position,
            "volume_ratio": volume_ratio,
            "realized_volatility": realized_vol,
            "opening_range_high": self.opening_high or price,
            "opening_range_low": self.opening_low or price,
            "opening_range_status": opening_range_status,
            "vwap_distance": vwap_distance,
            "log_return": log_return,
            "regime_state_id": None,
            "regime_probabilities": {},
            "regime_label": "NOT_READY",
            "model_status": "NOT_READY",
            "last_fit_time": self.hmm_last_fit_time,
            "markov_status": "NOT_READY",
            "markov_current_state": "NOT_READY",
            "transition_warning": "NONE",
            "volatility_forecast": 0.0,
            "garch_status": "NOT_READY",
            "dynamic_rebuy_gap": dynamic_rebuy_gap,
            "expected_rebuy_price": expected_rebuy_price,
        }

    def BidAskSpread(self):
        bid = None
        ask = None
        try:
            security = self.Securities[self.symbol]
            bid = float(security.BidPrice) if security.BidPrice else None
            ask = float(security.AskPrice) if security.AskPrice else None
        except Exception:
            pass
        spread = ask - bid if bid is not None and ask is not None and ask >= bid else None
        return bid, ask, spread

    def FitOrLoadModels(self, snapshot):
        if self.bar_count % self.hmm_fit_every_bars != 0:
            return

        try:
            import numpy as np
            from hmmlearn.hmm import GaussianHMM
        except Exception:
            self.hmm_status = "NOT_READY"
            self.hmm_model = None
            return

        pool = self.TrainingPool()
        if len(pool) < self.hmm_min_samples:
            self.hmm_status = "NOT_READY"
            return

        try:
            x = np.array([row[1] for row in pool], dtype=float)
            if not np.isfinite(x).all():
                self.hmm_status = "NOT_READY"
                return
            model = GaussianHMM(n_components=4, covariance_type="diag", n_iter=80, random_state=7)
            model.fit(x)
            states = model.predict(x)
            self.hmm_model = model
            self.hmm_state_labels = self.BuildRegimeLabels(x, states)
            self.hmm_status = "READY"
            self.hmm_last_fit_time = self.Time
        except Exception as err:
            self.hmm_status = "NOT_READY"
            self.hmm_model = None
            self.Debug(f"HMM NOT_READY: real hmmlearn fit failed: {err}")

    def TrainingPool(self):
        if self.Time.year <= 2024:
            return [(t, x) for t, x in self.feature_rows if t.year == 2024 and t < self.Time]
        if self.Time.year == 2025:
            return [(t, x) for t, x in self.feature_rows if t.year == 2024]
        return [(t, x) for t, x in self.feature_rows if t < self.Time][-2500:]

    def StoreFeatureForTraining(self, s):
        row = [
            s["log_return"],
            s["realized_volatility"],
            s["volume_ratio"],
            s["vwap_distance"],
            (s["rsi"] - 50.0) / 50.0,
            s["atr_pct"],
        ]
        if all(self.IsFinite(x) for x in row):
            self.feature_rows.append((self.Time, row))
            self.feature_rows = self.feature_rows[-10000:]

    def BuildRegimeLabels(self, x, states):
        try:
            import numpy as np
        except Exception:
            return {}
        labels = {}
        vol_75 = float(np.percentile(x[:, 1], 75))
        for state_id in sorted(set(int(v) for v in states)):
            mask = states == state_id
            rows = x[mask]
            mean_ret = float(np.mean(rows[:, 0]))
            mean_vol = float(np.mean(rows[:, 1]))
            mean_vwap_dist = float(np.mean(rows[:, 3]))
            if mean_vol >= vol_75:
                label = "HIGH_VOL"
            elif mean_ret > 0 and mean_vwap_dist > 0:
                label = "UP_DRIFT"
            elif mean_ret < 0 and mean_vwap_dist < 0:
                label = "DOWN_DRIFT"
            else:
                label = "SIDEWAYS"
            labels[state_id] = label
        return labels

    def UpdateRealHMM(self, snapshot):
        if self.hmm_model is None or self.hmm_status != "READY":
            snapshot["model_status"] = "NOT_READY"
            snapshot["regime_probabilities"] = {}
            snapshot["regime_label"] = "NOT_READY"
            snapshot["regime_state_id"] = None
            snapshot["last_fit_time"] = self.hmm_last_fit_time
            return snapshot

        try:
            import numpy as np
            x = np.array([self.FeatureVector(snapshot)], dtype=float)
            probs = self.hmm_model.predict_proba(x)[0]
            state_id = int(self.hmm_model.predict(x)[0])
            label = self.hmm_state_labels.get(state_id, f"STATE_{state_id}")
            prob_map = {}
            for i, prob in enumerate(probs):
                state_label = self.hmm_state_labels.get(i, f"STATE_{i}")
                prob_map[f"state_{i}_{state_label}"] = float(prob)
            snapshot["model_status"] = "READY"
            snapshot["regime_probabilities"] = prob_map
            snapshot["regime_state_id"] = state_id
            snapshot["regime_label"] = label
            snapshot["last_fit_time"] = self.hmm_last_fit_time
            self.regime_sequence.append(state_id)
            self.regime_sequence = self.regime_sequence[-1200:]
        except Exception as err:
            snapshot["model_status"] = "NOT_READY"
            snapshot["regime_probabilities"] = {}
            snapshot["regime_label"] = "NOT_READY"
            snapshot["regime_state_id"] = None
            self.Debug(f"HMM NOT_READY: inference failed: {err}")
        return snapshot

    def UpdateMarkovTransitionMatrix(self, snapshot):
        if snapshot["model_status"] != "READY" or len(self.regime_sequence) < 20:
            snapshot["markov_status"] = "NOT_READY"
            snapshot["markov_current_state"] = "NOT_READY"
            snapshot["transition_warning"] = "NONE"
            return snapshot

        counts = {}
        for a, b in zip(self.regime_sequence[:-1], self.regime_sequence[1:]):
            counts.setdefault(a, {})
            counts[a][b] = counts[a].get(b, 0) + 1

        matrix = {}
        for a, row in counts.items():
            total = float(sum(row.values()))
            matrix[a] = {b: count / total for b, count in row.items()} if total else {}
        self.markov_transition_matrix = matrix
        self.markov_status = "READY"

        current = snapshot["regime_state_id"]
        transitions = matrix.get(current, {})
        warning = "NONE"
        if transitions:
            next_state, prob = max(transitions.items(), key=lambda item: item[1])
            current_label = self.hmm_state_labels.get(current, f"STATE_{current}")
            next_label = self.hmm_state_labels.get(next_state, f"STATE_{next_state}")
            if next_state != current and prob >= 0.55:
                warning = f"SHIFT_TO_{next_label}_{prob:.2f}"
            elif current_label == "HIGH_VOL" and prob >= 0.55:
                warning = f"HIGH_VOL_PERSISTENCE_{prob:.2f}"

        snapshot["markov_status"] = "READY"
        snapshot["markov_current_state"] = f"STATE_{current}"
        snapshot["transition_warning"] = warning
        return snapshot

    def UpdateRealGARCHOrFallbackVol(self, snapshot):
        if len(self.returns) < 30:
            snapshot["garch_status"] = "NOT_READY"
            snapshot["volatility_forecast"] = 0.0
            return snapshot

        fallback = math.sqrt(max(self.ewma_var or 0.0, 0.0))
        snapshot["garch_status"] = "FALLBACK_EWMA"
        snapshot["volatility_forecast"] = fallback

        if len(self.returns) < 90 or self.bar_count % 30 != 0:
            return snapshot

        try:
            import numpy as np
            from arch import arch_model
            returns_pct = np.array(self.returns[-500:], dtype=float) * 100.0
            model = arch_model(returns_pct, vol="Garch", p=1, q=1, mean="Zero", rescale=False)
            fit = model.fit(disp="off")
            forecast = fit.forecast(horizon=1)
            variance = float(forecast.variance.values[-1, 0])
            snapshot["garch_status"] = "READY"
            snapshot["volatility_forecast"] = math.sqrt(max(variance, 0.0)) / 100.0
        except Exception:
            snapshot["garch_status"] = "FALLBACK_EWMA"
            snapshot["volatility_forecast"] = fallback
        return snapshot

    def ScoreSnapshot(self, s):
        price = s["price"]
        atr = max(s["atr"], price * 0.002)
        harvest_profit = max(0.0, s["profit_per_share"])
        profit_gate = s["profit_per_share"] >= self.min_harvest_profit
        extended_from_vwap = s["price"] >= s["vwap"] + max(atr * 0.35, price * 0.003)
        near_upper_band = s["bb_position"] >= 0.72
        opening_strength = s["opening_range_status"] in ["ABOVE_OPENING_RANGE", "NEAR_OPENING_HIGH"]
        harvest_zone = profit_gate and (extended_from_vwap or near_upper_band or opening_strength)

        trend_strength = self.Clamp(s["ema_slope"] / atr, -1.0, 1.0)
        volume_strength = self.Clamp((s["volume_ratio"] - 1.0) / 1.5, 0.0, 1.0)
        rsi_hot = self.Clamp((s["rsi"] - 55.0) / 20.0, 0.0, 1.0)
        bc = 100.0 * self.Clamp(
            0.30 * max(trend_strength, 0.0)
            + 0.25 * volume_strength
            + 0.25 * self.Clamp((s["vwap_distance"] - 0.002) / 0.012, 0.0, 1.0)
            + 0.20 * (1.0 if opening_strength else 0.0),
            0.0,
            1.0,
        )

        mur = self.Clamp(bc / 100.0, 0.0, 1.0) * max(atr * 0.75, self.min_harvest_profit)
        support = min(s["vwap"], s["bb_mid"], s["opening_range_low"])
        near_support = price <= support + max(atr * 0.35, price * 0.003)
        pullback_ok = price <= s["vwap"] - max(atr * 0.20, price * 0.002) or s["bb_position"] <= 0.45
        bearish_continuation = (
            s["ema_slope"] < -atr * 0.20
            and s["price"] < s["vwap"] - atr * 0.50
            and s["volume_ratio"] > 1.25
        )
        rp = 100.0 * self.Clamp(
            0.30 * (1.0 if near_support else 0.0)
            + 0.25 * (1.0 if pullback_ok else 0.0)
            + 0.20 * self.Clamp((55.0 - s["rsi"]) / 25.0, 0.0, 1.0)
            + 0.15 * self.Clamp(s["atr_pct"] / 0.018, 0.0, 1.0)
            + 0.10 * (0.0 if bearish_continuation else 1.0),
            0.0,
            1.0,
        )

        he = 100.0 * self.Clamp(
            0.45 * self.Clamp((s["profit_per_share"] - self.min_harvest_profit) / 0.35, 0.0, 1.0)
            + 0.20 * (1.0 if harvest_zone else 0.0)
            + 0.15 * rsi_hot
            + 0.10 * volume_strength
            + 0.10 * s["bb_position"],
            0.0,
            1.0,
        )

        spread_penalty = 0.0
        if s["spread"] is not None and price > 0:
            spread_penalty = self.Clamp((s["spread"] / price) / 0.006, 0.0, 1.0) * 35.0
        mqi = self.Clamp(80.0 + volume_strength * 15.0 - spread_penalty - (20.0 if bearish_continuation else 0.0), 0.0, 100.0)
        rs = 100.0 * self.Clamp(
            0.45 * (1.0 - self.Clamp(s["realized_volatility"] / 0.025, 0.0, 1.0))
            + 0.25 * (1.0 if abs(trend_strength) <= 0.65 else 0.5)
            + 0.20 * (1.0 if s["model_status"] == "READY" else 0.5)
            + 0.10 * (1.0 if s["transition_warning"] == "NONE" else 0.2),
            0.0,
            1.0,
        )

        expected_buyback_discount = s["dynamic_rebuy_gap"] * (rp / 100.0)
        reentry_failure_risk = (1.0 - rp / 100.0) * max(s["dynamic_rebuy_gap"], atr * 0.50)
        sell_buyback_ev = harvest_profit + expected_buyback_discount - mur - reentry_failure_risk
        hold_ev = max(0.0, bc / 100.0 * atr * 0.75) + max(0.0, trend_strength) * atr * 0.20
        rte = sell_buyback_ev - hold_ev

        s["harvest_profit"] = harvest_profit
        s["expected_buyback_discount"] = expected_buyback_discount
        s["reentry_failure_risk"] = reentry_failure_risk
        s["harvest_zone"] = harvest_zone
        s["near_rebuy_zone"] = near_support or pullback_ok
        s["bearish_continuation_risk"] = bearish_continuation
        s["key_level_hit"] = self.KeyLevelHit(s)
        s["RTE"] = rte
        s["HE"] = he
        s["RP"] = rp
        s["MUR"] = mur
        s["BC"] = bc
        s["MQI"] = mqi
        s["RS"] = rs
        s["hold_ev"] = hold_ev
        s["sell_buyback_ev"] = sell_buyback_ev
        s["ev_ranking"] = "SELL_BUYBACK" if sell_buyback_ev > hold_ev else "HOLD"
        s["volatility_forecast"] = max(s.get("volatility_forecast", 0.0), 0.0)
        return s

    def DecisionEngineV8(self, s):
        if s["position_qty"] > 0 and s["profit_per_share"] < 0:
            return self.PROTECT, "cost-basis protection: below average cost; no harvest sell allowed", "cost basis"

        if s["position_qty"] > 0:
            sell_ready = (
                s["profit_per_share"] >= self.min_harvest_profit
                and s["harvest_zone"]
                and s["MUR"] <= self.mur_max_dollars
                and s["RP"] >= self.reentry_min_probability
                and s["sell_buyback_ev"] > s["hold_ev"]
                and s["MQI"] >= self.mqi_min
            )
            if sell_ready:
                return self.SELL_READY, "round-trip EV beats hold EV and re-entry odds are acceptable", "RTE"
            if s["profit_per_share"] >= self.min_harvest_profit:
                if s["harvest_zone"]:
                    return self.SELL_WATCH, "harvest zone reached but RTIS filters have not all cleared", "harvest zone"
                return self.SELL_WATCH, "minimum profit reached but harvest zone rejected", "harvest rejected"
            return self.HOLD, "holding: no positive round-trip edge over hold", "hold EV"

        if self.last_sell_price is None:
            return self.WAIT, "flat state without prior sell price; buyback logic locked", "last sell"

        price_discount = self.last_sell_price - s["price"]
        buyback_ready = (
            price_discount >= s["dynamic_rebuy_gap"]
            and s["near_rebuy_zone"]
            and not s["bearish_continuation_risk"]
            and s["MQI"] >= self.mqi_min
        )
        if buyback_ready:
            return self.BUYBACK_READY, "buyback discount reached near support/VWAP with acceptable market quality", "buyback gap"
        if price_discount >= s["dynamic_rebuy_gap"] * 0.45:
            return self.BUYBACK_WATCH, "sold and waiting for cleaner support/VWAP buyback confirmation", "buyback watch"
        return self.SOLD_WAIT, "sold state: waiting for discount and market-quality confirmation", "sold wait"

    def ApplyVirtualSignal(self, state, s):
        if state == self.SELL_READY and self.position_qty > 0:
            self.last_sell_price = s["price"]
            self.expected_rebuy_price = s["price"] - s["dynamic_rebuy_gap"]
            self.position_qty = 0
            self.Debug(
                f"VIRTUAL SELL SIGNAL ONLY price={s['price']:.2f} shares={self.starting_position_qty} "
                f"expected_rebuy={self.expected_rebuy_price:.2f}. No live order sent."
            )
        elif state == self.BUYBACK_READY and self.position_qty == 0:
            self.position_qty = self.starting_position_qty
            self.Debug(
                f"VIRTUAL BUYBACK SIGNAL ONLY price={s['price']:.2f} shares={self.starting_position_qty}. "
                "No live order sent."
            )

    def RecordSignalJournal(self, s, state, reason):
        row = {
            "timestamp": str(s["timestamp"]),
            "price": s["price"],
            "bid": s["bid"],
            "ask": s["ask"],
            "spread": s["spread"],
            "position_qty": s["position_qty"],
            "average_cost": s["average_cost"],
            "unrealized_profit": s["unrealized_profit"],
            "profit_per_share": s["profit_per_share"],
            "VWAP": s["vwap"],
            "ATR": s["atr"],
            "RSI": s["rsi"],
            "EMA_slope": s["ema_slope"],
            "Bollinger_position": s["bb_position"],
            "volume_ratio": s["volume_ratio"],
            "opening_range_status": s["opening_range_status"],
            "HMM_state": s["regime_state_id"],
            "HMM_probabilities": s["regime_probabilities"],
            "Markov_transition_warning": s["transition_warning"],
            "volatility_forecast": s["volatility_forecast"],
            "RTE": s["RTE"],
            "HE": s["HE"],
            "RP": s["RP"],
            "MUR": s["MUR"],
            "BC": s["BC"],
            "MQI": s["MQI"],
            "RS": s["RS"],
            "hold_ev": s["hold_ev"],
            "sell_buyback_ev": s["sell_buyback_ev"],
            "decision_state": state,
            "trigger_reason": reason,
            "last_sell_price": self.last_sell_price,
            "dynamic_rebuy_gap": s["dynamic_rebuy_gap"],
            "expected_rebuy_price": s["expected_rebuy_price"],
            "model_status": s["model_status"],
            "garch_status": s["garch_status"],
            "markov_status": s["markov_status"],
        }
        self.signal_journal.append(row)
        if self.Time.minute % 30 == 0:
            self.Debug(
                f"JOURNAL {row['timestamp']} price={s['price']:.2f} state={state} "
                f"RTE={s['RTE']:.3f} HE={s['HE']:.1f} RP={s['RP']:.1f} MUR={s['MUR']:.3f} "
                f"MQI={s['MQI']:.1f} EV={s['ev_ranking']} reason={reason}"
            )

    def EmitAlertLog(self, s, state, reason, key):
        events = []
        prev = self.last_decision_state
        if prev is not None:
            transition = (prev, state)
            allowed = [
                (self.HOLD, self.SELL_WATCH),
                (self.SELL_WATCH, self.SELL_READY),
                (self.SELL_READY, self.SOLD_WAIT),
                (self.SOLD_WAIT, self.BUYBACK_WATCH),
                (self.BUYBACK_WATCH, self.BUYBACK_READY),
            ]
            if transition in allowed:
                events.append(f"{prev}->{state}")
            if state == self.PROTECT and prev != self.PROTECT:
                events.append("PROTECT")

        if s["model_status"] == "READY" and self.last_regime_label not in (None, s["regime_label"]):
            events.append("regime shift")
        if s["transition_warning"] != "NONE" and s["transition_warning"] != self.last_markov_warning:
            events.append("Markov transition warning")
        if s["harvest_zone"] and not self.last_harvest_zone:
            events.append("harvest zone reached")
        if s["profit_per_share"] >= self.min_harvest_profit and not s["harvest_zone"] and self.last_harvest_zone:
            events.append("harvest zone rejected")
        if s["BC"] >= 70 and (self.last_bc is None or self.last_bc < 70):
            events.append("breakout accepted")
        if self.last_bc is not None and self.last_bc >= 70 and s["BC"] < 55:
            events.append("breakout failed")
        if s["price"] < s["vwap"] - max(s["atr"] * 0.75, s["price"] * 0.006):
            events.append("breakdown trigger hit")
        if state == self.PROTECT:
            events.append("cost-basis protection triggered")
        if self.last_ev_rank is not None and s["ev_ranking"] != self.last_ev_rank:
            events.append("EV ranking change")
        if self.last_rte is not None and self.last_rte > 0 and s["RTE"] <= 0:
            events.append("RTE deterioration")
        if self.last_mqi is not None and self.last_mqi >= self.mqi_min and s["MQI"] < self.mqi_min:
            events.append("MQI deterioration")
        if self.last_garch_status not in (None, "NOT_READY") and s["garch_status"] != self.last_garch_status:
            events.append("forecast invalidation")

        alert_key = f"{state}:{key}:{'|'.join(events)}"
        if events and alert_key != self.last_alert_key:
            self.last_alert_key = alert_key
            self.Debug(
                "UNG V8 ALERT | "
                f"events={';'.join(events)} | price={s['price']:.2f} | bid={self.Fmt(s['bid'])} ask={self.Fmt(s['ask'])} | "
                f"position={s['position_qty']} avg_cost={s['average_cost']:.4f} | signal={state} | reason={reason} | "
                f"regime={s['regime_label']} probs={s['regime_probabilities']} | markov={s['transition_warning']} | "
                f"key_level={s['key_level_hit']} | RTE={s['RTE']:.3f} HE={s['HE']:.1f} RP={s['RP']:.1f} "
                f"MUR={s['MUR']:.3f} MQI={s['MQI']:.1f} | EV={s['ev_ranking']} | "
                "logic=long-only signal: harvest only when SELL->WAIT->BUYBACK EV beats HOLD EV; no shorting; no live order"
            )

        self.last_decision_state = state
        self.last_regime_label = s["regime_label"]
        self.last_markov_warning = s["transition_warning"]
        self.last_harvest_zone = s["harvest_zone"]
        self.last_ev_rank = s["ev_ranking"]
        self.last_rte = s["RTE"]
        self.last_mqi = s["MQI"]
        self.last_bc = s["BC"]
        self.last_garch_status = s["garch_status"]

    def FeatureVector(self, s):
        return [
            s["log_return"],
            s["realized_volatility"],
            s["volume_ratio"],
            s["vwap_distance"],
            (s["rsi"] - 50.0) / 50.0,
            s["atr_pct"],
        ]

    def VolumeRatio(self):
        if len(self.volumes) < 20:
            return 1.0
        recent = self.volumes[-1]
        baseline = sum(self.volumes[-20:]) / 20.0
        return recent / baseline if baseline > 0 else 1.0

    def RealizedVol(self):
        if len(self.returns) < 20:
            return 0.0
        mean = sum(self.returns[-20:]) / 20.0
        variance = sum((x - mean) ** 2 for x in self.returns[-20:]) / 20.0
        return math.sqrt(max(variance, 0.0))

    def OpeningRangeStatus(self, price):
        high = self.opening_high or price
        low = self.opening_low or price
        width = max(high - low, 0.0001)
        if price > high:
            return "ABOVE_OPENING_RANGE"
        if price >= high - width * 0.20:
            return "NEAR_OPENING_HIGH"
        if price < low:
            return "BELOW_OPENING_RANGE"
        if price <= low + width * 0.20:
            return "NEAR_OPENING_LOW"
        return "INSIDE_OPENING_RANGE"

    def KeyLevelHit(self, s):
        if s["price"] >= s["bb_upper"]:
            return "upper Bollinger"
        if s["price"] <= s["bb_lower"]:
            return "lower Bollinger"
        if abs(s["price"] - s["vwap"]) <= max(s["atr"] * 0.15, s["price"] * 0.0015):
            return "VWAP"
        if s["opening_range_status"] in ["ABOVE_OPENING_RANGE", "BELOW_OPENING_RANGE"]:
            return s["opening_range_status"]
        if self.last_sell_price is not None and s["price"] <= self.last_sell_price - s["dynamic_rebuy_gap"]:
            return "rebuy gap"
        return "none"

    def IsFinite(self, value):
        try:
            return value is not None and not math.isnan(float(value)) and not math.isinf(float(value))
        except Exception:
            return False

    def Clamp(self, value, low, high):
        return max(low, min(high, value))

    def Fmt(self, value):
        return "NA" if value is None else f"{value:.2f}"

    def OnEndOfAlgorithm(self):
        self.Debug(
            f"UNG V8 RTIS complete. journal_rows={len(self.signal_journal)} "
            f"HMM={self.hmm_status} Markov={self.markov_status} GARCH={self.last_garch_status}. "
            "No live orders were placed."
        )
