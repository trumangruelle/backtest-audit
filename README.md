# Backtest audit

**The problem.** A backtest is a program, and programs have bugs. The bugs that matter most in a
backtest do not crash it: they make it look better (or worse) than the strategy really is, and
the equity curve gives no hint. The usual ones are:

- **look-ahead**: a trade is filled at a price from before the moment its signal could be known;
- **survivorship**: the list of stocks the strategy could trade is today's list, so the companies
  that went bust along the way are silently missing;
- **an unadjusted split**: a 2-for-1 split halves the price overnight, and the backtest books it
  as a 50% move;
- **impossible fills**: a fill at a price the stock never traded that day;
- **no costs**: no commission and no slippage.

**What this does.** It reads a backtest's trade log, the daily prices and the universe (the
tickers the backtest was allowed to trade on each date), runs one check per error, and writes a
report: PASS or FAIL per check, how many rows are affected and the first few of them, and the
P&L as reported next to the P&L with the split corrected and stated costs charged. It exits with
code 1 if any check fails, so it can sit in front of a results page or a CI job.

**Result on the included sample.** One toy strategy was run twice on the same synthetic prices:
once correctly, and once with all five errors planted. The planted run reports a profit of
**$16,721.26**. The same strategy run correctly **loses $1,533.63**. The audit, given only the
planted run's log, prices and universe, **fails all 5 checks**:

- **100 of 178 trades** enter on the very bar whose close fired their signal;
- **WILLOW**, which fell 82% and delisted, is missing from the universe on **all 380 days** it
  traded;
- **SPRUCE**'s 2-for-1 split on 2024-02-26 is found in the prices, and **1 short held across it**
  booked **$5,063.40**, of which **$4,939.20** is the split;
- **3 fills** are at prices outside their bar's low-high range;
- **all 178 trades** were charged nothing, against **$3,548.56** at 5 bp commission + 5 bp
  slippage per side.

With the split corrected and the costs charged, the reported $16,721.26 becomes **$8,233.50**,
and the report says why that is still not the real figure: the 100 look-ahead trades contributed
**$22,806.39**, and only a rerun can price them. On the correct run's files, all 5 checks pass
and the recomputed P&L equals the reported one to the cent.

**Evidence it works:** 60 automated tests, all offline, in under a second. Each check fails on
the planted sample and passes on the clean one, and the tests hold the audit to an answer key
the fixture generator writes: it must find exactly the planted trades, no more and no fewer. To
check the tests themselves, I broke the code on purpose in 32 specific ways (switched each check
off, let an entry on the signal bar through, forgot reverse splits, checked only the high of the
bar, charged costs on one side only, and so on), and the tests caught all 32.

