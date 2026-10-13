"""Minimal OANDA v20 REST client (stdlib only).

Env: OANDA_TOKEN, OANDA_ACCOUNT, OANDA_LIVE=1 for the live host (default: practice).
Smoke test (read-only, places nothing): python -m kurumi.oanda
"""
import json
import os
import urllib.parse
import urllib.request

HOSTS = {False: "https://api-fxpractice.oanda.com", True: "https://api-fxtrade.oanda.com"}


def order_body(instrument, side, units, sl, tp, precision):
    """MARKET order with SL (mandatory) and TP attached on fill. Prices as strings at the pair's precision."""
    if sl is None:
        raise ValueError("refusing to build an order without a stop-loss")
    if units <= 0:
        raise ValueError("units must be positive")
    fmt = lambda x: f"{x:.{precision}f}"
    order = {
        "type": "MARKET", "instrument": instrument, "timeInForce": "FOK", "positionFill": "DEFAULT",
        "units": str(units if side == "buy" else -units),
        "stopLossOnFill": {"price": fmt(sl)},
    }
    if tp is not None:
        order["takeProfitOnFill"] = {"price": fmt(tp)}
    return {"order": order}


class Oanda:
    def __init__(self, token=None, account=None, live=None):
        self.token = token or os.environ["OANDA_TOKEN"]
        self.account = account or os.environ["OANDA_ACCOUNT"]
        live = os.environ.get("OANDA_LIVE") == "1" if live is None else live
        self.host = HOSTS[live]

    def _req(self, method, path, params=None, body=None):
        url = self.host + path + ("?" + urllib.parse.urlencode(params) if params else "")
        req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None, headers={
            "Authorization": "Bearer " + self.token, "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)

    def summary(self):
        return self._req("GET", f"/v3/accounts/{self.account}/summary")["account"]

    def instrument(self, name):
        return self._req("GET", f"/v3/accounts/{self.account}/instruments", {"instruments": name})["instruments"][0]

    def candles(self, instrument, granularity="H1", count=200):
        """Completed mid candles only, oldest first: dicts with time, o, h, l, c, volume."""
        raw = self._req("GET", f"/v3/instruments/{instrument}/candles",
                        {"granularity": granularity, "count": count, "price": "M"})["candles"]
        return [{"time": c["time"], "volume": c["volume"], **{k: float(v) for k, v in c["mid"].items()}}
                for c in raw if c["complete"]]

    def price(self, instrument):
        """bid, ask, and quote->account conversion for losses (homeConversions.accountLoss)."""
        r = self._req("GET", f"/v3/accounts/{self.account}/pricing",
                      {"instruments": instrument, "includeHomeConversions": "true"})
        p = r["prices"][0]
        quote = instrument.split("_")[1]
        conv = {h["currency"]: float(h["accountLoss"]) for h in r.get("homeConversions", [])}
        return {"bid": float(p["bids"][0]["price"]), "ask": float(p["asks"][0]["price"]),
                "tradeable": p.get("tradeable", True), "quote_to_account": conv.get(quote)}

    def open_trades(self):
        return self._req("GET", f"/v3/accounts/{self.account}/openTrades")["trades"]

    def market_order(self, instrument, side, units, sl, tp, precision):
        return self._req("POST", f"/v3/accounts/{self.account}/orders",
                         body=order_body(instrument, side, units, sl, tp, precision))


if __name__ == "__main__":
    o = Oanda()
    s = o.summary()
    print("account", s["currency"], "balance", s["balance"], "NAV", s["NAV"], "open trades", s["openTradeCount"])
    print("EUR_USD", o.instrument("EUR_USD"))
    print("price", o.price("EUR_USD"))
    print("USD_JPY price", o.price("USD_JPY"))
    for c in o.candles("EUR_USD", "H1", 4)[-3:]:
        print(c)
