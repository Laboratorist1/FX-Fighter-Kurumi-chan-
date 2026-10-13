# FX Fighter Kurumi-chan — Plan

Human-in-the-loop FX bot. The VPS does all the work; Claude only carries proposals
to the human and decisions back.

## Decisions so far

| Topic | Decision |
|---|---|
| Broker | OANDA v20 REST (practice → live) |
| Human approves | Every trade **entry**. Exits (SL/TP) are automatic, held at OANDA |
| Approval UI | Private Claude Artifact page (Approve / Reject + size scale) |
| Proposal expiry | At the close of the next candle of its timeframe |
| Market | Major pairs, H1–H4 |
| Risk | Global **risk dial** (presets) + **per-proposal size scale** at approval |
| Hard caps (always on) | Max risk/trade %, daily loss limit, max open positions, mandatory SL |
| Strategies | Pluggable, several. Backtest gate → demo forward test → small live |
| Runtime | Always-on VPS runs everything. Claude routine is a thin bridge |

## Architecture

```
            ┌───────────────────────── VPS (always on) ─────────────────────────┐
 OANDA ◄──► │ data feed → strategies → risk engine → proposals ── executor     │
            │                                   ▲          │          ▲        │
            │                         SQLite (state, trades, stats)    │        │
            │              small HTTPS API: GET /proposals, POST /decisions,    │
            │                           GET/PUT /dial, GET /status             │
            └───────────────┬──────────────────────────────────────┬───────────┘
        new proposal → POST │ routine /fire                        │ decisions
                            ▼                                      │
                ┌──── Claude routine (bridge) ────┐                │
                │ pulls VPS API, writes page db,  │────────────────┘
                │ pushes decisions back to VPS    │
                └───────────────┬─────────────────┘
                                │ ArtifactData read/write
                                ▼
                ┌──── Artifact page (you) ─────────┐
                │ proposals, Approve/Reject,        │
                │ size scale, risk dial, status     │
                │ Approve → fire_trigger (connector)│
                └───────────────────────────────────┘
```

Event flow:
1. Candle closes → VPS strategies run → risk engine sizes the trade → proposal stored
   with an expiry, and the VPS calls the routine's API `/fire` endpoint.
2. The routine fetches `/proposals` from the VPS and writes them into the page's db.
3. You click Approve (with a size scale) or Reject. The page writes the decision to the db and
   calls `fire_trigger` through the "Claude Code Remote" connector.
4. The routine reads the decisions and POSTs them to the VPS `/decisions` endpoint.
5. The VPS **re-validates** each approval (not expired, price hasn't drifted past its
   threshold, caps still OK, dial re-applied) and places the order with SL/TP attached.
6. The hourly schedule on the same routine is a safety-net sync if a fire was missed.

### Trust boundaries
- The VPS is the only authority. It enforces every cap no matter what a decision says,
  and treats page and db data as untrusted input.
- VPS API: HTTPS + bearer token, stored as a routine **network secret**. The routine
  environment needs a custom allowlist entry for the VPS host.
- OANDA token lives only on the VPS.
- Page db rules: only the owner can write decisions and the dial.
- Fire `text` is ignored. The routine always pulls the real data from the VPS API.

## Components (VPS, Python)

1. `oanda.py` — candles, pricing, account, orders (practice/live by config).
2. `strategies/` — each strategy is a function `(candles) -> signal | None`
   (side, entry, SL, TP, reason). A list in config picks which ones run, on which pairs and timeframes.
3. `risk.py` — position size from dial % and SL distance, daily loss stop,
   max-positions check, refuses anything without an SL. Self-test included.
4. `backtest.py` — replays OANDA history through the same strategy and risk code,
   including spread. Outputs per-strategy stats.
5. `bot.py` — main loop on candle close, the proposal store, the executor, and the HTTP API.
6. SQLite — proposals, decisions, trades, per-strategy P&L.
7. systemd service plus a daily heartbeat in `/status`.

## Bridge (Claude side)
- One routine: prompt = "sync proposals/decisions between VPS API and artifact db".
  Triggers: API (fired by the VPS), connector fire (from the page), and hourly.
- One Artifact page with `db` + `user` + `mcp` (Claude Code Remote: `fire_trigger`) capabilities.

## Phases
0. **Prove the bridge** (riskiest, unverified): page button → `fire_trigger` → routine
   run → round-trip to a stub VPS endpoint → result back in the page db. Measure the latency
   and usage cost of one run.
1. OANDA client + risk engine + self-tests (practice account).
2. Backtester + first baseline strategies. Set the pass criteria.
3. Live loop on demo: proposals → approvals → orders. Paper-trade for N weeks.
4. Go-live review against the criteria → small live allocation.

## Unverified / risks
- Calling `fire_trigger` from an Artifact page via the Claude Code Remote connector is documented
  but untested. Phase 0 settles it. Fallback: hourly sync only, which is too slow for H1 expiry.
- Routines are a **research preview**. Limits and behavior may change. Runs can start late,
  and a green status ≠ task succeeded.
- Every routine run uses Claude subscription usage. Cost per run is unmeasured (Phase 0).
- If the bridge is down, no new trades are placed. Open trades keep their broker-side SL/TP (fail-safe).

## Open questions
- VPS provider/region, and a domain + TLS for the API?
- Language: Python assumed. OK?
- Risk dial presets: exact % values for each level, and the hard upper bound?
- Price-drift threshold for rejecting a stale approval?
- Backtest pass criteria (e.g. min trades, profit factor, max drawdown)?
- Demo → live criteria and the starting live allocation?
- Trading hours and news filter (skip around high-impact events)?
- First strategies to try?
