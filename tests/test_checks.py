"""Each check on small hand-made data, at its edges. No fixtures needed."""

import unittest
from datetime import date, timedelta

import audit
from audit import Bar, Split, Trade

D = date(2024, 3, 4)          # a Monday


def day(n):
    return D + timedelta(days=n)


def trade(**kw):
    base = dict(trade_id="1", ticker="TESTAA", side="long", size=100.0, signal_date=day(0),
                entry_date=day(1), entry_price=10.0, exit_date=day(3), exit_price=11.0, costs=0.0, pnl=100.0)
    base.update(kw)
    return Trade(**base)


def flat_bars(n, price=10.0, start=0):
    return {day(start + i): Bar(price, price + 0.5, price - 0.5, price) for i in range(n)}


class LookAheadTests(unittest.TestCase):
    def test_entry_the_bar_after_the_signal_passes(self):
        self.assertTrue(audit.check_look_ahead([trade()]).passed)

    def test_entry_on_the_signal_bar_fails(self):
        c = audit.check_look_ahead([trade(entry_date=day(0))])
        self.assertFalse(c.passed)
        self.assertEqual([r[0] for r in c.rows], ["1"])

    def test_entry_before_the_signal_bar_fails(self):
        self.assertFalse(audit.check_look_ahead([trade(entry_date=day(-1))]).passed)

    def test_counts_only_the_offending_trades(self):
        c = audit.check_look_ahead([trade(trade_id="1"), trade(trade_id="2", entry_date=day(0)), trade(trade_id="3")])
        self.assertEqual([r[0] for r in c.rows], ["2"])
        self.assertIn("1 of 3 trades", c.summary)


class SurvivorshipTests(unittest.TestCase):
    def setUp(self):
        self.prices = {"KEEPER": flat_bars(10), "GONEXX": flat_bars(6)}
        self.full = {(d, tk) for tk, bars in self.prices.items() for d in bars}

    def test_point_in_time_universe_passes(self):
        self.assertTrue(audit.check_survivorship(self.prices, self.full).passed)

    def test_delisted_ticker_missing_on_one_day_it_traded_fails(self):
        c = audit.check_survivorship(self.prices, self.full - {(day(2), "GONEXX")})
        self.assertFalse(c.passed)
        self.assertEqual(c.rows[0][:4], ("GONEXX", day(5), 6, 1))

    def test_delisted_ticker_missing_everywhere_fails(self):
        c = audit.check_survivorship(self.prices, {k for k in self.full if k[1] != "GONEXX"})
        self.assertEqual(c.rows[0][3], 6)

    def test_survivor_left_out_is_not_survivorship(self):
        self.assertTrue(audit.check_survivorship(self.prices, {k for k in self.full if k[1] != "KEEPER"}).passed)

    def test_price_change_is_over_the_missing_days(self):
        self.prices["GONEXX"][day(5)] = Bar(5.0, 5.0, 5.0, 5.0)
        c = audit.check_survivorship(self.prices, {k for k in self.full if k[1] != "GONEXX"})
        self.assertEqual(c.rows[0][4], "-50%")


    def test_price_change_starts_at_the_first_missing_day(self):
        self.prices["GONEXX"][day(2)] = Bar(20.0, 20.0, 20.0, 20.0)
        self.prices["GONEXX"][day(5)] = Bar(5.0, 5.0, 5.0, 5.0)
        universe = self.full - {(day(n), "GONEXX") for n in (2, 3, 4, 5)}
        self.assertEqual(audit.check_survivorship(self.prices, universe).rows[0][4], "-75%")


