"""Precision profile — for the personal tool: zero garbage output.

Philosophy: 99% "great results" means 99% of output is either a genuinely
excellent setup or an honest NO TRADE. Achieved by precision-over-recall:
hard gates, fail-closed judging, state restriction, confluence minimums.

This is NOT a 99% win rate (that does not exist in T0 trading).
Expectancy comes from R:R >= 2.5 on the few setups that clear these gates.
Expect 0-2 A-grade signals per WEEK. That is the design, not a flaw.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PrecisionProfile:
    """Hard gates — any single failure = the setup is not emitted."""

    min_score: float = 80.0            # A-grade only
    min_rr: float = 2.5
    allowed_states: tuple[str, ...] = ("CONFIRMED", "ENTERABLE")
    min_confluence_factors: int = 4    # of 7 score factors, must be >= this
    factor_pass_threshold: float = 0.70  # a factor "counts" at >= 0.70
    min_volume_shares: int = 100_000   # deeper than v0's 50k
    min_relative_volume: float = 1.4   # needs real participation
    max_rsi_long: float = 74.0         # beyond this = extended, refuse
    min_rsi_long: float = 52.0         # needs directional momentum
    market_bias_min: float = 0.15      # index must not fight the trade
    judge_fail_closed: bool = True     # no judgment => no trade
    suppress_below: str = "A"          # B-grade watch rows are suppressed

    @staticmethod
    def from_flags(strict: bool = True) -> "PrecisionProfile":
        return PrecisionProfile() if strict else PrecisionProfile(
            min_score=65.0, min_rr=2.0,
            allowed_states=("CONFIRMED", "ENTERABLE", "PRE-SETUP"),
            min_confluence_factors=2, min_volume_shares=50_000,
            min_relative_volume=0.0, max_rsi_long=80.0, min_rsi_long=0.0,
            market_bias_min=-1.0, judge_fail_closed=False,
            suppress_below="NO TRADE",
        )


def confluence_count(breakdown: dict, threshold: float = 0.70) -> tuple[int, list[str]]:
    """Count independent factors at/above threshold. Returns (count, names)."""
    passed = [k for k, v in breakdown.items() if v >= threshold]
    return len(passed), passed


def precision_gate(candidate: dict, profile: PrecisionProfile) -> tuple[bool, str]:
    """
    Deterministic hard gates. Returns (passes, reason-if-failed).
    Applied AFTER normal scoring; a failure here is final.
    """
    if candidate["state"] not in profile.allowed_states:
        return False, f"state {candidate['state']} not in {profile.allowed_states}"
    if candidate["score"] < profile.min_score:
        return False, f"score {candidate['score']} < {profile.min_score}"
    if candidate["rr"] < profile.min_rr:
        return False, f"RR {candidate['rr']} < {profile.min_rr}"

    count, names = confluence_count(candidate.get("breakdown", {}), profile.factor_pass_threshold)
    if count < profile.min_confluence_factors:
        return False, f"confluence {count}/{len(candidate.get('breakdown', {}))} < {profile.min_confluence_factors} ({', '.join(names) or 'none'})"

    if candidate.get("_volume_shares", 0) < profile.min_volume_shares:
        return False, "volume below precision minimum"
    if (candidate.get("_relative_volume") or 0) < profile.min_relative_volume:
        return False, "relative volume below precision minimum"

    if candidate["direction"] == "LONG":
        rsi = candidate.get("_rsi") or 50.0
        if rsi > profile.max_rsi_long:
            return False, f"RSI {rsi:.0f} extended (> {profile.max_rsi_long})"
        if rsi < profile.min_rsi_long:
            return False, f"RSI {rsi:.0f} lacks momentum (< {profile.min_rsi_long})"

    if candidate["direction"] == "LONG" and candidate.get("market_bias", 0) < profile.market_bias_min:
        return False, "market/sector bias does not confirm long"
    if candidate["direction"] == "SHORT" and candidate.get("market_bias", 0) > -profile.market_bias_min:
        return False, "market/sector bias does not confirm short"

    return True, ""


def judge_gate(judgement: dict | None, profile: PrecisionProfile) -> tuple[bool, str]:
    """
    Judge application under precision rules:
    fail-closed => judge unavailable/error BLOCKS the trade.
    VETO always blocks. PASS is required, not assumed.
    """
    if judgement is None:
        if profile.judge_fail_closed:
            return False, "judge unavailable and profile is fail-closed"
        return True, ""
    if judgement.get("verdict") == "VETO":
        return False, f"judge veto: {judgement.get('reason', '')}"
    if judgement.get("verdict") == "PASS":
        conf = judgement.get("confidence")
        if isinstance(conf, (int, float)) and conf < 0.6:
            return False, f"judge pass confidence too low ({conf})"
        return True, ""
    return False, "judge verdict malformed"
