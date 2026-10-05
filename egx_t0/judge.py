#!/usr/bin/env python3
"""Judge layer — Jev-semantics veto on top of the deterministic engine.

Backend: GLM (Coding Plan, anthropic-style /messages) today. Jev swap-in
when keys land. The judge may VETO a passing trade with a reason; it may
not create trades, adjust prices, or override the score upward.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional

SYSTEM = (
    "You are a skeptical intraday trading reviewer for the Egyptian Exchange (EGX). "
    "You receive a trade plan with all computed numbers. Your job is to find reasons "
    "it might FAIL: thin volume, wide spread, news-driven spike, index divergence, "
    "illiquid print, extended move, unreliable data. "
    "You never invent numbers. You never approve something the numbers already failed. "
    "Respond with ONLY JSON: "
    '{"verdict": "PASS" | "VETO", "reason": "<max 30 words>", "confidence": 0.0-1.0}'
)


def _load_key() -> str:
    if os.environ.get("GLM_API_KEY"):
        return os.environ["GLM_API_KEY"]
    for p in ("/data/.env", ".env"):
        if os.path.exists(p):
            for line in open(p, encoding="utf-8"):
                if line.startswith("GLM_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError("GLM_API_KEY not set")


def judge_trade(plan_json: str, model: str = "glm-5.3-flash",
                base_url: str = "https://api.z.ai/api/anthropic/v1") -> Optional[dict]:
    """Returns {'verdict':..., 'reason':..., 'confidence':...} or None on error."""
    key = _load_key()
    body = json.dumps({
        "model": model,
        "max_tokens": 2000,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"Review this EGX T0 trade plan:\n{plan_json}",
        }],
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/messages",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "egx-t0/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        start, end = text.find("{"), text.rfind("}")
        if start == -1:
            return None
        raw = text[start:end + 1] if end != -1 else text[start:]
        # tolerate truncation: close dangling quotes/braces
        if end == -1:
            raw = raw.rstrip().rstrip(',"').rstrip() + '"}'
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        verdict = str(parsed.get("verdict", "")).upper()
        if verdict in ("PASS",):
            parsed["verdict"] = "PASS"
        elif verdict in ("VETO", "FAIL", "REJECT", "NO"):
            parsed["verdict"] = "VETO"
        else:
            return None
        return parsed
    except Exception:
        return None  # fail-open: judge unavailability never fabricates a veto


def explain(plan_json: str, verdict: dict | None, model: str = "glm-5.3-flash",
            base_url: str = "https://api.z.ai/api/anthropic/v1") -> Optional[str]:
    """Plain-language explanation for the human. Numbers come from the plan."""
    key = _load_key()
    v = f"Judge: {verdict['verdict']} ({verdict['reason']})." if verdict else ""
    body = json.dumps({
        "model": model,
        "max_tokens": 700,
        "system": (
            "You are an experienced EGX intraday trader explaining a trade plan to another trader. "
            "Use ONLY the numbers in the plan. Never invent prices or indicators. "
            "Structure: Why this trade (3-5 bullets), Invalidation (one line), Execution note (one line). "
            "Plain, direct, no hype. Max 120 words."
        ),
        "messages": [{"role": "user", "content": f"{v}\nPlan:\n{plan_json}"}],
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/messages",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "egx-t0/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    except Exception:
        return None
