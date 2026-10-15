"""Minimal cTrader Open API client: JSON over WebSocket, port 5036.

Message ids and fields come from spotware/openapi-proto-messages (OpenApiMessages.proto).
Env: CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID, CTRADER_LIVE=1 for live.
Smoke test (read-only, places nothing): python -m kurumi.ctrader
"""
import json
import os
import time
import uuid

from websockets.sync.client import connect

HOSTS = {False: "wss://demo.ctraderapi.com:5036", True: "wss://live.ctraderapi.com:5036"}
PRICE = 100_000  # prices and relative SL/TP are in 1/100000 of a price unit
PERIODS = {"M15": (7, 15), "H1": (9, 60), "H4": (10, 240), "D1": (12, 1440)}  # name: (enum, minutes)
HEARTBEAT, ERROR_RES, OA_ERROR_RES, ORDER_ERROR_EVENT, EXECUTION_EVENT = 51, 50, 2142, 2132, 2126
FILLED, REJECTED = 3, 7


def order_req(account, symbol_id, side, units, sl_dist, tp_dist, digits, step_volume, min_volume):
    """MARKET order payload. cTrader rejects absolute SL/TP on MARKET orders, so both are distances
    from the fill price. SL is mandatory. Volume is in 1/100 units, floored to the symbol's step."""
    if not sl_dist or sl_dist <= 0:
        raise ValueError("refusing to build an order without a stop-loss")
    volume = units * 100 // step_volume * step_volume
    if volume < min_volume:
        raise ValueError(f"volume {volume} below symbol minimum {min_volume}")
    grain = 10 ** (5 - digits)  # distances must sit on the symbol's price grid
    rel = lambda d: max(grain, round(d * PRICE / grain) * grain)
    p = {"ctidTraderAccountId": account, "symbolId": symbol_id, "orderType": 1,
         "tradeSide": 1 if side == "buy" else 2, "volume": volume, "relativeStopLoss": rel(sl_dist)}
    if tp_dist:
        p["relativeTakeProfit"] = rel(tp_dist)
    return p


