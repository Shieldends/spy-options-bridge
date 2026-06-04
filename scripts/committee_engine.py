"""
Workforce Committee — Python brain (Phase 2+ hooks).
MODE: design stubs only — does NOT alter live /entry until committee.yaml mode=strict.

Improvements baked in:
  - Lookback window (async insider / dark pool in last X hours)
  - Query-on-trigger (premium APIs only after TV statistical pre-alert)
  - IV regime routing (credit spread vs long / shares)
  - Liquidity + account risk math
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "committee.yaml"


@dataclass
class LayerVotes:
    behavioral: bool = False
    structural: bool = False
    statistical: bool = False
    behavioral_notes: str = ""
    structural_notes: str = ""
    statistical_notes: str = ""


@dataclass
class CommitteeDecision:
    committee_pass: bool
    size_multiplier: float = 1.0
    iv_regime: int = 0  # 0 low, 1 mid, 2 high
    preferred_structure: str = "put_credit_spread"
    max_risk_dollars: float = 45.0
    flags: list[str] = field(default_factory=list)
    layers: LayerVotes = field(default_factory=LayerVotes)


def load_committee_config() -> dict[str, Any]:
    if not CONFIG.exists():
        return {"mode": "off"}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


class LookbackWindow:
    """Was there insider / dark-pool activity in the last X hours?"""

    def __init__(self, hours: int = 24) -> None:
        self.hours = hours
        self.cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    def check_quiver_insider(self, ticker: str) -> tuple[bool, str]:
        # Phase 3: httpx call to Quiver API — cache results with timestamp
        return False, "quiver_not_configured"

    def check_unusual_whales_dp(self, ticker: str) -> tuple[bool, str]:
        # Phase 3: query last N hours DP prints
        return False, "unusual_whales_not_configured"

    def behavioral_vote(self, ticker: str) -> tuple[bool, str]:
        insider_ok, ni = self.check_quiver_insider(ticker)
        dp_ok, nd = self.check_unusual_whales_dp(ticker)
        if insider_ok or dp_ok:
            return True, f"{ni}; {nd}"
        return False, "no lookback hits (APIs idle until Phase 3)"


class QueryOnTrigger:
    """
    Premium API calls ONLY when TradingView fires statistical pre-alert.
    Cuts API spend ~80% vs 24/7 polling.
    """

    def __init__(self) -> None:
        self._armed = False

    def on_statistical_setup(self, ticker: str, payload: dict[str, Any]) -> CommitteeDecision:
        """Called from /entry when queryOnTrigger=true in webhook JSON."""
        cfg = load_committee_config()
        lookback_h = int(payload.get("lookbackHours") or cfg.get("lookback_hours", 24))
        lb = LookbackWindow(hours=lookback_h)

        layers = LayerVotes()
        b_ok, b_note = lb.behavioral_vote(ticker)
        layers.behavioral = b_ok or bool(payload.get("layerBehavioral"))
        layers.behavioral_notes = b_note

        # Structural: trust Pine vol breakout until UW wired
        layers.structural = bool(payload.get("layerStructural"))
        layers.structural_notes = "pine_volume_gate"

        layers.statistical = bool(payload.get("layerStatistical"))
        layers.statistical_notes = "pine_atr_macd_trigger"

        iv_regime = int(payload.get("ivRegime") or 0)
        preferred = route_structure(iv_regime, cfg)

        equity = float(payload.get("accountEquity") or 3000)
        risk_pct = float(payload.get("maxRiskPct") or 1.5)
        max_risk = equity * (risk_pct / 100.0)

        macro_high = bool(payload.get("macroVolHigh"))
        size_mult = float(payload.get("sizeMultiplier") or 1.0)
        if macro_high and size_mult > 0.5:
            size_mult = min(size_mult, 0.5)

        committee_pass = (
            bool(payload.get("committeePass"))
            and layers.behavioral
            and layers.structural
            and layers.statistical
        )

        flags: list[str] = []
        if not liquidity_ok(ticker, payload, cfg):
            committee_pass = False
            flags.append("liquidity_blocked")

        return CommitteeDecision(
            committee_pass=committee_pass,
            size_multiplier=size_mult,
            iv_regime=iv_regime,
            preferred_structure=preferred,
            max_risk_dollars=max_risk,
            flags=flags,
            layers=layers,
        )


def route_structure(iv_regime: int, cfg: dict) -> str:
    """
    IV crush guard:
      low IV  → long options / shares (Phase 4)
      high IV → credit spreads (current production)
    """
    routing = cfg.get("iv_routing", {})
    if iv_regime >= 2:
        return routing.get("high", "put_credit_spread")
    if iv_regime == 0:
        return routing.get("low", "long_call_itm")  # Phase 4 — delta 0.6-0.7
    return routing.get("mid", "put_credit_spread")


def liquidity_ok(ticker: str, payload: dict, cfg: dict) -> bool:
    guards = cfg.get("liquidity", {})
    min_adv = float(guards.get("min_adv_millions", 2.0)) * 1_000_000
    price_min = float(guards.get("price_min", 10))
    price_max = float(guards.get("price_max", 150))
    price = float(payload.get("signalPrice") or 0)
    if price < price_min or price > price_max:
        return False
    if ticker.upper() == "SPY":
        return True
    adv = float(payload.get("adv10d") or min_adv)
    return adv >= min_adv


def evaluate_committee(ticker: str, payload: dict[str, Any]) -> CommitteeDecision:
    """Entry point for bridge (Phase 2). Returns allow/deny + sizing."""
    cfg = load_committee_config()
    if cfg.get("mode", "off") == "off":
        return CommitteeDecision(committee_pass=True, size_multiplier=float(payload.get("sizeMultiplier") or 1.0))

    if payload.get("queryOnTrigger"):
        return QueryOnTrigger().on_statistical_setup(ticker, payload)

    return CommitteeDecision(committee_pass=bool(payload.get("committeePass", True)))