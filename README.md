# FX Fighter Kurumi-chan

Human-in-the-loop FX bot (Pepperstone via cTrader Open API). See [PLAN.md](PLAN.md).

## Run on the VPS (Python 3.10+)

```sh
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python3 -m kurumi.test_risk          # offline self-test: risk engine + order builder
# Credentials from openapi.ctrader.com (never commit; keep in a root-only env file)
export CTRADER_CLIENT_ID=... CTRADER_CLIENT_SECRET=... CTRADER_ACCESS_TOKEN=... CTRADER_ACCOUNT_ID=...
python3 -m kurumi.ctrader            # read-only smoke test: account, equity, EURUSD spec/price/candles. Places nothing.
```
