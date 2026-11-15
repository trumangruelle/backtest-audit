# Backtest audit

Trades: `fixtures/trades_planted.csv`  
Prices: `fixtures/prices`  
Universe: `fixtures/universe_survivors.csv`

## Summary

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
```

## FAIL: look-ahead

100 of 178 trades enter on or before the bar whose close fired the signal.

First 5 of 100:

| trade | ticker | side | signal bar | entry |
|---|---|---|---|---|
| 1 | SPRUCE | long | 2023-01-30 | 2023-01-30 |
| 2 | ALDERS | long | 2023-02-01 | 2023-02-01 |
| 4 | CEDARS | long | 2023-02-13 | 2023-02-13 |
| 5 | SPRUCE | long | 2023-02-13 | 2023-02-13 |
| 7 | MAPLES | long | 2023-02-24 | 2023-02-24 |

## FAIL: survivorship

WILLOW delisted 2024-06-14: missing from the universe on 380 of the 380 days it traded (-82% over those days).

All 1:

| ticker | last bar | days traded | days missing | price change while missing |
|---|---|---|---|---|
| WILLOW | 2024-06-14 | 380 | 380 | -82% |

## FAIL: unadjusted split

Split-shaped gaps in the prices: SPRUCE 2024-02-26 (2-for-1 split); 1 trade(s) held across one.

All 1:

| trade | ticker | side | entry | entry price | exit | exit price | reported pnl |
|---|---|---|---|---|---|---|---|
| 98 | SPRUCE | short | 2024-02-22 | 111.14 | 2024-03-06 | 54.88 | $5,063.40 |

## FAIL: fill outside bar

3 of 356 fills are outside the bar's low-high range or have no bar.

All 3:

| trade | ticker | leg | date | price | what the bar allows |
|---|---|---|---|---|---|
| 44 | CEDARS | entry | 2023-07-05 | 19.36 | bar 18.60 to 19.13 |
| 78 | SPRUCE | entry | 2023-12-05 | 108.47 | bar 105.36 to 107.18 |
| 125 | SPRUCE | exit | 2024-06-10 | 63.10 | bar 63.87 to 64.43 |

## FAIL: costs

178 of 178 trades charged less than the stated 10 bp per side (charged $0.00, stated rate gives $3,548.56).

First 5 of 178:

| trade | ticker | charged | at the stated rate |
|---|---|---|---|
| 1 | SPRUCE | $0.00 | $19.90 |
| 2 | ALDERS | $0.00 | $19.51 |
| 3 | MAPLES | $0.00 | $19.94 |
| 4 | CEDARS | $0.00 | $18.94 |
| 5 | SPRUCE | $0.00 | $19.72 |

## P&L

| | |
|---|---:|
| As reported (sum of the pnl column) | $16,721.26 |
| Splits corrected | -$4,939.20 |
| Costs at the stated rate, less costs charged | -$3,548.56 |
| **Recomputed** | **$8,233.50** |

- 100 trades with look-ahead entries reported $22,806.39 between them. They stay in the recomputed figure, because the log cannot say what the right fill was.
- 3 trades with fills the bar never traded reported -$684.70 between them. They stay in the recomputed figure, because the log cannot say what the right fill was.
- Trades the backtest never saw (the tickers missing from its universe) cannot be recomputed from the log. Rerun it on the point-in-time universe.

These are data-level checks on the log, the prices and the universe. Passing them does not show that the strategy has an edge.