class CTrader:
    def __init__(self, live=None):
        e = os.environ
        self.client_id, self.secret = e["CTRADER_CLIENT_ID"], e["CTRADER_CLIENT_SECRET"]
        self.token, self.account = e["CTRADER_ACCESS_TOKEN"], int(e["CTRADER_ACCOUNT_ID"])
        self.host = HOSTS[e.get("CTRADER_LIVE") == "1" if live is None else live]

    # ponytail: one short connection per cycle, no heartbeat loop. Add heartbeats if a session ever lasts > ~10 s idle.
    def __enter__(self):
        self.ws = connect(self.host, open_timeout=15)
        self._call(2100, {"clientId": self.client_id, "clientSecret": self.secret})
        self._call(2102, {"ctidTraderAccountId": self.account, "accessToken": self.token})
        self._symbols = None
        return self

    def __exit__(self, *a):
        self.ws.close()

    def _send(self, ptype, payload):
        mid = uuid.uuid4().hex[:12]
        self.ws.send(json.dumps({"clientMsgId": mid, "payloadType": ptype, "payload": payload}))
        return mid

    def _recv(self, timeout=15):
        m = json.loads(self.ws.recv(timeout=timeout))
        if m["payloadType"] in (ERROR_RES, OA_ERROR_RES):
            raise RuntimeError(f"cTrader error: {m.get('payload')}")
        return m

    def _call(self, ptype, payload):
        """Send a request and return the payload of the reply carrying the same clientMsgId."""
        mid = self._send(ptype, payload)
        while True:
            m = self._recv()
            if m.get("clientMsgId") == mid:
                return m.get("payload", {})

    def _acc(self, **kw):
        return {"ctidTraderAccountId": self.account, **kw}

    def trader(self):
        """balance (float), currency, leverage, broker, isLimitedRisk."""
        t = self._call(2121, self._acc())["trader"]
        assets = {a["assetId"]: a["name"] for a in self._call(2112, self._acc())["asset"]}
        return {"balance": t["balance"] / 10 ** t.get("moneyDigits", 2), "currency": assets.get(t["depositAssetId"]),
                "leverage": t.get("leverageInCents", 0) / 100, "broker": t.get("brokerName"),
                "limited_risk": t.get("isLimitedRisk", False)}

    def equity(self):
        bal = self.trader()["balance"]
        r = self._call(2187, self._acc())
        pnl = sum(p["netUnrealizedPnL"] for p in r.get("positionUnrealizedPnL", []))
        return bal + pnl / 10 ** r["moneyDigits"]

    def symbol_id(self, name):
        """name like 'EURUSD' or 'EUR/USD'."""
        if self._symbols is None:
            self._symbols = {s["symbolName"].replace("/", "").upper(): s["symbolId"]
                             for s in self._call(2114, self._acc())["symbol"] if "symbolName" in s}
        return self._symbols[name.replace("/", "").replace("_", "").upper()]

    def symbol(self, name):
        return self._call(2116, self._acc(symbolId=[self.symbol_id(name)]))["symbol"][0]

    def candles(self, name, period="H1", count=200):
        """Completed bars only, oldest first: dicts with time (unix s), o, h, l, c, volume."""
        enum, minutes = PERIODS[period]
        now = int(time.time() * 1000)
        r = self._call(2137, self._acc(symbolId=self.symbol_id(name), period=enum,
                                       fromTimestamp=now - (count + 5) * minutes * 60_000, toTimestamp=now))
        bars = []
        for b in r.get("trendbar", []):
            low, t = b["low"], b["utcTimestampInMinutes"] * 60
            if (t + minutes * 60) * 1000 > now:
                continue  # still forming
            bars.append({"time": t, "volume": b["volume"], "l": low / PRICE, "o": (low + b.get("deltaOpen", 0)) / PRICE,
                         "h": (low + b.get("deltaHigh", 0)) / PRICE, "c": (low + b.get("deltaClose", 0)) / PRICE})
        return bars[-count:]

    def price(self, name, timeout=10):
        """Current bid/ask from the first spot event carrying both."""
        sid = self.symbol_id(name)
        self._call(2127, self._acc(symbolId=[sid]))
        bid = ask = None
        end = time.time() + timeout
        while time.time() < end and not (bid and ask):
            m = self._recv(timeout=timeout)
            p = m.get("payload", {})
            if m["payloadType"] == 2131 and p.get("symbolId") == sid:
                bid, ask = p.get("bid", bid), p.get("ask", ask)
        if not (bid and ask):
            raise TimeoutError(f"no full quote for {name}")
        return {"bid": bid / PRICE, "ask": ask / PRICE}

    def positions(self):
        return self._call(2124, self._acc()).get("position", [])

    def market_order(self, name, side, units, sl_dist, tp_dist=None):
        """Place, then wait for FILLED. Returns the execution event payload; raises on reject or error."""
        s = self.symbol(name)
        req = order_req(self.account, s["symbolId"], side, units, sl_dist, tp_dist,
                        s["digits"], s.get("stepVolume", 100), s.get("minVolume", 100))
        self._send(2106, req)
        end = time.time() + 30
        while time.time() < end:
            m = self._recv(timeout=30)
            p = m.get("payload", {})
            if m["payloadType"] == ORDER_ERROR_EVENT:
                raise RuntimeError(f"order error: {p}")
            if m["payloadType"] == EXECUTION_EVENT:
                if p.get("executionType") == REJECTED:
                    raise RuntimeError(f"order rejected: {p.get('errorCode')}")
                if p.get("executionType") == FILLED:
                    return p
        raise TimeoutError("no fill confirmation in 30 s; check positions() before retrying")


if __name__ == "__main__":
    with CTrader() as c:
        print("trader", c.trader())
        print("equity", c.equity())
        s = c.symbol("EURUSD")
        print("EURUSD", {k: s.get(k) for k in ("symbolId", "digits", "pipPosition", "minVolume", "stepVolume", "lotSize")})
        print("price", c.price("EURUSD"))
        for b in c.candles("EURUSD", "H1", 3):
            print(b)
        print("open positions", len(c.positions()))
