from kurumi.risk import CEILING, DIAL, limits, reasons_to_reject, units

# Sizing: 10k USD equity, 1% risk, EUR/USD 20-pip stop -> lose $100 at SL -> 50,000 units.
assert units(10_000, 1.0, 1.1000, 1.0980, 1.0) == 50_000
assert units(10_000, 1.0, 1.1000, 1.0980, 1.0, scale=0.5) == 25_000
# USD/JPY with a USD account: 1 JPY = 1/150 USD. 30-pip stop, 0.5% risk -> lose $50 -> 25,000 units.
assert units(10_000, 0.5, 150.00, 149.70, 1 / 150) == 25_000
assert units(10_000, 1.0, 1.1, 1.1, 1.0) == 0  # zero stop distance never sizes

# Every dial level stays under the hard ceiling.
assert all(a <= c for lvl in DIAL for a, c in zip(limits(lvl), CEILING))

from datetime import datetime, timezone
UTC = timezone.utc
ok_state = dict(now=datetime(2026, 10, 13, 14, 0, tzinfo=UTC), level=2, equity=10_000, day_start_equity=10_000, open_positions=0,
                price=1.1000, spread=0.00012, typical_spread=0.0001)
buy = dict(side="buy", entry=1.1000, sl=1.0980, tp=1.1040, scale=1.0)
assert reasons_to_reject(buy, ok_state) == []


def rejected(p=buy, **state):
    return reasons_to_reject(p, {**ok_state, **state})


assert rejected(p={**buy, "sl": None})                       # mandatory SL
assert rejected(p={**buy, "sl": 1.1010})                     # SL above entry on a buy
assert rejected(p={**buy, "side": "sell"})                   # same levels are wrong for a sell
assert rejected(p={**buy, "scale": 1.5})                     # scale can only go down
assert rejected(level=0)                                     # paused
assert rejected(equity=9_850)                                # -1.5% hits level 2 daily stop
assert not rejected(equity=9_860)                            # -1.4% is fine
assert rejected(open_positions=3)                            # level 2 max open
assert rejected(price=1.1006)                                # 6 pips > 25% of 20-pip stop
assert not rejected(price=1.1004)                            # 4 pips is within
assert rejected(spread=0.00021)                              # > 2x typical
assert rejected(now=datetime(2026, 10, 13, 21, 0, tzinfo=UTC))   # 17:00 NY (EDT) rollover
assert rejected(now=datetime(2026, 1, 13, 22, 0, tzinfo=UTC))    # 17:00 NY (EST) rollover
assert rejected(now=datetime(2026, 10, 17, 12, 0, tzinfo=UTC))   # Saturday
assert not rejected(now=datetime(2026, 10, 18, 21, 30, tzinfo=UTC))  # Sun 17:30 NY, open
assert not rejected(now=datetime(2026, 10, 13, 21, 30, tzinfo=UTC))  # Tue 17:30 NY, open
print("risk ok")

# cTrader order payload: relative SL/TP on the price grid, volume in 1/100 units floored to step, SL mandatory.
from kurumi.ctrader import order_req
o = order_req(1, 22, "sell", 25_000, 0.30, 0.90, digits=3, step_volume=100_000, min_volume=100_000)
assert o["tradeSide"] == 2 and o["volume"] == 2_500_000 and o["relativeStopLoss"] == 30_000 and o["relativeTakeProfit"] == 90_000
o = order_req(1, 1, "buy", 12_345, 0.00203, None, digits=5, step_volume=100_000, min_volume=100_000)
assert o["volume"] == 1_200_000 and o["relativeStopLoss"] == 203 and "relativeTakeProfit" not in o  # floored to 1k-unit step
for bad in (dict(sl_dist=None), dict(sl_dist=0.002, units=500)):
    try:
        order_req(1, 1, "buy", bad.get("units", 10_000), bad["sl_dist"], None, 5, 100_000, 100_000)
        raise AssertionError("bad order was built")
    except ValueError:
        pass
print("order ok")
