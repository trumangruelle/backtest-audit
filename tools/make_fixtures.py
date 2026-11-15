"""Regenerate everything in fixtures/ from a fixed seed.

All of it is synthetic. Six invented tickers (six-letter names, so none can be a
real US symbol) get two years of weekday bars from a seeded random walk:

    ALDERS BIRCHS CEDARS MAPLES   ordinary
    WILLOW                        falls steadily, then delists: its file just ends
    SPRUCE                        splits 2-for-1; prices/ has it UNADJUSTED
                                  (prices halve overnight), prices_adjusted/ does not

Then one toy strategy is run twice. The strategy: at a close that beats the
previous 20 closes, go long; at a close below all of them, go short; hold 10
bars (or to the ticker's last bar); size about $10,000 at the signal close; one
position per ticker.

    trades_clean.csv    done right: fills at the next bar's open, the point-in-time
                        universe (universe.csv), adjusted prices, and 5 bp
                        commission + 5 bp slippage charged on each side
    trades_planted.csv  done wrong, five ways:
                        (a) look-ahead: longs fill at the OPEN of the bar whose
                            CLOSE fired the signal
                        (b) survivorship: the universe is today's survivors
                            (universe_survivors.csv), so WILLOW is never seen
                        (c) unadjusted prices: a short held across SPRUCE's split
                            books the halving as profit
                        (d) three fills at prices the bar never traded
                        (e) no costs at all

planted_errors.json is the answer key the tests hold the audit to.

Run from the project root:  python tools/make_fixtures.py   (or give an output folder)
"""

import csv
import json
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

SEED = 1729
FIRST_DAY = date(2023, 1, 2)
LAST_DAY = date(2024, 12, 31)
LOOKBACK = 20
HOLD_BARS = 10
NOTIONAL = 10_000
COST_BP, SLIPPAGE_BP = 5, 5

# name: start price, daily drift, daily volatility, and what is special about it
TICKERS = {
    "ALDERS": dict(start=40.0, drift=0.0005, vol=0.016),
    "BIRCHS": dict(start=85.0, drift=0.0002, vol=0.012),
    "CEDARS": dict(start=22.0, drift=0.0006, vol=0.021),
    "MAPLES": dict(start=130.0, drift=-0.0001, vol=0.011),
    "WILLOW": dict(start=35.0, drift=-0.0030, vol=0.024, last_bar=380),
    "SPRUCE": dict(start=30.0, drift=0.0004, vol=0.015, split_bar=300),
}
SPLIT_RATIO = 2
OUTSIDE_FILLS = 3          # how many fills to move outside their bar in the planted log


def weekdays():
    day, out = FIRST_DAY, []
    while day <= LAST_DAY:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def make_bars(rng, spec, days):
    """Adjusted bars: a list of dicts with date, open, high, low, close, volume."""
    bars, prev_close = [], spec["start"]
    for i, day in enumerate(days[: spec.get("last_bar", len(days))]):
        drift = spec["drift"]
        split = spec.get("split_bar")
        if split is not None and split - 9 <= i <= split - 6:
            drift = -0.02                  # a sharp dip just before the split
        o = prev_close * math.exp(rng.gauss(0, spec["vol"] * 0.3))
        c = o * math.exp(drift + rng.gauss(0, spec["vol"]))
        h = max(o, c) * (1 + abs(rng.gauss(0, spec["vol"] * 0.5)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, spec["vol"] * 0.5)))
        o, c, h, lo = (round(x, 2) for x in (o, c, h, lo))
        bars.append(dict(date=day, open=o, high=max(h, o, c), low=min(lo, o, c), close=c,
                         volume=rng.randrange(200_000, 1_500_000)))
        prev_close = c
    return bars


def unadjust(bars, split_bar):
    """What a vendor delivers before adjusting: prices before the split are twice as high."""
    out = []
    for i, b in enumerate(bars):
        if i < split_bar:
            b = dict(b, volume=b["volume"] // SPLIT_RATIO,
                     **{k: round(b[k] * SPLIT_RATIO, 2) for k in ("open", "high", "low", "close")})
        out.append(b)
    return out


