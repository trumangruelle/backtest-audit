# Backtest audit

Trades: `fixtures/trades_clean.csv`  
Prices: `fixtures/prices_adjusted`  
Universe: `fixtures/universe.csv`

## Summary

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
```

## PASS: look-ahead

0 of 194 trades enter on or before the bar whose close fired the signal.

## PASS: survivorship

Every ticker that delists is in the universe on the days it traded.

## PASS: unadjusted split

Split-shaped gaps in the prices: none; 0 trade(s) held across one.

## PASS: fill outside bar

0 of 388 fills are outside the bar's low-high range or have no bar.

## PASS: costs

0 of 194 trades charged less than the stated 10 bp per side (charged $3,880.59, stated rate gives $3,880.59).

## P&L

| | |
|---|---:|
| As reported (sum of the pnl column) | -$1,533.63 |
| Splits corrected | $0.00 |
| Costs at the stated rate, less costs charged | $0.00 |
| **Recomputed** | **-$1,533.63** |

These are data-level checks on the log, the prices and the universe. Passing them does not show that the strategy has an edge.
