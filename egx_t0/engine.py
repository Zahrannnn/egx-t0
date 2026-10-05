#!/usr/bin/env python3
"""
EGX T0 Intraday Agent - deterministic quant engine.

Design contract (see README):
- This module NEVER calls an LLM. All numbers here are deterministic.
- The judge/reasoning layer reads this engine's output and may VETO,
  but may not invent prices, indicators, or setups.

Pipeline:
  scan -> filter -> setups -> entry zones -> risk -> score -> rank -> output
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from typing import Optional

from tradingview_mcp.core.services.egx_service import (
    get_egx_market_overview,
    screen_egx_stocks,
    generate_egx_trade_plan,
)

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RiskConfig:
    account_equity_egp: float = 100_000.0
    risk_per_trade_pct: float = 1.0          # % of equity risked per trade
    max_spread_pct: float = 1.5              # reject illiquid names
    min_rr: float = 2.0                      # minimum reward:risk
    min_volume_shares: int = 50_000          # avoid untradeable prints
    min_price_egp: float = 1.0               # avoid penny/noise
    max_price_egp: float = 500.0

@dataclass
class ScoreWeights:
    structure: float = 0.20
    volume: float = 0.20
    vwap: float = 0.15
    trend: float = 0.15
    level: float = 0.10
    momentum: float = 0.10
    market: float = 0.10

DEFAULT_RISK = RiskConfig()
DEFAULT_WEIGHTS = ScoreWeights()

GRADE_BANDS = ((85, "A+"), (75, "A"), (65, "B"))


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EntryZone:
    ideal_low: float
    ideal_high: float
    acceptable_low: float
    acceptable_high: float
    chase_above: float
    basis: str


@dataclass
class TradePlan:
    symbol: str
    name: str
    direction: str            # LONG / SHORT
    setup: str
    state: str                # PRE-SETUP / CONFIRMED / ENTERABLE / EXTENDED / INVALID
    entry: EntryZone
    stop: float
    tp1: float
    tp2: float
    risk_pct: float
    potential_pct: float
    rr: float
    position_size: int        # shares
    confidence: str
    score: float
    grade: str
    reasons: list[str] = field(default_factory=list)
    invalidation: str = ""
    notes: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic math helpers
# ─────────────────────────────────────────────────────────────────────────────

def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def _pct_move(price: float, ref: float) -> float:
    return (price - ref) / ref * 100.0 if ref else 0.0


def _atr_pct(ind: dict) -> Optional[float]:
    """ATR as % of price — proxy for volatility band width."""
    atr = _f(ind.get("ATR") or ind.get("atr"))
    close = _f(ind.get("close") or ind.get("price"))
    if atr and close:
        return atr / close * 100.0
    return None


# ─────────────────────────────────────────────────────────────────────────────
# T0 Setup state machine
# ─────────────────────────────────────────────────────────────────────────────

SETUP_STATES = ("PRE-SETUP", "CONFIRMED", "ENTERABLE", "EXTENDED", "INVALID")


def classify_setup(setup: str, ind: dict, gainer: bool) -> tuple[str, str]:
    """
    Returns (direction, state) for a named setup given current indicators.
    Deterministic thresholds, no judgment here.
    """
    price = _f(ind.get("close") or ind.get("price")) or 0.0
    rsi = _f(ind.get("RSI")) or 50.0
    chg = _f(ind.get("changePercent")) or 0.0
    vwap_reclaim = bool(ind.get("above_vwap"))
    ema_stack = bool(ind.get("ema_bullish"))

    if setup == "Breakout + volume":
        if rsi > 78 and chg > 8:
            return ("LONG", "EXTENDED")
        if chg > 0 and vwap_reclaim:
            return ("LONG", "ENTERABLE" if rsi < 74 else "EXTENDED")
        return ("LONG", "PRE-SETUP")

    if setup == "VWAP reclaim":
        if vwap_reclaim and chg > 0:
            return ("LONG", "CONFIRMED" if ema_stack else "PRE-SETUP")
        return ("LONG", "INVALID")

    if setup == "Opening range breakout":
        if vwap_reclaim and rsi < 76:
            return ("LONG", "ENTERABLE")
        return ("LONG", "PRE-SETUP")

    if setup == "Support bounce":
        if chg > 0 and rsi < 70:
            return ("LONG", "CONFIRMED")
        return ("LONG", "PRE-SETUP")

    if setup == "Momentum continuation":
        if rsi > 80:
            return ("LONG", "EXTENDED")
        if chg > 0 and vwap_reclaim and ema_stack:
            return ("LONG", "ENTERABLE")
        return ("LONG", "PRE-SETUP")

    if setup == "Resistance rejection":
        if rsi > 65 and chg < 0:
            return ("SHORT", "CONFIRMED")
        return ("SHORT", "PRE-SETUP")

    if setup == "Breakdown (SSR)":
        # short selling on EGX is restricted; only flagged when allowed
        if chg < -2 and not vwap_reclaim:
            return ("SHORT", "PRE-SETUP")
        return ("SHORT", "INVALID")

    return ("LONG", "PRE-SETUP")


# ─────────────────────────────────────────────────────────────────────────────
# Entry zone engine
# ─────────────────────────────────────────────────────────────────────────────

def build_entry_zone(price: float, ind: dict, direction: str) -> EntryZone:
    """
    Ideal zone: value area around the working level (VWAP or breakout level).
    Acceptable: one ATR beyond. Chase: beyond that — NO CHASE.
    """
    atr_pct = _atr_pct(ind) or 2.0
    atr = price * atr_pct / 100.0
    vwap = _f(ind.get("VWAP"))
    anchor = vwap if vwap else price

    basis_vwap = f"{vwap:.2f}" if vwap else "price"
    if direction == "LONG":
        ideal_low = round(min(price, anchor) - 0.25 * atr, 2)
        ideal_high = round(max(price, anchor) + 0.15 * atr, 2)
        acc_low = round(ideal_low - 0.5 * atr, 2)
        acc_high = round(ideal_high + 0.35 * atr, 2)
        chase = round(acc_high + 0.6 * atr, 2)
        basis = f"VWAP {basis_vwap} + 0.15-0.5 ATR (ATR% {atr_pct:.1f})"
    else:
        ideal_high = round(max(price, anchor) + 0.25 * atr, 2)
        ideal_low = round(min(price, anchor) - 0.15 * atr, 2)
        acc_high = round(ideal_high + 0.5 * atr, 2)
        acc_low = round(ideal_low - 0.35 * atr, 2)
        chase = round(acc_low - 0.6 * atr, 2)
        basis = f"VWAP {basis_vwap} + 0.15-0.5 ATR (ATR% {atr_pct:.1f})"

    return EntryZone(ideal_low, ideal_high, acc_low, acc_high, chase, basis)


# ─────────────────────────────────────────────────────────────────────────────
# Risk engine
# ─────────────────────────────────────────────────────────────────────────────

def build_risk(symbol_price: float, ind: dict, direction: str,
               zone: EntryZone, risk_cfg: RiskConfig) -> tuple[float, float, float, float, float, int, str]:
    """
    Returns (stop, tp1, tp2, risk_pct, potential_pct, rr, size_shares).
    Stop = technical invalidation (below VWAP/level for long, above for short).
    """
    atr_pct = _atr_pct(ind) or 2.0
    stop_dist = max(symbol_price * atr_pct / 100.0, symbol_price * 0.012)

    if direction == "LONG":
        stop = round(zone.acceptable_low - 0.4 * stop_dist, 2)
        risk_per_share = max(symbol_price - stop, 0.01)
        tp1 = round(symbol_price + 1.5 * risk_per_share, 2)
        tp2 = round(symbol_price + 2.5 * risk_per_share, 2)
        potential = (tp2 - symbol_price) / symbol_price * 100.0
    else:
        stop = round(zone.acceptable_high + 0.4 * stop_dist, 2)
        risk_per_share = max(stop - symbol_price, 0.01)
        tp1 = round(symbol_price - 1.5 * risk_per_share, 2)
        tp2 = round(symbol_price - 2.5 * risk_per_share, 2)
        potential = (symbol_price - tp2) / symbol_price * 100.0

    risk_pct = risk_per_share / symbol_price * 100.0
    rr = round(potential / risk_pct, 2) if risk_pct else 0.0

    risk_egp = risk_cfg.account_equity_egp * risk_cfg.risk_per_trade_pct / 100.0
    size = int(risk_egp / risk_per_share)
    size -= size % 100  # EGX board lot 100 (practical)
    size = max(size, 0)

    return stop, tp1, tp2, round(risk_pct, 2), round(potential, 2), rr, size


# ─────────────────────────────────────────────────────────────────────────────
# Scoring engine (0-100, transparent weights)
# ─────────────────────────────────────────────────────────────────────────────

def score_trade(ind: dict, setup_state: str, direction: str, rr: float,
                weights: ScoreWeights, market_bias: float = 0.0) -> tuple[float, dict]:
    """
    Deterministic 0-100 score. market_bias: -1..1 from index/sector.
    Returns (score, breakdown).
    """
    price = _f(ind.get("close") or ind.get("price")) or 0.0
    rsi = _f(ind.get("RSI")) or 50.0
    chg = _f(ind.get("changePercent")) or 0.0
    vol_ratio = _f(ind.get("relative_volume")) or 1.0

    b = {}
    # Structure: change% in productive band for T0
    b["structure"] = min(abs(chg) / 6.0, 1.0)
    # Volume: relative volume above 1 is what matters
    b["volume"] = min(vol_ratio / 2.5, 1.0)
    # VWAP side
    above = bool(ind.get("above_vwap"))
    b["vwap"] = 1.0 if (above if direction == "LONG" else not above) else 0.25
    # EMA alignment
    stack = bool(ind.get("ema_bullish"))
    b["trend"] = 1.0 if (stack if direction == "LONG" else not stack) else 0.3
    # Level quality: state-based
    b["level"] = {"ENTERABLE": 1.0, "CONFIRMED": 0.8, "PRE-SETUP": 0.5,
                  "EXTENDED": 0.3, "INVALID": 0.0}.get(setup_state, 0.4)
    # Momentum: RSI distance from 50 in trade direction, penalize extremes
    if direction == "LONG":
        m = min((rsi - 50) / 25, 1.0) if rsi > 50 else 0.2
        if rsi > 80: m *= 0.5
    else:
        m = min((50 - rsi) / 25, 1.0) if rsi < 50 else 0.2
        if rsi < 20: m *= 0.5
    b["momentum"] = m
    # Market/sector confirmation
    b["market"] = min(max(market_bias, -1), 1) * 0.5 + 0.5

    score = (
        b["structure"] * weights.structure +
        b["volume"] * weights.volume +
        b["vwap"] * weights.vwap +
        b["trend"] * weights.trend +
        b["level"] * weights.level +
        b["momentum"] * weights.momentum +
        b["market"] * weights.market
    ) * 100.0

    # State multiplier: never let an INVALID/PRE-SETUP score like ENTERABLE
    if setup_state in ("INVALID",):
        score *= 0.4
    elif setup_state == "PRE-SETUP":
        score *= 0.75

    # RR multiplier: reward >=2, punish <1.5
    if rr >= 2.0: score *= 1.08
    elif rr < 1.5: score *= 0.75

    return round(min(score, 100.0), 1), {k: round(v, 2) for k, v in b.items()}


def grade_for(score: float) -> str:
    for cut, label in GRADE_BANDS:
        if score >= cut:
            return label
    return "NO TRADE"
