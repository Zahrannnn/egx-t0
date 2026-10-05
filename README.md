<div align="center">

# EGX T0 Agent

**Intraday T+0 trade agent for the Egyptian Stock Exchange.**

Deterministic quant engine. Skeptical LLM veto layer. Zero garbage output.

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)]()
[![License](https://img.shields.io/badge/license-MIT-green.svg)]()
[![Discipline](https://img.shields.io/badge/philosophy-precision%20over%20recall-cfa355.svg)]()

*Not financial advice. Educational tool. T0 means flat before the close — always.*

</div>

---

## Why this exists

Most "AI trading agents" are chatbots with a price feed. They hallucinate
entries, invent indicators, and always have an opinion. This tool is built
on the opposite premise:

> **When it speaks, every number survived eight independent gates plus a
> skeptical LLM reading. On most days it says NO TRADE — and that is the
> product working.**

Three architectural rules make that real:

1. **The math never lies.** All indicators, entry zones, stops, and scores
   are computed by a deterministic engine with zero LLM involvement.
   Identical inputs produce identical plans — which is what makes
   backtesting meaningful.
2. **The LLM may only say no.** The judge layer reads computed plans and can
   VETO with a reason. It cannot create trades, adjust prices, or raise
   scores. Fail-closed: no judgment means no trade.
3. **Entry is a zone, never a price.** Ideal / acceptable / NO-CHASE bands
   derived from VWAP and ATR. Chasing is refused by design.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ DATA          tradingview-mcp-server (self-hosted, free) │
│               ~234 EGX names: price, OHLC, volume, RSI,  │
│               ratings — via public TradingView endpoints  │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ FILTER        liquidity ≥ 100k shares · sane price band   │
│               relative volume ≥ 1.4                       │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ SETUP ENGINE  state machine (deterministic):              │
│               PRE-SETUP → CONFIRMED → ENTERABLE           │
│                        → EXTENDED (NO CHASE) → INVALID    │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ ENTRY ZONE    VWAP + 0.15–0.5 ATR bands                   │
│               ideal · acceptable · chase threshold        │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ RISK          technical stop (invalidation, not %)        │
│               TP1 / TP2 · RR ≥ 2.5 · 1% equity risk      │
│               EGX board-lot sizing (100s)                 │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ SCORING       0–100, published weights:                   │
│               structure 20 · volume 20 · VWAP 15          │
│               trend 15 · level 10 · momentum 10           │
│               market 10 · state & RR multipliers          │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ PRECISION     hard gates: score ≥ 80 (A/A+ only)          │
│ GATES         CONFIRMED/ENTERABLE only · ≥4/7 confluence  │
│               RSI band · market-bias confirmation         │
└──────────────┬───────────────────────────────────────────┘
               ▼
┌──────────────────────────────────────────────────────────┐
│ JUDGE         LLM skeptical review (GLM today, Jev-ready) │
│               veto-only · fail-closed · logs reasons      │
└──────────────┬───────────────────────────────────────────┘
               ▼
        TOP 3 RANKED PLANS  —  or  **NO TRADE**
```

## The precision gates

The personal-tool profile. Any single failure drops the setup with a logged reason:

| Gate | Requirement |
|---|---|
| Grade | **A / A+ only** (score ≥ 80) — B-watch suppressed |
| Risk/Reward | **≥ 1 : 2.5** |
| Setup state | CONFIRMED or ENTERABLE — nothing earlier, nothing extended |
| Confluence | **≥ 4 of 7** independent factors at ≥ 0.70 each |
| Liquidity | volume ≥ 100k shares · relative volume ≥ 1.4 |
| Momentum band (longs) | RSI 52–74 — directional but not extended |
| Market bias | index/sector must confirm the direction |
| Judge | PASS required — unavailable judge blocks the trade |

**Expected output frequency: 0–2 A-grade setups per week.** That is the
design, not a limitation. The tool refuses to manufacture opportunities.

## Quick start

```bash
pip install tradingview-mcp-server
git clone https://github.com/Zahrannnn/egx-t0.git
cd egx-t0

# strict precision mode (the default — expect NO TRADE most days)
python -m egx_t0.scan

# with the LLM veto judge (needs GLM_API_KEY)
python -m egx_t0.scan --judge

# research mode: loose gates, see everything the engine considered
python -m egx_t0.scan --loose --min-score 40

# machine output for ZCode / MCP integration
python -m egx_t0.scan --json
```

## Example output

```
==============================================================
EGX T0 AGENT — TOP OPPORTUNITIES
==============================================================
NO TRADE — no setups passed quality thresholds today.
  (6 candidates scanned)
   - PRDC  SHORT 52.7: judge veto: relative volume 0.0 makes liquidity
     unverifiable; SSR availability unconfirmed for short
   - EGS370O1C013 LONG 46.4: judge veto: RSI 70.8 overbought, breakout
     unconfirmed
==============================================================
```

Every rejection carries its reason. A NO TRADE day is a successful run —
the gates did their job.

## Repo layout

| File | Role |
|---|---|
| `egx_t0/engine.py` | Deterministic core: setup state machine, entry zones, risk, scoring. No LLM imports. |
| `egx_t0/precision.py` | Hard gates + fail-closed judge application. The "zero garbage" profile. |
| `egx_t0/judge.py` | LLM veto layer (GLM via Anthropic-style endpoint; Jev-ready swap). Tolerant JSON parsing. |
| `egx_t0/scan.py` | Orchestration + CLI + rendering with reject transparency. |

## Design decisions worth knowing

- **Veto-only LLM.** The judge finds failure modes (thin volume, news spikes,
  index divergence, overbought entries) — it never approves a trade the
  numbers already failed, and never rescues one with optimism.
- **Fail-closed over fail-open.** If the judge is unreachable, the trade
  doesn't happen. Silence beats an unvetted signal.
- **Determinism first.** Because the engine is pure, the planned backtester
  replays the exact same logic on historical candles — no rewrite, no drift
  between live and tested behavior.
- **Shorts are WATCH-only.** EGX short selling is heavily restricted; every
  short setup is labeled "verify SSR availability" until your broker
  confirms otherwise.
- **Costs are not modeled yet.** Until commissions/slippage enter the
  backtester, treat live results as an upper bound.

## Roadmap

- [x] Live EGX scan (data → filter → setups → zones → risk → score)
- [x] Precision hard gates + fail-closed judge
- [x] Reject-reason transparency
- [ ] Backtester: replay candles through the same engine, train/test split,
      cost model, per-setup/per-hour expectancy
- [ ] Own indicator computation from raw candles (golden-fixture tested)
- [ ] Data-quality gate (stale/degraded feed ⇒ automatic NO TRADE)
- [ ] Trade management loop (partials, breakeven, trailing, T0 flat close)
- [ ] ZCode skill + MCP server wrapper

## Honest limitations

- TradingView public endpoints can be **delayed**. The agent states this on
  every run; verify data before acting.
- The judge is an LLM: its vetoes are opinions, strongly correlated with the
  computed numbers but not ground truth. The decision log exists precisely
  so its calibration can be measured over time.
- No system wins 99% of trades. This one is built so that **99% of its output
  is either excellent or honestly silent** — expectancy comes from R:R on
  the few setups that clear every gate.

## License

MIT — with the standing disclaimer that this is analysis tooling, not
financial advice. EGX intraday is thin and violent; the gates exist because
losses are the default outcome without them. You are responsible for your
own execution.
