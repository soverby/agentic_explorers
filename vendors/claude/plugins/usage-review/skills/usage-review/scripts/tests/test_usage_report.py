"""Tests for usage_report.py. Run: python -m unittest discover <scripts>/tests"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(HERE))

import fixtures as fx  # noqa: E402
import usage_report as ur  # noqa: E402

UTC = dt.timezone.utc
TODAY = dt.date(2026, 9, 28)
OUTPUTS = (
    "weekly_by_model_effort.csv",
    "weekly_by_agent.csv",
    "diagnostics.json",
    "inventory.json",
    "findings.json",
    "report.md",
    "manifest.json",
)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def run_main(self, *argv, env=None, out="runs/out"):
        args = list(argv)
        if out is not None and "--out" not in args:
            args += ["--out", str(self.tmp / out)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = ur.main(args, home=self.home, env=env or {}, today=TODAY, tz=UTC)
        self.stderr = err.getvalue()
        return rc

    def out(self, name, out="runs/out"):
        return self.tmp / out / name

    def load(self, name, out="runs/out"):
        return json.loads(self.out(name, out).read_text(encoding="utf-8"))

    def csv_rows(self, name, out="runs/out"):
        with self.out(name, out).open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))


class FixtureRun(Base):
    """Profile A at ~/.claude, profile B by --profile."""

    def setUp(self):
        super().setUp()
        fx.build_profile_a(self.home / ".claude")
        self.pb = fx.build_profile_b(self.tmp / "profile-b")
        self.rc = self.run_main("--profile", f"b={self.pb}")
        self.assertEqual(self.rc, 0, self.stderr)

    def test_totals_and_dedup(self):
        rows = self.csv_rows("weekly_by_model_effort.csv")
        total = rows[-1]
        self.assertEqual((total["week"], total["model"]), ("ALL", "TOTAL"))
        got = {
            k: int(total[k]) for k in ("sessions", "api_calls", *ur.CSV_TOKEN_COLUMNS)
        }
        self.assertEqual(
            got,
            dict(
                sessions=2,
                api_calls=10,
                input_uncached=42,
                cache_write_5m=83600,
                cache_write_1h=61000,
                cache_read=60100,
                output=294,
                thinking=20,
            ),
        )
        rec = self.load("manifest.json")["reconciliation"]
        self.assertEqual(
            (rec["raw_lines"], rec["dedup_calls"], rec["duplicate_lines"]), (11, 10, 1)
        )
        self.assertEqual(
            rec["removed_by_dedup"],
            dict(inp=10, w5=0, w1=30000, read=0, out=100, think=20),
        )

    def test_effort_fallback_and_rows(self):
        keys = {
            (r["model"], r["effort"])
            for r in self.csv_rows("weekly_by_model_effort.csv")
        }
        for k in (
            ("claude-opus-5", "high"),
            ("claude-opus-5", "medium"),
            ("claude-sonnet-5", "medium"),
            ("claude-sonnet-5", "low"),
            ("claude-haiku-4-5-20251001", "unknown"),
        ):
            self.assertIn(k, keys)
        self.assertEqual(
            self.load("manifest.json")["missing_fields"],
            {"cache_creation 5m/1h split (counted as 5m)": 1, "effort": 1},
        )

    def test_decimal_costs_round_half_up(self):
        rows = {
            (r["model"], r["effort"]): r
            for r in self.csv_rows("weekly_by_model_effort.csv")
        }
        # (20 + 61000*2 + 30100*0.1) * 5 / 1e6 = 0.62515 -> 0.63 (half up, not float/banker's)
        self.assertEqual(
            rows[("claude-opus-5", "high")]["cost_weighted_input_usd"], "0.63"
        )
        self.assertEqual(
            rows[("claude-sonnet-5", "medium")]["cost_weighted_input_usd"], "0.05"
        )
        self.assertEqual(
            rows[("claude-sonnet-5", "medium")]["cost_weighted_output_usd"], "0.00"
        )

    def test_unknown_model_listed_not_priced(self):
        m = self.load("manifest.json")
        self.assertEqual(m["unknown_models"], {"claude-unknown-9": 1})
        rows = self.csv_rows("weekly_by_model_effort.csv")
        unknown = [r for r in rows if r["model"] == "claude-unknown-9"][0]
        self.assertEqual(
            (unknown["cost_weighted_input_usd"], unknown["cost_weighted_output_usd"]),
            ("", ""),
        )
        self.assertEqual(
            rows[-1]["cost_weighted_input_usd"], ""
        )  # TOTAL includes an unpriced call
        ids = [f["id"] for f in self.load("findings.json")["findings"]]
        self.assertIn("unpriced_models", ids)

    def test_week_partial_and_skips(self):
        rows = self.csv_rows("weekly_by_model_effort.csv")
        self.assertTrue(all(r["week"] in ("2026-W38 (partial)", "ALL") for r in rows))
        m = self.load("manifest.json")
        self.assertEqual(m["lines_skipped"], {"<synthetic> model": 1, "bad json": 1})
        self.assertIn("2026-W38", m["date_range"]["partial_weeks"])

    def test_agent_split(self):
        rows = {
            (r["split"], r["agent_type"]): r
            for r in self.csv_rows("weekly_by_agent.csv")
        }
        self.assertEqual(
            set(rows), {("main", ""), ("subagent", "builder"), ("subagent", "reviewer")}
        )
        self.assertEqual(int(rows[("subagent", "reviewer")]["api_calls"]), 3)
        self.assertEqual(int(rows[("subagent", "reviewer")]["cache_write_5m"]), 63000)

    def test_verdicts_exact_line_only(self):
        d = self.load("diagnostics.json")
        v = d["reviewer_verdicts"]
        self.assertEqual((v["approve"], v["changes"], v["unclear"]), (1, 1, 1))
        by_run = {
            r["run"]: r["verdict"]
            for r in d["subagent_runs"]
            if r["agent"] == "reviewer"
        }
        self.assertEqual(
            by_run,
            {"agent-r1": "approve", "agent-r2": "unclear", "agent-r3": "changes"},
        )

    def test_verdict_parser(self):
        self.assertEqual(ur.verdict_of("x\nVERDICT: APPROVE"), "approve")
        self.assertEqual(ur.verdict_of("VERDICT: CHANGES\n"), "changes")
        self.assertEqual(ur.verdict_of("VERDICT: APPROVE\nVERDICT: CHANGES"), "unclear")
        self.assertEqual(ur.verdict_of("**VERDICT: APPROVE**"), "unclear")
        self.assertEqual(ur.verdict_of("verdict: approve"), "unclear")
        self.assertEqual(ur.verdict_of("Approved. LGTM."), "unclear")

    def test_diagnostics(self):
        d = self.load("diagnostics.json")
        self.assertEqual(d["repeated_reads"]["pairs"], 1)
        self.assertEqual(d["repeated_reads"]["extra_reads"], 2)
        self.assertEqual(
            d["repeated_reads"]["worst"][0]["file_path"], "/work/demo/a.py"
        )
        self.assertEqual(
            d["subagent_rereads"],
            dict(subagent_reads=1, already_read_by_parent=1, already_read_chars=200),
        )
        self.assertEqual(
            d["large_tool_results"]["by_tool"],
            {"Read": {"count": 1, "chars_over": 3000}},
        )
        self.assertEqual(d["largest_tool_results"][0]["tool"], "Read")
        self.assertEqual(d["largest_tool_results"][0]["chars"], len(fx.MARKER) * 1000)
        self.assertEqual(d["cache_rewrites"]["main"][">1h"]["events"], 1)
        self.assertEqual(d["cache_rewrites"]["main"][">1h"]["cache_write_1h"], 31000)
        self.assertEqual(d["cache_rewrites"]["main"]["5m-1h"]["events"], 0)
        self.assertEqual(d["limit_hits"]["total"], 1)
        self.assertEqual(d["compactions"]["by_trigger"], {"auto": 1})
        self.assertEqual(d["compactions"]["events"][0]["pre_tokens"], 31000)
        self.assertEqual(d["start_context"]["median"], 15258)  # median(30010, 507)
        br = d["builder_reviewer"]["sessions"][0]
        self.assertEqual((br["builder_runs"], br["reviewer_runs"]), (1, 3))

    def test_findings(self):
        f = self.load("findings.json")
        scores = [x["score"] for x in f["findings"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        by_id = {x["id"]: x for x in f["findings"]}
        rr = by_id["reviewer_rounds_over_threshold"]
        # 3rd reviewer run = 2 + 21000 + 20 tokens; range 3 days = 3/7 weeks
        self.assertEqual(rr["est_weekly_tokens"], 49051)
        self.assertEqual((rr["confidence"], rr["score"]), ("measured", 49051))
        lt = by_id["large_tool_results:Read"]
        self.assertEqual(lt["confidence"], "inferred")
        self.assertEqual(lt["score"], (lt["est_weekly_tokens"] + 1) // 2)
        for x in f["findings"]:
            for k in (
                "id",
                "rule",
                "measured",
                "threshold",
                "evidence",
                "est_weekly_tokens",
                "confidence",
                "score",
            ):
                self.assertIn(k, x)

    def test_inventory(self):
        inv = self.load("inventory.json")["profiles"]
        self.assertEqual(set(inv), {"claude", "b"})
        a = inv["claude"]
        self.assertEqual(
            a["settings"]["keys"],
            {
                "model": "opus",
                "autoCompactWindow": 500000,
                "subagentPromptCacheTtl": "1h",
            },
        )
        self.assertEqual(a["settings"]["env"], {"MAX_THINKING_TOKENS": "8000"})
        self.assertEqual(a["settings"]["hooks"], {"PostToolUse": 1})
        ag = a["agents"][0]
        self.assertEqual(
            (ag["name"], ag["model"], ag["effort"], ag["tools"]),
            ("reviewer", "opus", "medium", ["Read", "Grep"]),
        )
        self.assertEqual(a["memory_files"]["count"], 1)
        self.assertEqual(a["skills"][0]["name"], "demo")
        self.assertGreater(a["claude_md"]["bytes"], 0)

    def test_manifest_hashes_and_invariants(self):
        m = self.load("manifest.json")
        self.assertEqual(set(m["outputs"]), set(OUTPUTS) - {"manifest.json"})
        for name, h in m["outputs"].items():
            self.assertEqual(hashlib.sha256(self.out(name).read_bytes()).hexdigest(), h)
        self.assertEqual(set(m["invariants"].values()), {"pass"})
        self.assertEqual(len(m["invariants"]), 4)
        self.assertEqual(m["files_scanned"], 6)
        self.assertEqual(
            {p["name"]: p["files"] for p in m["profiles"]}, {"claude": 5, "b": 1}
        )

    def test_privacy_marker_not_in_outputs(self):
        for name in OUTPUTS:
            text = self.out(name).read_text(encoding="utf-8")
            self.assertNotIn(fx.MARKER, text, name)
            self.assertNotIn("ZQX-PRIVACY", text, name)

    def test_deterministic(self):
        rc = self.run_main("--profile", f"b={self.pb}", out="runs2/out")
        self.assertEqual(rc, 0, self.stderr)
        for name in OUTPUTS[:-1]:
            self.assertEqual(
                self.out(name).read_bytes(),
                self.out(name, "runs2/out").read_bytes(),
                name,
            )

    def test_date_filter(self):
        rc = self.run_main(
            "--profile",
            f"b={self.pb}",
            "--start",
            "2026-09-15",
            "--end",
            "2026-09-15",
            out="f/out",
        )
        self.assertEqual(rc, 0, self.stderr)
        total = self.csv_rows("weekly_by_model_effort.csv", "f/out")[-1]
        self.assertEqual(int(total["api_calls"]), 1)


class Errors(Base):
    def test_no_files_exit_2(self):
        self.assertEqual(self.run_main(), 2)
        self.assertIn("no transcript files", self.stderr)
        self.assertFalse(self.out("manifest.json").exists())

    def test_schema_drift_exit_2(self):
        fx.build_sessions(self.home / ".claude", 1, 9, 100)
        fx.write_jsonl(
            self.home / ".claude" / "projects" / fx.PROJECT / "drift.jsonl",
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-09-20T12:00:00Z",
                    "message": {"id": "x", "model": "m"},
                }
            ],
        )
        self.assertEqual(self.run_main(), 2)  # 1 of 10 = 10% > 5%
        self.assertIn("schema drift", self.stderr)

    def test_schema_drift_at_limit_passes(self):
        fx.build_sessions(self.home / ".claude", 1, 19, 100)
        fx.write_jsonl(
            self.home / ".claude" / "projects" / fx.PROJECT / "drift.jsonl",
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-09-20T12:00:00Z",
                    "message": {"id": "x", "model": "m"},
                }
            ],
        )
        self.assertEqual(self.run_main(), 0, self.stderr)  # 1 of 20 = 5%, not more

    def test_invariant_failure_exit_3_writes_nothing(self):
        fx.build_profile_a(self.home / ".claude")
        orig = ur.reconciliation

        def broken(*a, **k):
            r = orig(*a, **k)
            r["raw_sums"]["out"] = r["dedup_sums"]["out"] - 1
            return r

        ur.reconciliation = broken
        try:
            self.assertEqual(self.run_main(), 3)
        finally:
            ur.reconciliation = orig
        self.assertIn("raw_sum_ge_dedup_sum", self.stderr)
        self.assertFalse(self.out("manifest.json").exists())
        self.assertFalse(self.out("weekly_by_model_effort.csv").exists())

    def test_subtotal_invariant(self):
        fx.build_profile_a(self.home / ".claude")
        orig = ur.agg

        def broken(rs, prices):
            a = orig(rs, prices)
            if len(rs) == 1:
                a["out"] += 1
            return a

        ur.agg = broken
        try:
            self.assertEqual(self.run_main(), 3)
        finally:
            ur.agg = orig
        self.assertIn("weekly_subtotals_sum_to_total", self.stderr)

    def test_bad_args(self):
        fx.build_profile_a(self.home / ".claude")
        self.assertEqual(self.run_main("--start", "2026-9-1"), 2)
        self.assertEqual(
            self.run_main("--start", "2026-09-20", "--end", "2026-09-10"), 2
        )
        self.assertEqual(self.run_main("--profile", "nopath"), 2)
        self.assertEqual(self.run_main("--profile", f"x={self.tmp / 'missing'}"), 2)
        self.assertEqual(self.run_main("--end", "2020-01-01"), 2)  # no usage in range

    def test_bad_prices_exit_2(self):
        fx.build_profile_a(self.home / ".claude")
        bad = self.tmp / "prices.json"
        bad.write_text('{"checked": "2026-01-01", "models": {}}', encoding="utf-8")
        self.assertEqual(self.run_main("--prices", str(bad)), 2)


class Profiles(Base):
    def test_default_and_config_dir(self):
        ccd = self.tmp / ".claude-alt"
        ccd.mkdir()
        got = ur.resolve_profiles([], self.home, {"CLAUDE_CONFIG_DIR": str(ccd)})
        self.assertEqual(
            [(n, p) for n, p, _ in got],
            [("claude", self.home / ".claude"), ("claude-alt", ccd)],
        )

    def test_flag_wins_for_same_directory(self):
        ccd = self.tmp / ".claude-work"
        ccd.mkdir()
        got = ur.resolve_profiles(
            [f"work={ccd}"], self.home, {"CLAUDE_CONFIG_DIR": str(ccd)}
        )
        self.assertEqual([n for n, _, _ in got], ["claude", "work"])

    def test_no_hard_coded_work_profile(self):
        (self.home / ".claude-work").mkdir()
        got = ur.resolve_profiles([], self.home, {})
        self.assertEqual([n for n, _, _ in got], ["claude"])


class Changes(Base):
    def setUp(self):
        super().setUp()
        root = self.home / ".claude"
        fx.build_sessions(root, 1, 12, w5=5000)  # 2026-09-01 .. 09-12
        fx.build_sessions(root, 13, 12, w5=1000)  # 2026-09-13 .. 09-24

    def write_changes(
        self, path, metrics=("cache_write_5m", "sessions"), date="2026-09-13"
    ):
        fx.write_json(
            path,
            {
                "changes": [
                    {"date": date, "description": "demo", "metrics": list(metrics)}
                ]
            },
        )

    def test_compared(self):
        p = self.tmp / "c.json"
        self.write_changes(p)
        self.assertEqual(self.run_main("--changes", str(p)), 0, self.stderr)
        ba = self.load("diagnostics.json")["before_after"][0]
        self.assertEqual(ba["result"], "compared")
        self.assertEqual(ba["metrics"]["cache_write_5m"]["before"], 12 * 5000)
        self.assertEqual(ba["metrics"]["cache_write_5m"]["after"], 12 * 1000)
        self.assertEqual(ba["metrics"]["cache_write_5m"]["delta_pct"], "-80.0")

    def test_insufficient(self):
        p = self.tmp / "c.json"
        self.write_changes(
            p, date="2026-09-20"
        )  # 7 sessions after (13..19 before: 14 days back = 06..19)
        self.assertEqual(self.run_main("--changes", str(p)), 0, self.stderr)
        ba = self.load("diagnostics.json")["before_after"][0]
        self.assertEqual((ba["sessions_after"], ba["result"]), (5, "insufficient data"))

    def test_lookup_order(self):
        # 1) out-root/changes.json is used when --changes is absent
        self.write_changes(self.tmp / "runs" / "changes.json")
        self.assertEqual(self.run_main(), 0, self.stderr)
        self.assertEqual(
            self.load("manifest.json")["changes"]["path"],
            str(self.tmp / "runs" / "changes.json"),
        )
        # 2) --changes wins
        p = self.tmp / "c.json"
        self.write_changes(p)
        self.assertEqual(self.run_main("--changes", str(p), out="runs/out2"), 0)
        self.assertEqual(
            self.load("manifest.json", "runs/out2")["changes"]["path"], str(p)
        )
        # 3) template when neither exists
        self.assertEqual(self.run_main(out="other/out"), 0)
        self.assertEqual(
            Path(self.load("manifest.json", "other/out")["changes"]["path"]),
            SCRIPTS / "changes.json",
        )
        self.assertEqual(self.load("diagnostics.json", "other/out")["before_after"], [])

    def test_unknown_metric_exit_2(self):
        p = self.tmp / "c.json"
        self.write_changes(p, metrics=("nonsense",))
        self.assertEqual(self.run_main("--changes", str(p)), 2)
        self.assertIn("unknown metrics", self.stderr)

    def test_previous_run(self):
        self.assertEqual(self.run_main(out="runs/2026-09-27"), 0)
        self.assertIsNone(
            self.load("diagnostics.json", "runs/2026-09-27")["previous_run"]
        )
        self.assertEqual(self.run_main(out="runs/2026-09-28"), 0)
        prev = self.load("diagnostics.json", "runs/2026-09-28")["previous_run"]
        self.assertTrue(prev["comparable"])
        self.assertEqual(prev["result"], "compared")
        self.assertEqual(prev["metrics"]["sessions"]["delta"], "0")

    def test_template_is_valid(self):
        self.assertEqual(ur.load_changes(SCRIPTS / "changes.json"), [])


class Cli(Base):
    def test_subprocess_exit_codes(self):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        env.pop("CLAUDE_CONFIG_DIR", None)
        script = str(SCRIPTS / "usage_report.py")
        r = subprocess.run(
            [sys.executable, script, "--help"], capture_output=True, text=True, env=env
        )
        self.assertEqual(r.returncode, 0)
        r = subprocess.run(
            [sys.executable, script, "--out", str(self.tmp / "o")],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 2, r.stderr)
        fx.build_profile_a(self.home / ".claude")
        r = subprocess.run(
            [sys.executable, script, "--out", str(self.tmp / "o")],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.tmp / "o" / "manifest.json").is_file())

    def test_money_rounding_half_up(self):
        self.assertEqual(str(ur.q(ur.Decimal("0.125"))), "0.13")
        self.assertEqual(str(ur.q(ur.Decimal("0.135"))), "0.14")
        self.assertEqual(str(ur.pct(1, 8)), "12.5")
        self.assertEqual(str(ur.pct(1, 16)), "6.3")

    def test_prices_file(self):
        p = ur.Prices(SCRIPTS / "prices.json")
        self.assertEqual(p.checked, "2026-06-24")
        self.assertEqual(p.lookup("claude-opus-5-5-20260101")[2], ur.Decimal("0.05"))
        self.assertEqual(p.lookup("claude-opus-5")[2], ur.Decimal("0.1"))
        self.assertIsNone(p.lookup("gpt-x"))
        self.assertTrue(
            all(isinstance(v, ur.Decimal) for row in p.models.values() for v in row)
        )


if __name__ == "__main__":
    unittest.main()
