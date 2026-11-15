"""The audit against the shipped fixtures: every planted error found, nothing on the clean log."""

import contextlib
import filecmp
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import audit

ROOT = Path(__file__).resolve().parent.parent
FX = ROOT / "fixtures"
KEY = json.loads((FX / "planted_errors.json").read_text(encoding="utf-8"))
PLANTED = ["--trades", str(FX / "trades_planted.csv"), "--prices", str(FX / "prices"),
           "--universe", str(FX / "universe_survivors.csv")]
CLEAN = ["--trades", str(FX / "trades_clean.csv"), "--prices", str(FX / "prices_adjusted"),
         "--universe", str(FX / "universe.csv")]


def run(fixture, bp=10):
    args = dict(zip(fixture[::2], fixture[1::2]))
    trades = audit.load_trades(args["--trades"])
    checks, pnl = audit.audit(trades, audit.load_prices(args["--prices"]),
                              audit.load_universe(args["--universe"]), bp)
    return {c.name: c for c in checks}, pnl


def cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        report = Path(tmp) / "report.md"
        code = audit.main(argv + ["--report", str(report)])
        text = report.read_text(encoding="utf-8") if report.exists() else ""
    return code, out.getvalue(), err.getvalue(), text


class PlantedFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks, cls.pnl = run(PLANTED)

    def ids(self, name):
        return [int(r[0]) for r in self.checks[name].rows]

    def test_look_ahead_finds_exactly_the_planted_trades(self):
        self.assertFalse(self.checks["look-ahead"].passed)
        self.assertEqual(self.ids("look-ahead"), KEY["look_ahead"])

    def test_survivorship_finds_the_delisted_ticker(self):
        c = self.checks["survivorship"]
        self.assertFalse(c.passed)
        self.assertEqual([(r[0], str(r[1]), r[3]) for r in c.rows],
                         [(KEY["survivorship"]["ticker"], KEY["survivorship"]["last_bar"],
                           KEY["survivorship"]["days_missing"])])

    def test_split_finds_the_gap_and_the_trade_held_across_it(self):
        c = self.checks["unadjusted split"]
        self.assertFalse(c.passed)
        self.assertEqual(self.ids("unadjusted split"), KEY["unadjusted_split"]["trades"])
        s = KEY["unadjusted_split"]
        self.assertIn(f"{s['ticker']} {s['date']} ({s['ratio']}-for-1 split)", c.summary)

    def test_fills_finds_exactly_the_planted_fills(self):
        self.assertFalse(self.checks["fill outside bar"].passed)
        self.assertEqual(self.ids("fill outside bar"), KEY["fill_outside_bar"])

    def test_costs_flags_every_trade(self):
        self.assertFalse(self.checks["costs"].passed)
        self.assertEqual(len(self.checks["costs"].rows), KEY["no_costs"])

    def test_recomputed_pnl_is_reported_less_split_and_costs(self):
        p = self.pnl
        self.assertAlmostEqual(p["arithmetic"], 0, places=6)
        self.assertAlmostEqual(p["reported"] + p["split"] + p["costs"], p["recomputed"], places=6)
        self.assertLess(p["split"], 0)
        self.assertLess(p["recomputed"], p["reported"])

    def test_each_planted_error_is_caught_by_its_own_check_only(self):
        # the planted fills and the split trade are shorts, so none of them is also a look-ahead entry
        la = set(self.ids("look-ahead"))
        self.assertFalse(la & set(self.ids("fill outside bar")))
        self.assertFalse(la & set(self.ids("unadjusted split")))


class CleanFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checks, cls.pnl = run(CLEAN)

    def test_every_check_passes(self):
        for name, c in self.checks.items():
            with self.subTest(name):
                self.assertTrue(c.passed, c.summary)
                self.assertEqual(c.rows, [])

    def test_recomputed_equals_reported_to_the_cent(self):
        self.assertEqual(round(self.pnl["reported"], 2), round(self.pnl["recomputed"], 2))
        self.assertEqual(round(self.pnl["arithmetic"], 2), 0)
        self.assertEqual(round(self.pnl["costs"], 2), 0)

    def test_clean_log_trades_the_delisted_ticker_while_it_was_listed(self):
        trades = [t for t in audit.load_trades(FX / "trades_clean.csv") if t.ticker == "WILLOW"]
        self.assertTrue(trades)
        self.assertLessEqual(str(max(t.exit_date for t in trades)), KEY["survivorship"]["last_bar"])

    def test_a_higher_stated_cost_fails_the_clean_log(self):
        checks, _ = run(CLEAN, bp=12)
        self.assertFalse(checks["costs"].passed)


