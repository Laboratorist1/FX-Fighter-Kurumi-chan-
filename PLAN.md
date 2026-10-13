# FX Fighter Kurumi-chan — Plan

Human-in-the-loop FX bot. The VPS does all the work; Claude only carries proposals
to the human and decisions back.

## Decisions so far

| Topic | Decision |
|---|---|
| Broker | **Open.** OANDA v20 likely unavailable to Costa Rica residents (Global Markets division has no v20). Lead candidate: Pepperstone via cTrader Open API, still to be verified |
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
2. Backtester + baseline strategies + session parameter.
   2b. High-impact news study.
3. Live loop on demo: proposals → approvals → orders. Paper-trade for N weeks.
4. Go-live review against the criteria → small live allocation.

## Unverified / risks
- Calling `fire_trigger` from an Artifact page via the Claude Code Remote connector is documented
  but untested. Phase 0 settles it. Fallback: hourly sync only, which is too slow for H1 expiry.
- Routines are a **research preview**. Limits and behavior may change. Runs can start late,
  and a green status ≠ task succeeded.
- Every routine run uses Claude subscription usage. Cost per run is unmeasured (Phase 0).
- If the bridge is down, no new trades are placed. Open trades keep their broker-side SL/TP (fail-safe).

## Settled
- **Host:** Hetzner VPS. API on a subdomain of the owner's domain, with TLS via Caddy
  (automatic Let's Encrypt).
- **Language:** Python.

## Proposed (confirm or change)

### Risk dial
The dial is set from the page. The hard ceilings live in VPS config, and the page cannot change them.

| Level | Risk / trade | Daily loss stop | Max open |
|---|---|---|---|
| 0 Paused | — | — | 0 |
| 1 | 0.25% | 0.75% | 2 |
| 2 | 0.5% | 1.5% | 3 |
| 3 | 1% | 3% | 3 |
| 4 | 1.5% | 4.5% | 4 |
| **Hard ceiling** | 2% | 5% | 5 |

- Per-proposal size scale at approval: 0.25×, 0.5×, 0.75× or 1×. It can only scale **down**.
- Demo starts at level 2.

### Stale approval (price drift)
When an approval arrives, the VPS takes the current price and:
- rejects if `|price − entry| > 0.25 × |entry − SL|` (a quarter of the stop distance);
- rejects if the spread is more than 2× that pair's typical spread;
- otherwise keeps the SL level fixed and **re-sizes** from the actual entry.

### Backtest gate (profit factor)
Profit factor alone is noisy on small samples, so it comes with guards:
- PF ≥ 1.3 net of spread on in-sample data, **and** PF ≥ 1.2 on held-out (out-of-sample) data;
- at least 100 trades;
- max drawdown is reported, but it isn't a gate.

### Demo → live
- Demo PF ≥ 1.2 net of costs over at least 30 trades or 6 weeks, whichever comes later.
- Minimum live allocation: spread scales with position size, so the fixed monthly costs
  (VPS, domain) are what need covering. Live capital ≥ (fixed costs ÷ demo net monthly return)
  × 3 safety margin. This is computed from the real demo results, not guessed up front.

### Trading hours
The session (Asia / London / NY / London–NY overlap) is a **backtested parameter per strategy
and pair**. It's chosen on in-sample data and confirmed out-of-sample, so it isn't overfitted.

### High-impact news — dedicated research phase (2b)
- Needs a historical economic-calendar source. Whether OANDA offers one is **unverified**.
  Otherwise a third-party feed (free scraping has ToS issues; paid APIs cost money).
- Study: per pair, spread, volatility and returns around high-impact events. Each strategy's PF with vs.
  without blackout windows (±15 / 30 / 60 min).
- Until the study is done, demo runs without a news filter but **tags** every trade near
  an event, so the demo data feeds the study.

### First strategies (blank slate → simple, testable baselines)
1. Trend: EMA 20/50 crossover, ATR-based SL, 2R TP (H4).
2. Breakout: Donchian 20-bar channel break, opposite-channel or ATR SL (H1/H4).
3. Mean reversion: RSI(14) extremes inside Bollinger bands, ranging filter (H1).

These are baselines. Most won't pass the gate. That's the gate doing its job.

## Still open
- The news calendar data source, and whether paying for one is OK.
- Confirm or change the proposed numbers above.

## Phase 0 results (2026-10-13)
- Page: https://claude.ai/artifact/SEeDvZWLAjWrS9NoCLPY5C · routine `trig_01DTWFuUYztsBGy6Q79qbBkT` (fire-only, no schedule).
- **Verified:** fire → fresh routine session → ArtifactData read + write → ack visible on the page.
  Fire 13:47:16Z → session up 13:47:22Z → ack written 13:47:53Z, about **37 s** end to end. No approval prompt stalled the run.
- **Cost of one run:** ~45k tokens, reported as `cost_usd` 0.11 (an API-equivalent figure; on a subscription it draws plan usage).
  An hourly safety sync would be about 520 runs/month, roughly $57 equivalent. **Make the bridge event-driven only**
  (VPS fires on a new proposal, the page fires on approval), with at most a sync every few hours, or none.
- **Verified (owner click):** page button → `fire_trigger` via the Claude Code Remote connector → ack in about 25 s.

## Phase 1 status
- `kurumi/risk.py`: dial, ceilings, sizing, all reject rules. `kurumi/oanda.py`: stdlib v20 client, SL-mandatory order builder.
- Offline self-test passes (`python3 -m kurumi.test_risk`).
- **Not yet run against OANDA.** This container can't reach it. The VPS smoke test still has to confirm the response shapes,
  especially `homeConversions[].accountLoss` for sizing pairs whose quote currency differs from the account currency.

## Broker issue (2026-10-13)
- Owner resides in Costa Rica. The OANDA division is set by residence, not server location. Costa Rica appears either unsupported
  or under OANDA Global Markets (BVI), and OANDA's docs exclude Global Markets from v20 API access. The $450/mo figure on Capterra
  is OANDA's Exchange Rates data product, not the trading API.
- TradersUnion "Costa Rica" list (affiliate page): IUX, Exness, Pepperstone, XM, GTCFX, Valetax all say they accept residents. The page
  has nothing on APIs. All are MetaTrader brokers. The MT5 Python package is Windows-only, so it needs Wine or a Windows VPS. Avoid.
- Lead candidate: **Pepperstone + cTrader Open API**, which works on Linux. To verify: Costa Rica onboarding (probably the SCB Bahamas entity),
  and that this account can use the cTrader Open API.
- Only `kurumi/oanda.py` changes. The risk engine, page and bridge are broker-agnostic.

## Pepperstone Markets Ltd (SCB, Bahamas) terms: what affects the bot
Read 2026-10-13: Client T&Cs, Risk Disclosure, Order Execution Policy (also copy-trading, privacy, cookie, conflicts, complaints; nothing relevant in those).
- **Eligibility:** products are not for residents of Australia, UK, EU, US, or anywhere it would be unlawful (T&Cs, "Important").
  Costa Rica isn't named. Checking local law is the client's responsibility. Residence is what counts, not the server's location.
- **Automated trading is allowed at their discretion** (Risk Disclosure 12.4–12.5). They can restrict the account or change its password if
  "activity or server messages" are unreasonable. → The bot stays low-frequency: act on candle close, no tight polling loops.
- **Prohibited:** latency abuse, arbitrage, price/time manipulation, and strategies with "no downside market risk" (T&Cs 8.2(h), 12(m)).
  → Only directional strategies with real stop-losses. No cross-account hedging.
- **Stop-losses aren't guaranteed.** Slippage and gaps happen, mostly around news (Risk Disclosure 6.7, 7.1, 10.1). → Backtests must include
  SL slippage. Real loss can exceed the dial %. Feeds the news study.
- **Pepperstone is the counterparty** (principal, central risk book; Order Execution Policy 4.3, 6.1). Market orders may fill as a VWAP of several prices.
- **Their records prevail** (T&Cs 2.18), and they can void or adjust trades on pricing errors (2.21). → Each cycle the bot reconciles positions and
  equity from the broker and never trusts its own copy.
- **Rollover 16:59–17:01 NY**: trading disabled for 2–5 min, spreads widen (Risk Disclosure 6.9). Hours Sun 17:01 to Fri 16:55 NY (T&Cs 1.37).
  → **Implemented:** `risk.in_rollover` blocks 16:50–17:15 NY daily and the weekend.
- **Retail stop-out at 50% margin level**, and retail clients get negative balance protection (Risk Disclosure 5.3, 5.5).
- **Order types:** market, limit and stop-loss (T&Cs 2.12). Our market + SL/TP fits.
- **Not covered by any of these documents:** whether the cTrader Open API is available on this account. That's still open.
