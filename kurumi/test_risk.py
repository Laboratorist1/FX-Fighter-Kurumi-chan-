from kurumi.risk import CEILING, DIAL, limits, reasons_to_reject, units

# Sizing: 10k USD equity, 1% risk, EUR/USD 20-pip stop -> lose $100 at SL -> 50,000 units.
assert units(10_000, 1.0, 1.1000, 1.0980, 1.0) == 50_000
assert units(10_000, 1.0, 1.1000, 1.0980, 1.0, scale=0.5) == 25_000
# USD/JPY with a USD account: 1 JPY = 1/150 USD. 30-pip stop, 0.5% risk -> lose $50 -> 25,000 units.
assert units(10_000, 0.5, 150.00, 149.70, 1 / 150) == 25_000
assert units(10_000, 1.0, 1.1, 1.1, 1.0) == 0  # zero stop distance never sizes

# Every dial level stays under the hard ceiling.
assert all(a <= c for lvl in DIAL for a, c in zip(limits(lvl), CEILING))

ok_state = dict(level=2, equity=10_000, day_start_equity=10_000, open_positions=0,
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
print("risk ok")

# Order body: sign by side, prices at pair precision, SL mandatory.
from kurumi.oanda import order_body
b = order_body("USD_JPY", "sell", 25_000, 150.30, 149.40, 3)["order"]
assert b["units"] == "-25000" and b["stopLossOnFill"] == {"price": "150.300"} and b["takeProfitOnFill"] == {"price": "149.400"}
try:
    order_body("EUR_USD", "buy", 1000, None, None, 5)
    raise AssertionError("order without SL was built")
except ValueError:
    pass
print("order ok")
