from __future__ import annotations

from typing import Any

from .engine import Decision, DecisionEngineV8RTIS


READY = "READY"


def engine_health(engine: DecisionEngineV8RTIS, decision: Decision | None) -> dict[str, Any]:
    if decision is None:
        return {
            "status": "AMBER",
            "label": "Waiting for first market data fetch",
            "details": ["Fetch latest forecast to warm indicators and ML models."],
        }

    s = decision.snapshot
    problems: list[str] = []
    warming: list[str] = []

    if s.price <= 0:
        problems.append("Invalid UNG price from market data.")
    if len(engine.closes) == 0:
        problems.append("No market bars loaded.")
    if s.mqi < 40:
        problems.append("Market Quality Index is below the red threshold.")
    if s.atr < 0 or s.vwap <= 0:
        problems.append("Core indicators are invalid.")

    if len(engine.closes) < engine.config.warmup_bars:
        warming.append(f"Indicators warming {len(engine.closes)}/{engine.config.warmup_bars} bars.")
    if s.model_status != READY:
        warming.append("HMM regime model is warming or unavailable.")
    if s.markov_status != READY:
        warming.append("Markov transition model is warming.")
    if s.garch_status != READY:
        warming.append("GARCH volatility model is warming or using fallback.")

    if problems:
        return {"status": "RED", "label": "Engine needs attention", "details": problems + warming}
    if warming:
        return {"status": "AMBER", "label": "Engine warming up", "details": warming}
    return {
        "status": "GREEN",
        "label": "All systems running",
        "details": ["Indicators, HMM, Markov, and GARCH are running."],
    }
