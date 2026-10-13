"""Risk engine. The VPS is the only authority: every proposal and every approval goes through here."""
import math
from datetime import time
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")

# level: (risk per trade %, daily loss stop %, max open positions)
DIAL = {0: (0.0, 0.0, 0), 1: (0.25, 0.75, 2), 2: (0.5, 1.5, 3), 3: (1.0, 3.0, 3), 4: (1.5, 4.5, 4)}
CEILING = (2.0, 5.0, 5)  # hard caps; never configurable from the page
SCALES = (0.25, 0.5, 0.75, 1.0)  # per-proposal size scale, down only
MAX_DRIFT = 0.25  # reject if price moved > this fraction of the SL distance
MAX_SPREAD_X = 2.0  # reject if spread > this multiple of the pair's typical spread


def limits(level):
    risk, daily, max_open = DIAL[level]
    return min(risk, CEILING[0]), min(daily, CEILING[1]), min(max_open, CEILING[2])


def units(equity, risk_pct, entry, sl, quote_to_account, scale=1.0):
    """Units so that hitting SL loses risk_pct% * scale of equity (in account currency).

    quote_to_account: account-currency value of 1 unit of the pair's quote currency.
    Floors to whole units: rounding never adds risk.
    """
    per_unit_loss = abs(entry - sl) * quote_to_account
    if per_unit_loss <= 0:
        return 0
    # round first so float noise (1.1 - 1.098 = 0.0020000000000001) doesn't cost a unit
    return math.floor(round(equity * risk_pct / 100 * scale / per_unit_loss, 6))


def in_rollover(now_utc):
    """Pepperstone daily rollover 16:59-17:01 NY disables trading and widens spreads for 2-5 min
    (Risk Disclosure 6.9); the platform closes Fri 16:55 and reopens Sun 17:01 NY (T&Cs 1.37).
    Block a margin around both."""
    ny = now_utc.astimezone(NY)
    t, wd = ny.time(), ny.weekday()  # Mon=0 .. Sun=6
    if time(16, 50) <= t < time(17, 15):
        return True
    return (wd == 4 and t >= time(16, 50)) or wd == 5 or (wd == 6 and t < time(17, 15))


def reasons_to_reject(p, s):
    """p: proposal dict (side 'buy'|'sell', entry, sl, tp, scale).
    s: state dict (now: aware UTC datetime, level, equity, day_start_equity, open_positions, price, spread, typical_spread).
    Returns a list of reasons; empty list means OK to place."""
    r = []
    risk, daily, max_open = limits(s["level"])
    side, entry, sl, tp = p["side"], p["entry"], p.get("sl"), p.get("tp")
    if risk == 0:
        r.append("risk dial is paused")
    if sl is None:
        r.append("no stop-loss")
    elif (side == "buy" and sl >= entry) or (side == "sell" and sl <= entry):
        r.append("stop-loss on the wrong side of entry")
    if tp is not None and ((side == "buy" and tp <= entry) or (side == "sell" and tp >= entry)):
        r.append("take-profit on the wrong side of entry")
    if p.get("scale", 1.0) not in SCALES:
        r.append("size scale must be one of %s" % (SCALES,))
    loss_pct = (s["day_start_equity"] - s["equity"]) / s["day_start_equity"] * 100
    if loss_pct >= daily:
        r.append("daily loss stop reached (%.2f%% >= %.2f%%)" % (loss_pct, daily))
    if s["open_positions"] >= max_open:
        r.append("max open positions reached (%d)" % max_open)
    if sl is not None and abs(s["price"] - entry) > MAX_DRIFT * abs(entry - sl):
        r.append("price drifted too far from proposed entry")
    if in_rollover(s["now"]):
        r.append("inside rollover / weekend window")
    if s["spread"] > MAX_SPREAD_X * s["typical_spread"]:
        r.append("spread too wide")
    return r
