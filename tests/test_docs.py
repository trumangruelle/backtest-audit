"""The outputs pasted in README.md and the sample reports in docs/ match a fresh run."""

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path

import audit

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
COMMANDS = re.findall(r"^python audit\.py (.+)$", README, flags=re.M)


def run_from_root(args):
    """Run audit.main as the README does (relative paths, from the project folder)."""
    out, cwd = io.StringIO(), os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.md"
        try:
            os.chdir(ROOT)
            with contextlib.redirect_stdout(out):
                code = audit.main(args + ["--report", str(report)])
        finally:
            os.chdir(cwd)
        text = report.read_text(encoding="utf-8")
    printed = out.getvalue().replace(f"report: {report}", "report: report.md")
    return code, printed, text


def pasted_block(first_line):
    match = re.search(r"```\n(" + re.escape(first_line) + r".*?)```", README, flags=re.S)
    assert match, first_line
    return match.group(1)


class ReadmeTests(unittest.TestCase):
    def test_quick_start_has_the_two_audit_commands(self):
        self.assertEqual(len(COMMANDS), 2)

    def test_planted_output_matches_the_readme(self):
        code, printed, _ = run_from_root(COMMANDS[0].split())
        self.assertEqual(code, 1)
        self.assertEqual(printed, pasted_block("Backtest audit of fixtures/trades_planted.csv"))

    def test_clean_output_matches_the_readme(self):
        code, printed, _ = run_from_root(COMMANDS[1].split())
        self.assertEqual(code, 0)
        self.assertEqual(printed, pasted_block("Backtest audit of fixtures/trades_clean.csv"))

    def test_sample_reports_match_a_fresh_run(self):
        for args, name in ((COMMANDS[0], "sample-report-planted.md"), (COMMANDS[1], "sample-report-clean.md")):
            _, _, report = run_from_root(args.split())
            self.assertEqual(report, (ROOT / "docs" / name).read_text(encoding="utf-8"), name)

    def test_clean_log_against_the_wrong_prices_as_limitations_says(self):
        code, printed, _ = run_from_root(["--trades", "fixtures/trades_clean.csv", "--prices", "fixtures/prices",
                                          "--universe", "fixtures/universe.csv"])
        self.assertEqual(code, 1)
        self.assertIn("3 of 5 checks failed", printed)
        self.assertIn("43 of 388 fills are outside", printed)
        self.assertIn("43 fills fall outside their bars", README)


if __name__ == "__main__":
    unittest.main()
