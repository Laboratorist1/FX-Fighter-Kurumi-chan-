# FX Fighter Kurumi-chan

Human-in-the-loop FX bot (OANDA). See [PLAN.md](PLAN.md).

## Phase 1 — run on the VPS (Python 3.10+, no dependencies)

```sh
python3 -m kurumi.test_risk                 # offline self-test of risk engine + order builder
export OANDA_TOKEN=... OANDA_ACCOUNT=...    # practice account; never commit these
python3 -m kurumi.oanda                     # read-only smoke test: account, prices, candles. Places nothing.
```
