#!/usr/bin/env python3
"""EGX T0 scanner: orchestrates overview -> candidates -> plans -> score -> rank.

Usage:
  python -m egx_t0.scan                 # top 3 opportunities or NO TRADE
  python -m egx_t0.scan --min-score 75  # stricter
  python -m egx_t0.scan --judge         # enable GLM veto layer
  python -m egx_t0.scan --json          # machine output
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from .engine import (
    DEFAULT_RISK, DEFAULT_WEIGHTS, RiskConfig, ScoreWeights,
    classify_setup, build_entry_zone, build_risk, score_trade, grade_for,
)


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def scan(min_score: float = 65.0, top: int = 3, use_judge: bool = False,
         risk: RiskConfig = DEFAULT_RISK, weights: ScoreWeights = DEFAULT_WEIGHTS):
    from tradingview_mcp.core.services.egx_service import (
        get_egx_market_overview, screen_egx_stocks, generate_egx_trade_plan,
    )

    overview = get_egx_market_overview(timeframe="1D", limit=10)
    gainers = overview.get("top_gainers", [])[:6]
    losers = overview.get("top_losers", [])[:3]

    # Market bias: median gainer change% as crude index health proxy (deterministic)
    chgs = sorted(_f(g.get("changePercent")) or 0.0 for g in gainers)
    market_bias = 0.0
    if chgs:
        mid = chgs[len(chgs) // 2]
        market_bias = max(-1.0, min(1.0, mid / 5.0))

    candidates = []

    for g in gainers:
        sym = g.get("symbol", "")
        price = _f(g.get("price"))
        vol = _f(g.get("volume")) or 0
        if not price or vol < DEFAULT_RISK.min_volume_shares:
            continue
        if not (DEFAULT_RISK.min_price_egp <= price <= DEFAULT_RISK.max_price_egp):
            continue

        # Longs from strong gainers
        try:
            plan = generate_egx_trade_plan(symbol=sym, timeframe="15")
        except Exception:
            continue
        ind = plan.get("indicators") or plan.get("analysis") or {}
        ind["close"] = price
        ind["changePercent"] = g.get("changePercent")
        ind["relative_volume"] = plan.get("relative_volume") or ind.get("relative_volume") or 1.5

        setup_name = "Breakout + volume" if (g.get("changePercent") or 0) > 5 else (
            "VWAP reclaim" if ind.get("above_vwap") else "Momentum continuation")
        direction, state = classify_setup(setup_name, ind, gainer=True)
        if state == "INVALID":
            continue

        zone = build_entry_zone(price, ind, direction)
        stop, tp1, tp2, risk_pct, pot_pct, rr, size = build_risk(
            price, ind, direction, zone, risk)

        if rr < DEFAULT_RISK.min_rr:
            continue

        score, breakdown = score_trade(ind, state, direction, rr, weights, market_bias)
        if score < min_score:
            continue

        grade = grade_for(score)
        candidates.append({
            "symbol": sym.replace("EGX:", ""),
            "name": g.get("name") or sym,
            "direction": direction,
            "setup": setup_name,
            "state": state,
            "price": price,
            "entry_zone": f"{zone.ideal_low:.2f} - {zone.ideal_high:.2f}",
            "acceptable": f"{zone.acceptable_low:.2f} - {zone.acceptable_high:.2f}",
            "chase_above": zone.chase_above,
            "zone_basis": zone.basis,
            "stop": stop, "tp1": tp1, "tp2": tp2,
            "risk_pct": risk_pct, "potential_pct": pot_pct, "rr": rr,
            "size_shares": size,
            "score": score, "grade": grade,
            "breakdown": breakdown,
            "market_bias": round(market_bias, 2),
            "_plan": plan,
        })

    # Shorts: only where SSR permits — EGX short selling is heavily restricted,
    # so short setups are WATCH-ONLY unless user config says otherwise.
    for l in losers:
        sym = l.get("symbol", "")
        price = _f(l.get("price"))
        vol = _f(l.get("volume")) or 0
        if not price or vol < DEFAULT_RISK.min_volume_shares * 2:
            continue
        try:
            plan = generate_egx_trade_plan(symbol=sym, timeframe="15")
        except Exception:
            continue
        ind = plan.get("indicators") or {}
        ind["close"] = price
        ind["changePercent"] = l.get("changePercent")
        ind["above_vwap"] = bool(ind.get("above_vwap"))
        setup_name = "Breakdown (SSR)"
        direction, state = classify_setup(setup_name, ind, gainer=False)
        zone = build_entry_zone(price, ind, direction)
        stop, tp1, tp2, risk_pct, pot_pct, rr, size = build_risk(
            price, ind, direction, zone, risk)
        score, breakdown = score_trade(ind, state, direction, rr, weights, -market_bias)
        candidates.append({
            "symbol": sym.replace("EGX:", ""),
            "name": l.get("name") or sym,
            "direction": direction,
            "setup": setup_name + " [WATCH — verify SSR availability]",
            "state": state,
            "price": price,
            "entry_zone": f"{zone.ideal_low:.2f} - {zone.ideal_high:.2f}",
            "acceptable": f"{zone.acceptable_low:.2f} - {zone.acceptable_high:.2f}",
            "chase_above": zone.chase_above,
            "zone_basis": zone.basis,
            "stop": stop, "tp1": tp1, "tp2": tp2,
            "risk_pct": risk_pct, "potential_pct": pot_pct, "rr": rr,
            "size_shares": size,
            "score": score, "grade": grade_for(score),
            "breakdown": breakdown,
            "market_bias": round(-market_bias, 2),
            "_plan": plan,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Judge layer (optional): veto pass on top candidates
    if use_judge:
        from .judge import judge_trade
        for c in candidates[:top]:
            v = judge_trade(json.dumps({k: v for k, v in c.items() if k != "_plan"}, default=str))
            c["judgement"] = v or {"verdict": "PASS", "reason": "judge unavailable, fail-open", "confidence": 0}

    approved = [c for c in candidates if c.get("judgement", {}).get("verdict") != "VETO"][:top]
    return approved, candidates


def render(plans: list[dict]) -> str:
    if not plans:
        return "NO TRADE — no setups passed quality thresholds today."

    out = []
    for i, p in enumerate(plans, 1):
        out.append(f"RANK #{i}")
        out.append(f"  Ticker:              {p['symbol']}")
        out.append(f"  Direction:           {p['direction']}")
        out.append(f"  Setup:               {p['setup']}")
        out.append(f"  Entry Zone:          {p['entry_zone']} EGP  (ideal)")
        out.append(f"  Acceptable:          {p['acceptable']} EGP")
        out.append(f"  NO CHASE above:      {p['chase_above']} EGP")
        out.append(f"  Zone basis:          {p['zone_basis']}")
        out.append(f"  Stop Loss:           {p['stop']} EGP")
        out.append(f"  TP1:                 {p['tp1']} EGP")
        out.append(f"  TP2:                 {p['tp2']} EGP")
        out.append(f"  Risk:                {p['risk_pct']}%  (size: {p['size_shares']} shares)")
        out.append(f"  Potential:           {p['potential_pct']}%")
        out.append(f"  Risk/Reward:         1:{p['rr']}")
        out.append(f"  Score:               {p['score']} / 100  ({p['grade']})")
        if "judgement" in p:
            j = p["judgement"]
            out.append(f"  Judge:               {j['verdict']} - {j['reason']}")
        out.append("")

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(prog="egx-t0")
    ap.add_argument("--min-score", type=float, default=65.0)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--judge", action="store_true", help="enable GLM veto layer")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    approved, all_c = scan(min_score=args.min_score, top=args.top, use_judge=args.judge)

    if args.json:
        print(json.dumps(approved, indent=1, default=str))
        return

    print("=" * 62)
    print("EGX T0 AGENT — TOP OPPORTUNITIES")
    print("=" * 62)
    print(render(approved))
    if not approved:
        print(f"  (scanned {len(all_c)} candidates; none cleared {args.min_score})")
    print("=" * 62)
    print("Not financial advice. Data via TradingView public endpoints — verify")
    print("delayed/incomplete data before executing. T0: close before market close.")


if __name__ == "__main__":
    main()