class SplitTests(unittest.TestCase):
    def bars_with_gap(self, ratio):
        bars = flat_bars(3, 100.0)
        p = 100.0 / ratio
        bars.update({day(3 + i): Bar(p, p + 0.5, p - 0.5, p) for i in range(3)})
        return {"TESTAA": bars}

    def test_two_for_one_found(self):
        self.assertEqual(audit.find_splits(self.bars_with_gap(2)), [Split("TESTAA", day(3), 2)])

    def test_three_for_one_and_reverse_split_found(self):
        self.assertEqual(audit.find_splits(self.bars_with_gap(3))[0].ratio, 3)
        self.assertEqual(audit.find_splits(self.bars_with_gap(0.5))[0].ratio, 0.5)

    def test_ordinary_gaps_not_flagged(self):
        for ratio in (1.0, 1.08, 1 / 0.92, 1.8, 2.2):
            self.assertEqual(audit.find_splits(self.bars_with_gap(ratio)), [], ratio)

    def test_held_across_the_split_fails(self):
        splits = [Split("TESTAA", day(3), 2)]
        c = audit.check_split([trade(entry_date=day(1), exit_date=day(4))], splits)
        self.assertFalse(c.passed)

    def test_exit_on_the_split_bar_counts_as_held_across(self):
        self.assertEqual(audit.split_factor(trade(entry_date=day(1), exit_date=day(3)), [Split("TESTAA", day(3), 2)]), 2)

    def test_trades_on_one_side_of_the_split_pass(self):
        splits = [Split("TESTAA", day(3), 2)]
        before = trade(entry_date=day(0), exit_date=day(2))
        after = trade(entry_date=day(3), exit_date=day(5))
        other = trade(ticker="OTHERX", entry_date=day(1), exit_date=day(4))
        self.assertTrue(audit.check_split([before, after, other], splits).passed)

    def test_recompute_removes_the_fake_short_profit(self):
        short = trade(side="short", entry_price=100.0, exit_price=50.0, exit_date=day(4), pnl=5000.0)
        pnl = audit.recompute([short], [Split("TESTAA", day(3), 2)], 0)
        self.assertEqual(pnl["reported"], 5000.0)
        self.assertEqual(pnl["recomputed"], 0.0)       # 100 shares at 100 became 200 at 50
        self.assertEqual(pnl["split"], -5000.0)


class FillTests(unittest.TestCase):
    prices = {"TESTAA": flat_bars(5)}          # every bar 9.50 to 10.50

    def test_fills_at_the_high_and_low_pass(self):
        self.assertTrue(audit.check_fills([trade(entry_price=10.50, exit_price=9.50)], self.prices).passed)

    def test_one_cent_above_the_high_fails(self):
        c = audit.check_fills([trade(entry_price=10.51, exit_price=10.0)], self.prices)
        self.assertEqual([(r[0], r[2]) for r in c.rows], [("1", "entry")])

    def test_one_cent_below_the_low_on_exit_fails(self):
        c = audit.check_fills([trade(exit_price=9.49)], self.prices)
        self.assertEqual([r[2] for r in c.rows], ["exit"])

    def test_no_bar_that_day_fails(self):
        c = audit.check_fills([trade(exit_price=10.0, exit_date=day(30))], self.prices)
        self.assertEqual([r[5] for r in c.rows], ["no bar that day"])

    def test_unknown_ticker_fails(self):
        self.assertEqual(len(audit.check_fills([trade(ticker="NOPRCE")], self.prices).rows), 2)


class CostTests(unittest.TestCase):
    def test_expected_costs_both_sides(self):
        # (10 x 100 + 11 x 100) x 10 bp = 2.10
        self.assertEqual(audit.expected_costs(trade(), 10), 2.10)

    def test_charged_at_the_stated_rate_passes(self):
        self.assertTrue(audit.check_costs([trade(costs=2.10)], 10, []).passed)

    def test_one_cent_short_fails(self):
        self.assertFalse(audit.check_costs([trade(costs=2.09)], 10, []).passed)

    def test_zero_costs_fail_at_a_positive_rate(self):
        c = audit.check_costs([trade(), trade(trade_id="2")], 10, [])
        self.assertIn("2 of 2 trades", c.summary)

    def test_zero_stated_rate_passes(self):
        self.assertTrue(audit.check_costs([trade()], 0, []).passed)

    def test_exit_notional_follows_a_split(self):
        # 100 shares at 10 became 200 shares at 5.50: (1000 + 1100) x 10 bp
        self.assertEqual(audit.expected_costs(trade(exit_price=5.5), 10, 2.0), 2.10)


class RecomputeTests(unittest.TestCase):
    def test_reported_equals_recomputed_when_nothing_is_wrong(self):
        t = trade(costs=2.10, pnl=97.90)
        pnl = audit.recompute([t], [], 10)
        self.assertEqual((pnl["reported"], pnl["recomputed"]), (97.90, 97.90))

    def test_costs_are_taken_off(self):
        pnl = audit.recompute([trade()], [], 10)
        self.assertEqual(round(pnl["recomputed"], 2), 97.90)
        self.assertEqual(round(pnl["costs"], 2), -2.10)

    def test_short_sign(self):
        pnl = audit.recompute([trade(side="short", pnl=-100.0)], [], 0)
        self.assertEqual(pnl["recomputed"], -100.0)

    def test_a_pnl_column_that_disagrees_with_its_prices_is_measured(self):
        pnl = audit.recompute([trade(pnl=150.0)], [], 0)
        self.assertEqual(round(pnl["arithmetic"], 2), 50.0)

    def test_money_format(self):
        self.assertEqual(audit._money(-1234.5), "-$1,234.50")
        self.assertEqual(audit._money(-0.001), "$0.00")


if __name__ == "__main__":
    unittest.main()
