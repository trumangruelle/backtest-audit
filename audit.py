"""Backtest audit: data-level checks that catch the usual reasons a backtest lies.

    python audit.py --trades TRADES.csv --prices PRICE_DIR --universe UNIVERSE.csv
                    [--cost-bp N] [--slippage-bp N] [--report report.md]

Inputs
    trades    one row per round trip: trade_id, ticker, side (long/short), size,
              signal_date, entry_date, entry_price, exit_date, exit_price, costs, pnl
    prices    a folder of <TICKER>.csv daily bars: date, open, high, low, close, volume
    universe  date, ticker: the tickers the backtest was allowed to trade on each date

Five checks, one per error kind:
    look-ahead        an entry on or before the bar whose close fired the signal
    survivorship      a ticker that later delists is missing from the universe on days it traded
    unadjusted split  a trade held across a split-shaped overnight gap in the prices
    fill outside bar  an entry or exit price the bar never traded (or no bar that day)
    costs             costs charged below the stated commission + slippage

It prints a summary, writes a Markdown report, and exits 1 if any check fails
(0 if all pass, 2 if an input cannot be read). Standard library only.
"""

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SPLIT_RATIOS = (2, 3, 4, 5, 10)   # old price / new price; the reverse (1/2 ...) is a reverse split
SPLIT_TOLERANCE = 0.03            # an overnight ratio within 3% of one of those looks like a split
PRICE_TOLERANCE = 0.005           # half a cent, for prices written to two decimals
SHOW_ROWS = 5                     # offending rows shown per check
TRADE_COLUMNS = ("trade_id", "ticker", "side", "size", "signal_date", "entry_date", "entry_price",
                 "exit_date", "exit_price", "costs", "pnl")


class InputError(Exception):
    """An input file is missing or malformed; exit code 2."""


@dataclass
class Bar:
    open: float
    high: float
    low: float
    close: float


@dataclass
class Trade:
    trade_id: str
    ticker: str
    side: str
    size: float
    signal_date: date
    entry_date: date
    entry_price: float
    exit_date: date
    exit_price: float
    costs: float
    pnl: float

    @property
    def sign(self):
        return 1 if self.side == "long" else -1


@dataclass
class Split:
    ticker: str
    date: date          # the first bar at the new price
    ratio: float        # old price / new price: 2 for a 2-for-1 split, 0.5 for a 1-for-2


@dataclass
class Check:
    name: str
    passed: bool
    summary: str
    columns: tuple = ()
    rows: list = field(default_factory=list)


# ---- reading ---------------------------------------------------------------

def _read_csv(path, required):
    path = Path(path)
    if not path.is_file():
        raise InputError(f"{path}: file not found")
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise InputError(f"{path}: missing column(s) {', '.join(missing)}")
        return list(reader)


def _parse(path, line, kind, text):
    try:
        return date.fromisoformat(text) if kind == "date" else float(text)
    except (TypeError, ValueError):
        raise InputError(f"{path} line {line}: {text!r} is not a {kind}") from None


