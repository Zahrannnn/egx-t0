# EGX T0 Agent

**Intraday T+0 trade agent for the Egyptian Stock Exchange.**
Deterministic quant engine (setups, entry zones, risk, scoring) + skeptical
LLM judge layer (veto-only) + plain-language explanations. Live market data
via the TradingView MCP server (`tradingview-mcp-server`).

**Not financial advice.** Educational tool. T0 means flat before close.

## Install

```bash
pip install tradingview-mcp-server
# then run from this repo root:
python -m egx_t0.scan --min-score 65
```

## What it does each run

1. `egx_market_overview` — live EGX scan (top gainers/losers, ~234 names)
2. Liquidity + price filters (volume ≥ 50k shares, sane price band)
3. Per candidate: `generate_egx_trade_plan` for full indicators
4. Setup state machine: PRE-SETUP → CONFIRMED → ENTERABLE → EXTENDED → INVALID
5. **Entry ZONE** (never a single price): ideal / acceptable / NO-CHASE bands
   derived from VWAP + ATR
6. Risk engine: technical stop (not arbitrary %), TP1/TP2, R:R ≥ 2 required,
   position size from 1% account risk, EGX board-lot rounding
7. Transparent 0-100 score (structure 20% / volume 20% / VWAP 15% / trend 15% /
   level 10% / momentum 10% / market 10%) with state + RR multipliers
8. Grades: 85+ A+ · 75+ A · 65+ B · below = **NO TRADE**
9. Optional `--judge`: GLM skeptical veto (finds thin volume, news spikes,
   index divergence). Fail-open, veto-only, may never raise a score.
10. Output: top 3 or fewer. **If nothing qualifies: NO TRADE.**

## Commands

```bash
python -m egx_t0.scan                     # default (min score 65, top 3)
python -m egx_t0.scan --min-score 45      # looser, for research
python -m egx_t0.scan --judge             # GLM veto layer ON
python -m egx_t0.scan --json              # machine output (for ZCode/MCP)
```

## Architecture (why it's not a prompt)

- `engine.py` — deterministic math. No LLM. Identical inputs → identical plan.
- `judge.py` — LLM reads the plan and may VETO with a reason. It may never
  create trades, change prices, or raise scores. GLM today, Jev when keys ship.
- `scan.py` — orchestration + rendering.

This separation means backtesting replays the exact same setup engine that
runs live, and the LLM layer can never hallucinate a price (it only reads
engine output).

## Short-selling note

EGX short selling is heavily restricted. Breakdown setups are emitted as
**WATCH — verify SSR availability** unless you've confirmed your broker
supports intraday shorts on that ticker. Longs are unaffected.

## Backtesting

Backtester replays historical candles through the same setup engine
(train/test split, out-of-sample). Tracks win rate, avg win/loss, profit
factor, max drawdown, expectancy, avg R, per-setup/per-ticker/per-hour
breakdown. (In progress — the engine is already deterministic, which is the
hard part.)

## Honesty

- TradingView public endpoints can be delayed. The agent flags this every run.
- LLM judgments fail open and never fabricate numbers, but they are opinions.
- Nothing here is financial advice. You will lose money on some trades; the
  point of the gates is to make those losses small, rare, and planned.
