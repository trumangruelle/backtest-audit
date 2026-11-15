"""Mutation check: break each check on purpose, one way at a time, and confirm a test fails.

Each mutation is a realistic bug: a check switched off, a comparison off by one,
a tolerance widened, one side of the costs forgotten. The script copies the
project to a temporary folder, applies one mutation, runs the tests, restores
the file and moves on. A mutation that leaves every test passing would mean
that behaviour is not really tested.

Run from the project root:  python tools/mutation_check.py
The original project is never modified. Exit code 1 if any mutation survives.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
A = "audit.py"

MUTATIONS = [
    # ---- look-ahead -----------------------------------------------------------
    ("look-ahead: check switched off", A,
     "bad = [t for t in trades if t.entry_date <= t.signal_date]", "bad = []"),
    ("look-ahead: an entry ON the signal bar let through", A,
     "bad = [t for t in trades if t.entry_date <= t.signal_date]",
     "bad = [t for t in trades if t.entry_date < t.signal_date]"),

    # ---- survivorship ---------------------------------------------------------
    ("survivorship: check switched off", A,
     "        if missing:\n            change", "        if False:\n            change"),
    ("survivorship: every ticker treated as a survivor", A,
     "        if dates[-1] == last_day:", "        if True:"),
    ("survivorship: survivors judged too, not just delisted tickers", A,
     "    last_day = max(d for bars in prices.values() for d in bars)",
     "    last_day = min(d for bars in prices.values() for d in bars)"),
    ("survivorship: the first day it traded not compared", A,
     "missing = [d for d in dates if (d, ticker) not in universe]",
     "missing = [d for d in dates[1:] if (d, ticker) not in universe]"),
    ("survivorship: price change measured from the first bar, not the first missing day", A,
     "bars[dates[-1]].close / bars[missing[0]].close - 1", "bars[dates[-1]].close / bars[dates[0]].close - 1"),

    # ---- splits ---------------------------------------------------------------
    ("split: 2-for-1 not among the ratios", A,
     "SPLIT_RATIOS = (2, 3, 4, 5, 10)", "SPLIT_RATIOS = (3, 4, 5, 10)"),
    ("split: reverse splits not looked for", A,
     "                for ratio in (r, 1 / r):", "                for ratio in (r,):"),
    ("split: tolerance widened so ordinary gaps look like splits", A,
     "SPLIT_TOLERANCE = 0.03 ", "SPLIT_TOLERANCE = 0.15 "),
    ("split: a trade entered on the split day counted as held across it", A,
     "trade.entry_date < s.date <= trade.exit_date", "trade.entry_date <= s.date <= trade.exit_date"),
    ("split: a trade exiting on the split day not counted", A,
     "trade.entry_date < s.date <= trade.exit_date", "trade.entry_date < s.date < trade.exit_date"),
    ("split: another ticker's split applied", A,
     "        if s.ticker == trade.ticker and trade", "        if trade"),
    ("split: P&L not corrected for it", A,
     "        fixed = t.sign * (t.exit_price * factor - t.entry_price) * t.size",
     "        fixed = naive"),

    # ---- fills ----------------------------------------------------------------
    ("fills: only the high checked", A,
     "elif not bar.low - PRICE_TOLERANCE <= price <= bar.high + PRICE_TOLERANCE:",
     "elif not price <= bar.high + PRICE_TOLERANCE:"),
    ("fills: exits not checked", A,
     '(("entry", t.entry_date, t.entry_price), ("exit", t.exit_date, t.exit_price))',
     '(("entry", t.entry_date, t.entry_price),)'),
    ("fills: a fill on a day with no bar accepted", A,
     '                rows.append((t.trade_id, t.ticker, leg, d, f"{price:.2f}", "no bar that day"))',
     "                pass"),
    ("fills and costs: tolerance widened to two cents", A,
     "PRICE_TOLERANCE = 0.005 ", "PRICE_TOLERANCE = 0.02 "),

    # ---- costs ----------------------------------------------------------------
    ("costs: check switched off", A,
     "        if t.costs < want - PRICE_TOLERANCE:", "        if False:"),
    ("costs: only the entry side charged", A,
     "    notional = trade.entry_price * trade.size + trade.exit_price * trade.size * factor",
     "    notional = trade.entry_price * trade.size"),
    ("costs: slippage option ignored", A,
     "audit(trades, prices, universe, args.cost_bp + args.slippage_bp)",
     "audit(trades, prices, universe, args.cost_bp)"),
    ("costs: recomputed P&L keeps the costs the log charged", A,
     "        corrected += round(fixed - want, 2)", "        corrected += round(fixed - t.costs, 2)"),
    ("P&L: the pnl column's own arithmetic not checked", A,
     "        as_logged += round(naive - t.costs, 2)", "        as_logged += round(naive, 2)"),
    ("P&L: shorts given the long sign", A,
     '        return 1 if self.side == "long" else -1', "        return 1"),

    # ---- reading and the command line -----------------------------------------
    ("reading: price rows kept in file order", A,
     "        prices[path.stem] = dict(sorted(bars.items()))", "        prices[path.stem] = bars"),
    ("reading: a byte-order mark breaks the header", A,
     'encoding="utf-8-sig"', 'encoding="utf-8"'),
    ("reading: any side accepted", A,
     '        if side not in ("long", "short"):', "        if False:"),
    ("cli: exit 0 even when a check fails", A,
     "    return 0 if all(c.passed for c in checks) else 1", "    return 0"),
    ("cli: an unreadable input exits 1, like a failed check", A,
     '        print(f"error: {exc}", file=sys.stderr)\n        return 2',
     '        print(f"error: {exc}", file=sys.stderr)\n        return 1'),
    ("report: every offending row printed", A,
     "            shown = c.rows[:SHOW_ROWS]", "            shown = c.rows"),

    # ---- the fixtures ---------------------------------------------------------
    ("fixtures: the delisted ticker put back in the survivor universe", "tools/make_fixtures.py",
     'survivors = [tk for tk, bars in adjusted.items() if bars[-1]["date"] == days[-1]]',
     "survivors = list(adjusted)"),
    ("fixtures: no fills planted outside their bar", "tools/make_fixtures.py",
     "    outside = plant_outside_fills(planted, raw, set(look_ahead) | set(across_split))",
     "    outside = []"),
]


def run_tests(root):
    proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-q"], cwd=root,
                          capture_output=True, text=True)
    return proc.returncode == 0


def main():
    survivors = []
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "proj"
        shutil.copytree(SRC, base, ignore=shutil.ignore_patterns("__pycache__", "report.md"))
        assert run_tests(base), "baseline tests fail"
        print("baseline: tests pass")
        for name, rel, old, new in MUTATIONS:
            path = base / rel
            original = path.read_text(encoding="utf-8")
            count = original.count(old)
            if count != 1:
                print(f"SKIP (pattern found {count}x): {name}")
                survivors.append(name + " [pattern]")
                continue
            path.write_text(original.replace(old, new), encoding="utf-8")
            passed = run_tests(base)
            path.write_text(original, encoding="utf-8")
            print(f"{'SURVIVED' if passed else 'killed  '}: {name}")
            if passed:
                survivors.append(name)
    print(f"\n{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")
    if survivors:
        print("survivors:", survivors)
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