def load_prices(folder):
    """{ticker: {date: Bar}}, dates in file order (oldest first)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise InputError(f"{folder}: folder not found")
    prices = {}
    for path in sorted(folder.glob("*.csv")):
        bars = {}
        for line, row in enumerate(_read_csv(path, ("date", "open", "high", "low", "close")), 2):
            d = _parse(path, line, "date", row["date"])
            bars[d] = Bar(*(_parse(path, line, "number", row[k]) for k in ("open", "high", "low", "close")))
        prices[path.stem] = dict(sorted(bars.items()))
    if not prices:
        raise InputError(f"{folder}: no .csv price files")
    return prices


def load_universe(path):
    rows = _read_csv(path, ("date", "ticker"))
    return {(_parse(path, i, "date", r["date"]), r["ticker"].strip()) for i, r in enumerate(rows, 2)}


def load_trades(path):
    trades = []
    for line, r in enumerate(_read_csv(path, TRADE_COLUMNS), 2):
        side = r["side"].strip().lower()
        if side not in ("long", "short"):
            raise InputError(f"{path} line {line}: side must be long or short, not {r['side']!r}")
        values = {k: _parse(path, line, "date" if k.endswith("_date") else "number", r[k])
                  for k in TRADE_COLUMNS if k not in ("trade_id", "ticker", "side")}
        trades.append(Trade(trade_id=r["trade_id"], ticker=r["ticker"].strip(), side=side, **values))
    return trades


# ---- the checks --------------------------------------------------------------

def check_look_ahead(trades):
    bad = [t for t in trades if t.entry_date <= t.signal_date]
    return Check(
        "look-ahead", not bad,
        f"{len(bad)} of {len(trades)} trades enter on or before the bar whose close fired the signal",
        ("trade", "ticker", "side", "signal bar", "entry"),
        [(t.trade_id, t.ticker, t.side, t.signal_date, t.entry_date) for t in bad])


def check_survivorship(prices, universe):
    last_day = max(d for bars in prices.values() for d in bars)
    rows = []
    for ticker, bars in prices.items():
        dates = list(bars)
        if dates[-1] == last_day:
            continue                                   # still trading at the end: not delisted
        missing = [d for d in dates if (d, ticker) not in universe]
        if missing:
            change = bars[dates[-1]].close / bars[missing[0]].close - 1
            rows.append((ticker, dates[-1], len(dates), len(missing), f"{change:+.0%}"))
    summary = ("every ticker that delists is in the universe on the days it traded" if not rows else
               "; ".join(f"{r[0]} delisted {r[1]}: missing from the universe on {r[3]} of the {r[2]} "
                         f"days it traded ({r[4]} over those days)" for r in rows))
    return Check("survivorship", not rows, summary,
                 ("ticker", "last bar", "days traded", "days missing", "price change while missing"), rows)


def find_splits(prices):
    """Overnight gaps (open / previous close) within SPLIT_TOLERANCE of a split ratio."""
    found = []
    for ticker, bars in prices.items():
        items = list(bars.items())
        for (_, before), (d, bar) in zip(items, items[1:]):
            gap = bar.open / before.close
            for r in SPLIT_RATIOS:
                for ratio in (r, 1 / r):
                    if abs(gap * ratio - 1) <= SPLIT_TOLERANCE:
                        found.append(Split(ticker, d, ratio))
    return found


def split_factor(trade, splits):
    """How many shares one share at entry became by the exit (1.0 when no split in between)."""
    factor = 1.0
    for s in splits:
        if s.ticker == trade.ticker and trade.entry_date < s.date <= trade.exit_date:
            factor *= s.ratio
    return factor


def _ratio_name(ratio):
    return f"{ratio:g}-for-1 split" if ratio >= 1 else f"1-for-{1 / ratio:g} reverse split"


def check_split(trades, splits):
    held = [t for t in trades if split_factor(t, splits) != 1.0]
    gaps = ", ".join(f"{s.ticker} {s.date} ({_ratio_name(s.ratio)})" for s in splits) or "none"
    return Check(
        "unadjusted split", not held,
        f"split-shaped gaps in the prices: {gaps}; {len(held)} trade(s) held across one",
        ("trade", "ticker", "side", "entry", "entry price", "exit", "exit price", "reported pnl"),
        [(t.trade_id, t.ticker, t.side, t.entry_date, f"{t.entry_price:.2f}", t.exit_date,
          f"{t.exit_price:.2f}", _money(t.pnl)) for t in held])


def check_fills(trades, prices):
    rows = []
    for t in trades:
        for leg, d, price in (("entry", t.entry_date, t.entry_price), ("exit", t.exit_date, t.exit_price)):
            bar = prices.get(t.ticker, {}).get(d)
            if bar is None:
                rows.append((t.trade_id, t.ticker, leg, d, f"{price:.2f}", "no bar that day"))
            elif not bar.low - PRICE_TOLERANCE <= price <= bar.high + PRICE_TOLERANCE:
                rows.append((t.trade_id, t.ticker, leg, d, f"{price:.2f}",
                             f"bar {bar.low:.2f} to {bar.high:.2f}"))
    return Check("fill outside bar", not rows,
                 f"{len(rows)} of {2 * len(trades)} fills are outside the bar's low-high range or have no bar",
                 ("trade", "ticker", "leg", "date", "price", "what the bar allows"), rows)


def expected_costs(trade, bp, factor=1.0):
    """Stated costs in currency: bp per side on the entry and exit notional."""
    notional = trade.entry_price * trade.size + trade.exit_price * trade.size * factor
    return round(notional * bp / 10_000, 2)


def check_costs(trades, bp, splits):
    rows = []
    for t in trades:
        want = expected_costs(t, bp, split_factor(t, splits))
        if t.costs < want - PRICE_TOLERANCE:
            rows.append((t.trade_id, t.ticker, _money(t.costs), _money(want)))
    charged = sum(t.costs for t in trades)
    stated = sum(expected_costs(t, bp, split_factor(t, splits)) for t in trades)
    return Check(
        "costs", not rows,
        f"{len(rows)} of {len(trades)} trades charged less than the stated {bp:g} bp per side "
        f"(charged {_money(charged)}, stated rate gives {_money(stated)})",
        ("trade", "ticker", "charged", "at the stated rate"), rows)


# ---- P&L -------------------------------------------------------------------

def recompute(trades, splits, bp):
    """Reported P&L next to P&L with splits corrected and the stated costs charged."""
    reported = sum(t.pnl for t in trades)
    as_logged, corrected, split_effect, cost_effect = 0.0, 0.0, 0.0, 0.0
    for t in trades:
        factor = split_factor(t, splits)
        naive = t.sign * (t.exit_price * 1.0 - t.entry_price) * t.size
        fixed = t.sign * (t.exit_price * factor - t.entry_price) * t.size
        want = expected_costs(t, bp, factor)
        as_logged += round(naive - t.costs, 2)
        corrected += round(fixed - want, 2)
        split_effect += fixed - naive
        cost_effect += t.costs - want
    return {"reported": reported, "recomputed": corrected, "split": split_effect,
            "costs": cost_effect, "arithmetic": reported - as_logged}


def _money(x):
    x = round(x, 2) + 0.0          # + 0.0 turns -0.0 into 0.0
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


# ---- running it --------------------------------------------------------------

def audit(trades, prices, universe, bp):
    splits = find_splits(prices)
    checks = [check_look_ahead(trades), check_survivorship(prices, universe), check_split(trades, splits),
              check_fills(trades, prices), check_costs(trades, bp, splits)]
    return checks, recompute(trades, splits, bp)


def pnl_of(trades, checks, name):
    ids = {row[0] for c in checks if c.name == name for row in c.rows}
    return len(ids), sum(t.pnl for t in trades if t.trade_id in ids)


def summary_lines(args, trades, prices, universe, checks, pnl):
    lines = [f"Backtest audit of {args.trades}: {len(trades)} trades, {len(prices)} price files, "
             f"{len(universe):,} universe rows",
             f"stated costs: {args.cost_bp:g} bp commission + {args.slippage_bp:g} bp slippage per side", ""]
    width = max(len(c.name) for c in checks)
    lines += [f"{'PASS' if c.passed else 'FAIL'}  {c.name:<{width}}  {c.summary}" for c in checks]
    lines += ["", f"P&L as reported:   {_money(pnl['reported'])}",
              f"P&L recomputed:    {_money(pnl['recomputed'])}  (splits corrected, stated costs charged)"]
    n, look_ahead_pnl = pnl_of(trades, checks, "look-ahead")
    if n:
        lines.append(f"not recomputed:    {n} look-ahead trades reported {_money(look_ahead_pnl)}; "
                     "rerun the backtest to price them")
    failed = sum(not c.passed for c in checks)
    lines += ["", f"{failed} of {len(checks)} checks failed" if failed else f"all {len(checks)} checks passed"]
    return lines


def report_markdown(args, trades, checks, pnl, lines):
    out = ["# Backtest audit", "", f"Trades: `{args.trades}`  ", f"Prices: `{args.prices}`  ",
           f"Universe: `{args.universe}`", "", "## Summary", "", "```", *lines, "```", ""]
    for c in checks:
        sentence = c.summary[0].upper() + c.summary[1:] + "."
        out += [f"## {'PASS' if c.passed else 'FAIL'}: {c.name}", "", sentence, ""]
        if c.rows:
            shown = c.rows[:SHOW_ROWS]
            label = f"All {len(shown)}" if len(shown) == len(c.rows) else f"First {len(shown)} of {len(c.rows)}"
            out += [label + ":", "",
                    "| " + " | ".join(c.columns) + " |", "|" + "---|" * len(c.columns)]
            out += ["| " + " | ".join(str(v) for v in row) + " |" for row in shown]
            out.append("")
    out += ["## P&L", "", "| | |", "|---|---:|",
            f"| As reported (sum of the pnl column) | {_money(pnl['reported'])} |",
            f"| Splits corrected | {_money(pnl['split'])} |",
            f"| Costs at the stated rate, less costs charged | {_money(pnl['costs'])} |"]
    if abs(pnl["arithmetic"]) >= 0.01:
        out.append(f"| The pnl column against its own prices, size and costs | {_money(-pnl['arithmetic'])} |")
    out += [f"| **Recomputed** | **{_money(pnl['recomputed'])}** |"]
    notes = []
    for name, what in (("look-ahead", "look-ahead entries"), ("fill outside bar", "fills the bar never traded")):
        n, value = pnl_of(trades, checks, name)
        if n:
            notes.append(f"- {n} trades with {what} reported {_money(value)} between them. They stay in the "
                         "recomputed figure, because the log cannot say what the right fill was.")
    if any(c.name == "survivorship" and not c.passed for c in checks):
        notes.append("- Trades the backtest never saw (the tickers missing from its universe) cannot be "
                     "recomputed from the log. Rerun it on the point-in-time universe.")
    out += [""] + notes + ([""] if notes else [])
    out += ["These are data-level checks on the log, the prices and the universe. Passing them does not "
            "show that the strategy has an edge.", ""]
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Audit a backtest's trade log against its prices and universe.")
    p.add_argument("--trades", required=True, help="trade log CSV")
    p.add_argument("--prices", required=True, help="folder of <TICKER>.csv daily bars")
    p.add_argument("--universe", required=True, help="CSV of date,ticker the backtest could trade")
    p.add_argument("--cost-bp", type=float, default=5.0, help="commission per side, basis points (default 5)")
    p.add_argument("--slippage-bp", type=float, default=5.0, help="slippage per side, basis points (default 5)")
    p.add_argument("--report", default="report.md", help="where to write the report (default report.md)")
    args = p.parse_args(argv)
    if args.cost_bp < 0 or args.slippage_bp < 0:
        p.error("costs cannot be negative")
    try:
        trades = load_trades(args.trades)
        prices = load_prices(args.prices)
        universe = load_universe(args.universe)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    checks, pnl = audit(trades, prices, universe, args.cost_bp + args.slippage_bp)
    lines = summary_lines(args, trades, prices, universe, checks, pnl)
    Path(args.report).write_text(report_markdown(args, trades, checks, pnl, lines), encoding="utf-8")
    print("\n".join(lines + [f"report: {args.report}"]))
    return 0 if all(c.passed for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