Run on Python 3.14 (Windows 11). Written for Python 3.10 and newer; see
[Limitations](#limitations). Standard library only, so there is nothing else to install. MIT
license.

## Quick start

On macOS and Linux, type `python3` instead of `python`. Download this folder, then:

```
cd backtest-audit-demo
python audit.py --trades fixtures/trades_planted.csv --prices fixtures/prices --universe fixtures/universe_survivors.csv
python audit.py --trades fixtures/trades_clean.csv --prices fixtures/prices_adjusted --universe fixtures/universe.csv
python -m unittest
```

Each audit prints a summary and writes `report.md` (use `--report PATH` to put it elsewhere).
Nothing goes over the network. Costs default to 5 bp commission and 5 bp slippage per side; set
your own with `--cost-bp N --slippage-bp N`.

## What the report says on the planted sample

The first command above prints this (2026-11-14, Windows 11, Python 3.14) and exits with code 1:

```
Backtest audit of fixtures/trades_planted.csv: 178 trades, 6 price files, 2,610 universe rows
stated costs: 5 bp commission + 5 bp slippage per side

FAIL  look-ahead        100 of 178 trades enter on or before the bar whose close fired the signal
FAIL  survivorship      WILLOW delisted 2024-06-14: missing from the universe on 380 of the 380 days it traded (-82% over those days)
FAIL  unadjusted split  split-shaped gaps in the prices: SPRUCE 2024-02-26 (2-for-1 split); 1 trade(s) held across one
FAIL  fill outside bar  3 of 356 fills are outside the bar's low-high range or have no bar
FAIL  costs             178 of 178 trades charged less than the stated 10 bp per side (charged $0.00, stated rate gives $3,548.56)

P&L as reported:   $16,721.26
P&L recomputed:    $8,233.50  (splits corrected, stated costs charged)
not recomputed:    100 look-ahead trades reported $22,806.39; rerun the backtest to price them

5 of 5 checks failed
report: report.md
```

The full report, with the offending rows, is
[docs/sample-report-planted.md](docs/sample-report-planted.md). Its P&L section:

| | |
|---|---:|
| As reported (sum of the pnl column) | $16,721.26 |
| Splits corrected | -$4,939.20 |
| Costs at the stated rate, less costs charged | -$3,548.56 |
| **Recomputed** | **$8,233.50** |

The split row is one trade. Trade 98 sold 90 SPRUCE short at $111.14 and bought back at $54.88
after the split. The log calls that $5,063.40. But the 90 shares became 180, so buying them back
cost $9,878.40 against $10,002.60 received: a profit of $124.20.

## And on the clean sample

The second command prints this and exits with code 0:

```
Backtest audit of fixtures/trades_clean.csv: 194 trades, 6 price files, 2,990 universe rows
stated costs: 5 bp commission + 5 bp slippage per side

PASS  look-ahead        0 of 194 trades enter on or before the bar whose close fired the signal
PASS  survivorship      every ticker that delists is in the universe on the days it traded
PASS  unadjusted split  split-shaped gaps in the prices: none; 0 trade(s) held across one
PASS  fill outside bar  0 of 388 fills are outside the bar's low-high range or have no bar
PASS  costs             0 of 194 trades charged less than the stated 10 bp per side (charged $3,880.59, stated rate gives $3,880.59)

P&L as reported:   -$1,533.63
P&L recomputed:    -$1,533.63  (splits corrected, stated costs charged)

all 5 checks passed
report: report.md
```

The clean run trades 194 times rather than 178, because it can see WILLOW (24 of its trades)
and its entries come a day later. Full report: [docs/sample-report-clean.md](docs/sample-report-clean.md).

## How each check works

| Check | Fails when | Needs in the log |
|---|---|---|
| look-ahead | `entry_date` is on or before `signal_date`. A signal computed from a bar's close can be traded at the next bar at the earliest | the bar the signal was computed on |
| survivorship | a ticker whose price file ends before the others' (it delisted) is missing from the universe on a day it has a bar | nothing extra; it reads the prices and the universe |
| unadjusted split | a trade is held across an overnight gap (open over previous close) within 3% of 2, 3, 4, 5 or 10 to 1, either way round | entry and exit dates |
| fill outside bar | an entry or exit price is below the bar's low or above its high (half a cent of rounding allowed), or there is no bar that day | fill prices |
| costs | a trade's `costs` is below commission + slippage at the stated basis points, on both the entry and the exit value | a costs column |

The P&L is recomputed trade by trade: the price move times the size, with the exit leg scaled
by any split the trade was held across, less the stated costs.

The trade log is a CSV with one row per round trip:
`trade_id, ticker, side, size, signal_date, entry_date, entry_price, exit_date, exit_price, costs, pnl`.
Prices are one CSV per ticker with `date, open, high, low, close` (and `volume`, not used). The
universe is `date, ticker`. A real backtest's export rarely has exactly these columns; mapping it
is the first step of an audit.

## The sample

All of it is synthetic: invented tickers with six-letter names, so none can be a real symbol,
and prices from a seeded random walk. `python tools/make_fixtures.py` rebuilds every file in
`fixtures/`, and a test checks it reproduces them byte for byte.

- Six tickers, 522 weekdays from 2023-01-02 to 2024-12-31. ALDERS, BIRCHS, CEDARS and MAPLES are
  ordinary. WILLOW falls from $35.90 to $6.54 and delists on 2024-06-14: its file just ends.
  SPRUCE splits 2-for-1 on 2024-02-26.
- `prices/` is what a data vendor might deliver unadjusted: SPRUCE's prices halve overnight.
  `prices_adjusted/` is the same with SPRUCE's earlier prices halved to match.
- `universe.csv` lists every ticker on every day it had a bar (2,990 rows). `universe_survivors.csv`
  lists only the tickers still trading on the last day, on every day (2,610 rows).
- The toy strategy: at a close above the previous 20 closes go long, below all of them go short;
  hold 10 bars or to the ticker's last bar; about $10,000 a trade; one position per ticker.
- `trades_clean.csv`: fills at the next bar's open, the point-in-time universe, adjusted prices,
  5 bp + 5 bp a side. `trades_planted.csv`: long entries filled at the open of the signal bar,
  the survivor universe, unadjusted prices, three fills moved outside their bar, and no costs.
- `planted_errors.json` is the answer key: which trades carry which error.

## How it is tested

```
python -m unittest
```

On the machine used for this README (Windows 11, Python 3.14, 2026-11-14):

```
Ran 60 tests in 0.796s

OK
```

The tests check:

- every check on the planted sample finds exactly the trades the answer key lists, and every
  check on the clean sample finds nothing;
- the clean run's recomputed P&L equals its reported P&L to the cent, and the planted run's
  recomputed P&L is the reported figure less the split and the costs;
- each check at its edges, on small hand-made data: an entry on the signal bar fails and one on
  the next bar passes; a fill exactly at the high passes and one cent above fails; a 3-for-1 and
  a 1-for-2 reverse split are found, while ordinary gaps of about 8% either way and drops to
  1/1.8 or 1/2.2 of the close are not; a trade entered on the split day or exited the day before
  is not flagged, one exited on the split day is; costs one cent short fail; a survivor left out
  of the universe is not called survivorship;
- the command line: exit codes 1, 0 and 2 (a missing file, a missing column, a bad side, a value
  that is not a number), the report's row limit, and that `--cost-bp` and `--slippage-bp` add up;
- a price file in any row order, and a CSV saved with a byte-order mark;
- the fixtures: rebuilt byte for byte, and the raw and adjusted prices differ only for SPRUCE;
- the two outputs pasted above, the two sample reports in `docs/`, and the wrong-prices figure
  under Limitations, against a fresh run.

**Testing the tests.** `python tools/mutation_check.py` breaks the code in 32 specific ways,
one at a time, on a copy of the project, and runs the tests after each. For example, it switches
each check off, lets an entry on the signal bar through, measures WILLOW's fall from the wrong
day, drops 2-for-1 from the split ratios, forgets reverse splits, widens the split tolerance until
ordinary gaps look like splits, applies another ticker's split, checks only the bar's high,
skips exits, accepts a fill on a day with no bar, charges costs on the entry only, ignores the
slippage option, keeps the log's own costs in the recomputed P&L, gives shorts the long sign,
exits 0 when a check fails, and stops planting errors in the fixtures. The harness's last line:

```
32/32 mutations killed
```

The run took 38 seconds (2026-11-14, Windows 11, Python 3.14).

## Limitations

- These are data-level checks on a trade log, its prices and its universe. Passing all five does
  not show that a strategy has an edge, that it was not overfitted, or that it will work live.
  The clean sample passes everything and still loses money.
- The audit can fix what the log itself determines (a split, the costs). It cannot fix a
  look-ahead entry or a fill the bar never traded, because the log does not say what the right
  fill would have been, and it cannot add the trades a survivor-only universe never made. It
  reports those, with what they contributed, and the fix is a rerun.
- The look-ahead check needs the log to record the bar each signal was computed on. Many
  backtests do not write it out; adding it is usually one line. It catches entries timed before
  their information; it cannot see a signal that itself used future data (a feature built from
  a full-sample average, say). That needs the strategy code.
- The survivorship check knows a ticker existed only because its price file does. A universe and
  a price folder both built from today's survivors pass it. Ask the data vendor for delisted
  tickers.
- A split is recognised by the shape of the overnight gap. A real 50% crash at the open is
  indistinguishable from a 2-for-1 split, and an odd ratio (3-for-2) is not looked for. A
  corporate-actions file would settle both; this demo does not read one.
- Give it the prices the backtest actually used. The clean log against the unadjusted `prices/`
  fails 3 checks: 43 fills fall outside their bars, because SPRUCE's early fills are at adjusted
  prices.
- Fills are checked against the daily low and high only. An intraday strategy needs intraday
  bars, and a fill inside the day's range can still be unrealistic for a large order.
- Costs are a flat rate in basis points per side. Spreads, market impact, borrow fees for
  shorts and financing are not modelled.
- Daily bars only, one currency, and a trade log of complete round trips.
- Tested on Windows 11 with Python 3.14 only. The code uses nothing newer than Python 3.10, but
  it has not been run on 3.10.

## Layout

```
audit.py                 the five checks, the P&L recomputation, the report and the command line
fixtures/                the synthetic sample (see "The sample")
docs/                    the two sample reports
tests/test_checks.py     each check at its edges, on hand-made data
tests/test_fixtures.py   the planted and clean samples, the answer key, the command line, the generator
tests/test_docs.py       the outputs in this README and the sample reports
tools/make_fixtures.py   rebuilds fixtures/ from a fixed seed
tools/mutation_check.py  breaks the code 32 ways and checks the tests notice
```

## License

MIT. See [LICENSE](LICENSE).

Hire me on Upwork: [my Upwork profile](https://www.upwork.com/freelancers/~0102c9da1a58bd5441)

Fixed-price projects you can order there, each built around a check like the ones above:

- [Your vendor security questionnaire answered from your policies](https://www.upwork.com/services/product/development-it-your-vendor-security-questionnaire-answered-from-your-policies-2122208134162584093?ref=project_share)
- [A starter security policy pack written from how you work](https://www.upwork.com/services/product/development-it-a-starter-security-policy-pack-written-from-how-you-work-2122239934187225497?ref=project_share)
- [Your invoice PDFs into Excel, every total checked](https://www.upwork.com/services/product/development-it-invoice-data-extracted-into-a-spreadsheet-with-totals-checked-2102654749501770828?ref=project_share)
- [A Python script that pulls your API data into CSV or SQLite](https://www.upwork.com/services/product/development-it-a-python-script-that-pulls-your-api-data-into-csv-or-sqlite-2102648424948517964?ref=project_share)
- [An automated weekly sales report from your POS CSV exports](https://www.upwork.com/services/product/development-it-an-automated-weekly-sales-report-from-your-pos-csv-exports-2102545739255613093?ref=project_share)