def run_strategy(prices, universe, look_ahead_longs, cost_bp):
    """The toy breakout strategy. Returns trades sorted by entry date, then ticker."""
    trades = []
    for ticker, bars in prices.items():
        t = LOOKBACK
        while t < len(bars):
            bar = bars[t]
            if (bar["date"], ticker) not in universe:
                t += 1
                continue
            window = [b["close"] for b in bars[t - LOOKBACK:t]]
            side = "long" if bar["close"] > max(window) else "short" if bar["close"] < min(window) else None
            entry = t if (side == "long" and look_ahead_longs) else t + 1
            if side is None or entry >= len(bars):
                t += 1
                continue
            exit_ = min(entry + HOLD_BARS - 1, len(bars) - 1)
            size = int(NOTIONAL // bar["close"])
            ep, xp = bars[entry]["open"], bars[exit_]["close"]
            costs = round((ep * size + xp * size) * cost_bp / 10_000, 2)   # as audit.py states it
            sign = 1 if side == "long" else -1
            trades.append(dict(ticker=ticker, side=side, size=size, signal_date=bar["date"],
                               entry_date=bars[entry]["date"], entry_price=ep,
                               exit_date=bars[exit_]["date"], exit_price=xp, costs=costs,
                               pnl=round(sign * (xp * 1.0 - ep) * size - costs, 2)))
            t = max(exit_, t + 1)   # the next signal can come at the exit bar's close
    trades.sort(key=lambda r: (r["entry_date"], r["ticker"]))
    for i, tr in enumerate(trades, 1):
        tr["trade_id"] = i
    return trades


def plant_outside_fills(trades, prices, avoid):
    """Move a few short trades' fills to prices outside their bar, each in the trade's favour."""
    by_date = {tk: {b["date"]: b for b in bars} for tk, bars in prices.items()}
    shorts = [tr for tr in trades if tr["side"] == "short" and tr["trade_id"] not in avoid]
    chosen = [shorts[len(shorts) * k // (OUTSIDE_FILLS + 1)] for k in range(1, OUTSIDE_FILLS + 1)]
    for k, tr in enumerate(chosen):
        if k < 2:   # sold above the day's high
            tr["entry_price"] = round(by_date[tr["ticker"]][tr["entry_date"]]["high"] * 1.012, 2)
        else:       # bought back below the day's low
            tr["exit_price"] = round(by_date[tr["ticker"]][tr["exit_date"]]["low"] * 0.988, 2)
        tr["pnl"] = round(-(tr["exit_price"] * 1.0 - tr["entry_price"]) * tr["size"] - tr["costs"], 2)
    return [tr["trade_id"] for tr in chosen]


def write_bars(path, bars):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b["date"].isoformat(), f"{b['open']:.2f}", f"{b['high']:.2f}",
                        f"{b['low']:.2f}", f"{b['close']:.2f}", b["volume"]])


def write_universe(path, pairs):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["date", "ticker"])
        for d, tk in sorted(pairs):
            w.writerow([d.isoformat(), tk])


TRADE_COLUMNS = ["trade_id", "ticker", "side", "size", "signal_date", "entry_date", "entry_price",
                 "exit_date", "exit_price", "costs", "pnl"]


def write_trades(path, trades):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(TRADE_COLUMNS)
        for tr in trades:
            row = dict(tr)
            for k in ("entry_price", "exit_price", "costs", "pnl"):
                row[k] = f"{tr[k]:.2f}"
            for k in ("signal_date", "entry_date", "exit_date"):
                row[k] = tr[k].isoformat()
            w.writerow([row[k] for k in TRADE_COLUMNS])


def main(out=None):
    out = Path(out) if out else Path(__file__).resolve().parent.parent / "fixtures"
    days = weekdays()
    adjusted = {}
    for i, (ticker, spec) in enumerate(TICKERS.items()):
        adjusted[ticker] = make_bars(random.Random(SEED + i), spec, days)
    split_bar = TICKERS["SPRUCE"]["split_bar"]
    raw = dict(adjusted, SPRUCE=unadjust(adjusted["SPRUCE"], split_bar))
    split_date = adjusted["SPRUCE"][split_bar]["date"]

    point_in_time = {(b["date"], tk) for tk, bars in adjusted.items() for b in bars}
    survivors = [tk for tk, bars in adjusted.items() if bars[-1]["date"] == days[-1]]
    survivor_universe = {(d, tk) for d in days for tk in survivors}

    clean = run_strategy(adjusted, point_in_time, False, COST_BP + SLIPPAGE_BP)
    planted = run_strategy(raw, survivor_universe, True, 0)

    look_ahead = [tr["trade_id"] for tr in planted if tr["entry_date"] <= tr["signal_date"]]
    across_split = [tr["trade_id"] for tr in planted
                    if tr["ticker"] == "SPRUCE" and tr["entry_date"] < split_date <= tr["exit_date"]]
    assert across_split and all(planted[i - 1]["side"] == "short" for i in across_split), \
        "the planted log must hold a short across the split"
    outside = plant_outside_fills(planted, raw, set(look_ahead) | set(across_split))
    willow = adjusted["WILLOW"]

    for folder, data in (("prices", raw), ("prices_adjusted", adjusted)):
        (out / folder).mkdir(parents=True, exist_ok=True)
        for tk, bars in data.items():
            write_bars(out / folder / f"{tk}.csv", bars)
    write_universe(out / "universe.csv", point_in_time)
    write_universe(out / "universe_survivors.csv", survivor_universe)
    write_trades(out / "trades_clean.csv", clean)
    write_trades(out / "trades_planted.csv", planted)
    key = {
        "look_ahead": look_ahead,
        "survivorship": {"ticker": "WILLOW", "days_missing": len(willow),
                         "last_bar": willow[-1]["date"].isoformat()},
        "unadjusted_split": {"ticker": "SPRUCE", "date": split_date.isoformat(),
                             "ratio": SPLIT_RATIO, "trades": across_split},
        "fill_outside_bar": outside,
        "no_costs": len(planted),
        "trades": {"planted": len(planted), "clean": len(clean)},
    }
    (out / "planted_errors.json").write_text(json.dumps(key, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(planted)} planted trades, {len(clean)} clean trades")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