class CommandLineTests(unittest.TestCase):
    def test_planted_exits_1_and_reports_five_failures(self):
        code, out, _, report = cli(PLANTED)
        self.assertEqual(code, 1)
        self.assertEqual(out.count("FAIL  "), 5)
        self.assertIn("5 of 5 checks failed", out)
        self.assertEqual(report.count("## FAIL: "), 5)

    def test_clean_exits_0(self):
        code, out, _, report = cli(CLEAN)
        self.assertEqual(code, 0)
        self.assertIn("all 5 checks passed", out)
        self.assertEqual(report.count("## PASS: "), 5)

    def test_report_shows_at_most_five_rows_per_check(self):
        _, _, _, report = cli(PLANTED)
        self.assertIn("First 5 of 100:", report)
        self.assertIn("All 3:", report)

    def test_cost_options_are_added(self):
        code, out, _, _ = cli(CLEAN + ["--cost-bp", "6", "--slippage-bp", "5"])
        self.assertEqual(code, 1)
        self.assertIn("FAIL  costs", out)

    def test_missing_file_exits_2(self):
        code, _, err, _ = cli(["--trades", "nope.csv", "--prices", str(FX / "prices"),
                               "--universe", str(FX / "universe.csv")])
        self.assertEqual(code, 2)
        self.assertIn("file not found", err)

    def test_bad_side_and_bad_number_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = (FX / "trades_clean.csv").read_text(encoding="utf-8").splitlines()
            for bad, needle in ((lines[1].replace(",long,", ",buy,").replace(",short,", ",buy,"), "side must be"),
                                (lines[1].replace(lines[1].split(",")[6], "abc", 1), "is not a number")):
                path = Path(tmp) / "t.csv"
                path.write_text("\n".join([lines[0], bad]) + "\n", encoding="utf-8")
                code, _, err, _ = cli(["--trades", str(path)] + CLEAN[2:])
                self.assertEqual(code, 2, bad)
                self.assertIn(needle, err)

    def test_missing_column_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            path.write_text("trade_id,ticker\n1,ALDERS\n", encoding="utf-8")
            code, _, err, _ = cli(["--trades", str(path)] + CLEAN[2:])
            self.assertEqual(code, 2)
            self.assertIn("missing column(s) side", err)


class ReadingTests(unittest.TestCase):
    def test_price_file_in_any_order_is_read_oldest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            lines = (FX / "prices" / "SPRUCE.csv").read_text(encoding="utf-8").splitlines()
            reversed_rows = [lines[0]] + lines[:0:-1]
            (Path(tmp) / "SPRUCE.csv").write_text("\n".join(reversed_rows) + "\n", encoding="utf-8")
            splits = audit.find_splits(audit.load_prices(tmp))
        self.assertEqual([(s.ticker, str(s.date), s.ratio) for s in splits],
                         [("SPRUCE", KEY["unadjusted_split"]["date"], 2)])

    def test_a_spreadsheet_saved_csv_with_a_byte_order_mark_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.csv"
            path.write_bytes(b"\xef\xbb\xbf" +(FX / "trades_clean.csv").read_bytes())
            self.assertEqual(len(audit.load_trades(path)), KEY["trades"]["clean"])


class FixtureGeneratorTests(unittest.TestCase):
    def test_generator_reproduces_the_fixtures_byte_for_byte(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("make_fixtures", ROOT / "tools" / "make_fixtures.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            mod.main(tmp)
            cmp = filecmp.dircmp(tmp, FX)
            self.assertEqual((cmp.left_only, cmp.right_only, cmp.diff_files), ([], [], []))
            for sub in ("prices", "prices_adjusted"):
                match, mismatch, errors = filecmp.cmpfiles(Path(tmp) / sub, FX / sub,
                                                           [p.name for p in (FX / sub).glob("*.csv")], shallow=False)
                self.assertEqual((len(match), mismatch, errors), (6, [], []))
            for name in ("trades_planted.csv", "trades_clean.csv", "universe.csv",
                         "universe_survivors.csv", "planted_errors.json"):
                self.assertTrue(filecmp.cmp(Path(tmp) / name, FX / name, shallow=False), name)

    def test_raw_and_adjusted_differ_only_for_the_split_ticker(self):
        for path in (FX / "prices").glob("*.csv"):
            same = path.read_bytes() == (FX / "prices_adjusted" / path.name).read_bytes()
            self.assertEqual(same, path.stem != "SPRUCE", path.name)


if __name__ == "__main__":
    unittest.main()
