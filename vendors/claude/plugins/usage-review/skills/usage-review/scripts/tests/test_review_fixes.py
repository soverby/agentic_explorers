"""Tests for review round 1: task rounds, verdicts, stdout, robustness, dedup, weeks,
time zones, manifest counts, previous run, inventory allowlist, cost-weight scoring."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import fixtures as fx  # noqa: E402
import usage_report as ur  # noqa: E402
from test_usage_report import OUTPUTS, SCRIPTS, UTC, Base  # noqa: E402

PROJ = ("projects", fx.PROJECT)


def call(ts, mid, out=10, model="claude-opus-5", session="s-main", **kw):
    return fx.assistant(
        ts, mid, model, fx.usage(inp=1, read=1000, out=out), session=session, **kw
    )


class Home(Base):
    def proj(self, profile=".claude"):
        return self.home.joinpath(profile, *PROJ)

    def main_session(self, sid="s-main", day="2026-09-14"):
        fx.write_jsonl(
            self.proj() / f"{sid}.jsonl",
            [call(f"{day}T08:00:00Z", f"m-{sid}", session=sid)],
        )

    def subagent(
        self,
        name,
        agent,
        desc,
        ts,
        text=None,
        handback=None,
        sid="s-main",
        tokens=1000,
        model=None,
    ):
        content = [{"type": "text", "text": text or f"done {fx.MARKER}"}]
        if handback is not None:
            content.append(
                fx.tool_use(f"hb-{name}", "SubagentHandback", message=handback)
            )
        d = self.proj() / sid / "subagents"
        fx.write_jsonl(
            d / f"{name}.jsonl",
            [
                fx.assistant(
                    ts,
                    f"m-{name}",
                    model or "claude-sonnet-5",
                    fx.usage(inp=1, w5=tokens, out=10),
                    isSidechain=True,
                    session=sid,
                    content=content,
                )
            ],
        )
        fx.write_json(
            d / f"{name}.meta.json", {"agentType": agent, "description": desc}
        )


class TaskRounds(Home):
    def test_independent_tasks_no_finding_and_4_rounds_finding(self):
        self.main_session()
        t = iter(f"2026-09-14T{h:02d}:00:00Z" for h in range(9, 24))
        for p in ("P1 network isolation", "P2 secrets store", "P3 CLI core"):
            self.subagent(f"b-{p[:2]}", "builder", f"Build {p}", next(t))
            self.subagent(
                f"r-{p[:2]}",
                "reviewer",
                f"Validate {p}",
                next(t),
                text="VERDICT: APPROVE",
            )
        self.assertEqual(self.run_main(), 0, self.stderr)
        d = self.load("diagnostics.json")
        self.assertEqual(d["builder_reviewer"]["tasks_by_rounds"], {"1": 3})
        ids = [f["rule"] for f in self.load("findings.json")["findings"]]
        self.assertNotIn("reviewer_rounds_over_threshold", ids)

        # one more task with 4 rounds
        self.subagent("b-D1", "builder", "Build P4 model routing", next(t))
        for i in range(1, 5):
            self.subagent(
                f"r-D{i}",
                "reviewer",
                f"Validate P4 model routing round {i}",
                next(t),
                text="VERDICT: CHANGES" if i < 4 else "VERDICT: APPROVE",
            )
            if i < 4:
                self.subagent(
                    f"b-D{i + 1}",
                    "builder",
                    f"Fix P4 model routing (round {i + 1})",
                    next(t),
                )
        self.assertEqual(self.run_main(out="r2/out"), 0, self.stderr)
        d = self.load("diagnostics.json", "r2/out")
        self.assertEqual(d["builder_reviewer"]["tasks_by_rounds"], {"1": 3, "4": 1})
        f = {x["rule"]: x for x in self.load("findings.json", "r2/out")["findings"]}
        rr = f["reviewer_rounds_over_threshold"]
        self.assertEqual(rr["measured"], 1)
        self.assertEqual(rr["confidence"], "measured")
        # runs after round 2: builders 3, 4 and reviewers 3, 4
        self.assertEqual(
            sorted(rr["evidence"]["extra_run_ids"]), ["b-D3", "b-D4", "r-D3", "r-D4"]
        )

    def test_time_linked_run_makes_finding_inferred(self):
        self.main_session()
        t = iter(f"2026-09-14T{h:02d}:00:00Z" for h in range(9, 24))
        self.subagent("b1", "builder", "Build gateway", next(t))
        for i in range(3):
            self.subagent(
                f"r{i}", "reviewer", "Check the work", next(t), text="VERDICT: CHANGES"
            )
        self.assertEqual(self.run_main(), 0, self.stderr)
        task = self.load("diagnostics.json")["builder_reviewer"]["tasks"][0]
        self.assertEqual(task["rounds"], 3)
        self.assertEqual(task["linked_by"], {"new": 1, "time": 3})
        rr = {x["rule"]: x for x in self.load("findings.json")["findings"]}[
            "reviewer_rounds_over_threshold"
        ]
        self.assertEqual(rr["confidence"], "inferred")

    def test_task_tokens(self):
        self.assertEqual(
            ur.task_tokens("Validate P6b OAuth MCP (round 2)"),
            frozenset({"p6b", "oauth", "mcp"}),
        )
        self.assertEqual(
            ur.overlap(
                ur.task_tokens("Build P0 scaffold and profile schema"),
                ur.task_tokens("Validate P0 build"),
            ),
            1,
        )


class Verdicts(Home):
    def test_handback_first_then_text(self):
        self.main_session()
        self.subagent(
            "r1",
            "reviewer",
            "Validate A",
            "2026-09-14T09:00:00Z",
            text="VERDICT: APPROVE",
            handback=f"{fx.MARKER}\n**VERDICT: CHANGES REQUESTED**",
        )
        self.subagent(
            "r2",
            "reviewer",
            "Validate B",
            "2026-09-14T10:00:00Z",
            text="VERDICT: PASS - all good",
            handback="no verdict line here",
        )
        self.subagent(
            "r3",
            "reviewer",
            "Validate C",
            "2026-09-14T11:00:00Z",
            text="Approved, LGTM",
            handback="Verdict: yes, with one condition",
        )
        self.assertEqual(self.run_main(), 0, self.stderr)
        d = self.load("diagnostics.json")
        by = {r["run"]: r["verdict"] for r in d["subagent_runs"]}
        self.assertEqual(by, {"r1": "changes", "r2": "approve", "r3": "unclear"})
        for name in OUTPUTS:
            self.assertNotIn(fx.MARKER, self.out(name).read_text(encoding="utf-8"))


class Stdout(Home):
    def test_ok_line_is_last(self):
        fx.build_profile_a(self.home / ".claude")  # has an unpriced model
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        env.pop("CLAUDE_CONFIG_DIR", None)
        r = subprocess.run(
            [
                sys.executable,
                str(SCRIPTS / "usage_report.py"),
                "--out",
                str(self.tmp / "o"),
            ],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.strip().splitlines()
        self.assertTrue(lines[-1].startswith("OK: "), lines)
        self.assertTrue(lines[-1].endswith(f"-> {self.tmp / 'o'}"), lines)
        self.assertTrue(
            any(ln.startswith("Unpriced models") for ln in lines[:-1]), lines
        )


def good_lines(n=40, day="2026-09-14"):
    return [
        fx.assistant(
            f"{day}T10:{i:02d}:00Z",
            f"g{i}",
            "claude-opus-5",
            fx.usage(inp=1, out=1, read=5),
            session="s",
        )
        for i in range(n)
    ]


class Robustness(Home):
    """The cases of the reviewer's rv_rob.py: no traceback, a count instead."""

    def run_case(self, fn):
        p = self.proj()
        fx.write_jsonl(p / "s.jsonl", good_lines())
        fn(p)
        rc = self.run_main()
        self.assertNotIn("Traceback", self.stderr)
        return rc

    def manifest(self):
        return self.load("manifest.json")

    def one(self, obj):
        return lambda p: fx.write_jsonl(p / "a.jsonl", [obj])

    def asst(self, **msg):
        m = dict(id="q", model="claude-opus-5", usage=dict(output_tokens=1))
        m.update(msg)
        return dict(type="assistant", timestamp="2026-09-14T11:00:00Z", message=m)

    def test_malformed_json(self):
        self.assertEqual(
            self.run_case(
                lambda p: (p / "a.jsonl").write_text('{bad\n[1,2]\n"str"\nnull\n')
            ),
            0,
        )
        self.assertEqual(
            self.manifest()["lines_skipped"], {"bad json": 1, "not a json object": 3}
        )

    def test_empty_file_and_non_utf8(self):
        def fn(p):
            (p / "e.jsonl").write_text("")
            (p / "n.jsonl").write_bytes(
                b"\xff\xfe\x80garbage\n"
                + json.dumps(self.asst(id="z")).encode()
                + b"\xc3\x28\n"
            )

        self.assertEqual(self.run_case(fn), 0)
        self.assertEqual(self.manifest()["lines_skipped"], {"bad json": 2})

    def test_large_line(self):
        big = dict(
            type="user",
            timestamp="2026-09-14T11:00:00Z",
            message=dict(content="x" * 5_000_000),
        )
        self.assertEqual(self.run_case(self.one(big)), 0)

    def test_missing_and_bad_meta(self):
        def fn(p):
            s = p / "s" / "subagents"
            fx.write_jsonl(s / "agent-x.jsonl", [self.asst(id="q1")])
            fx.write_jsonl(s / "agent-y.jsonl", [self.asst(id="q2")])
            (s / "agent-y.meta.json").write_bytes(b"\xff[1")

        self.assertEqual(self.run_case(fn), 0)
        self.assertEqual(self.manifest()["missing_fields"]["subagent meta.json"], 1)
        self.assertEqual(
            self.manifest()["missing_fields"]["subagent meta.json unreadable"], 1
        )

    def symlink(self, target, link):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")

    def test_symlink_loop(self):
        self.assertEqual(
            self.run_case(
                lambda p: (
                    self.symlink(p, p / "loop"),
                    self.symlink(p.parent, p / "loop2"),
                )
            ),
            0,
        )

    def test_dangling_symlink_is_unreadable(self):
        self.assertEqual(
            self.run_case(
                lambda p: self.symlink(self.tmp / "nonexistent", p / "dead.jsonl")
            ),
            0,
        )
        self.assertEqual(self.manifest()["lines_skipped"]["unreadable file"], 1)

    def test_dir_named_jsonl_is_unreadable(self):
        self.assertEqual(self.run_case(lambda p: (p / "d.jsonl").mkdir()), 0)
        self.assertEqual(self.manifest()["lines_skipped"]["unreadable file"], 1)

    @unittest.skipIf(
        os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
        "chmod does not block",
    )
    def test_unreadable_file(self):
        def fn(p):
            f = p / "a.jsonl"
            f.write_text("{}\n")
            f.chmod(0)
            self.addCleanup(f.chmod, 0o600)

        self.assertEqual(self.run_case(fn), 0)
        self.assertEqual(self.manifest()["lines_skipped"]["unreadable file"], 1)

    def test_bad_types_are_drift(self):
        cases = [
            self.asst(usage=dict(output_tokens="12x")),
            self.asst(usage=dict(output_tokens=1.5)),
            self.asst(usage=dict(output_tokens=True)),
            self.asst(usage=dict(input_tokens=-1)),
            self.asst(id=["q"]),
            dict(self.asst(id=None), requestId={"a": 1}),
            self.asst(content=[{"type": "tool_use", "id": {"a": 1}, "name": "X"}]),
            self.asst(
                usage=dict(
                    output_tokens=1, cache_creation=dict(ephemeral_1h_input_tokens="9")
                )
            ),
        ]
        for i, c in enumerate(cases):
            with self.subTest(i=i):
                self.setUp()
                self.assertEqual(self.run_case(self.one(c)), 0)
                drift = self.manifest()["schema_drift"]
                self.assertEqual(drift["lines"], 1, drift)
                total = self.csv_rows("weekly_by_model_effort.csv")[-1]
                self.assertEqual(int(total["api_calls"]), 40)

    def test_bad_timestamps(self):
        for ts in (
            "0001-01-01T00:00:00Z",
            12345,
            "9999-12-31T23:59:59-14:00",
            "not a date",
        ):
            with self.subTest(ts=ts):
                self.setUp()
                self.assertEqual(
                    self.run_case(self.one(dict(self.asst(), timestamp=ts))), 0
                )
                self.assertEqual(
                    self.manifest()["schema_drift"]["by_reason"], {"timestamp": 1}
                )
        self.assertIsNone(ur.parse_ts("0001-01-01T00:00:00+14:00", UTC))

    def test_model_int_and_weird_cwd(self):
        self.assertEqual(
            self.run_case(self.one(dict(self.asst(model=5), cwd="\x00bad"))), 0
        )
        self.assertEqual(self.manifest()["missing_fields"]["message.model"], 1)

    def test_settings_and_inventory_oddities(self):
        def fn(p):
            root = p.parent.parent
            fx.write_json(
                root / "settings.json", {"enabledPlugins": ["a"], "hooks": [1]}
            )
            s = root / "skills" / "k"
            s.mkdir(parents=True)
            (s / "SKILL.md").write_bytes(b"\xff---")
            m = p / "memory"
            m.mkdir()
            self.symlink(self.tmp / "nonexistent", m / "x.md")

        self.assertEqual(self.run_case(fn), 0)
        inv = self.load("inventory.json")
        self.assertEqual(inv["skipped"], {"memory file: stat failed": 1})
        self.assertEqual(inv["profiles"]["claude"]["settings"]["enabled_plugins"], [])

    def test_no_projects_and_all_drift_exit_2(self):
        self.assertEqual(self.run_main(), 2)
        fx.write_jsonl(
            self.proj() / "s.jsonl",
            [
                dict(
                    type="assistant",
                    timestamp="2026-09-14T11:00:00Z",
                    message=dict(id=f"q{i}", model="claude-opus-5"),
                )
                for i in range(10)
            ],
        )
        self.assertEqual(self.run_main(), 2)
        self.assertIn("schema drift", self.stderr)
        self.assertNotIn("Traceback", self.stderr)


class Dedup(Home):
    def test_keeps_larger_output_either_order(self):
        fx.write_jsonl(
            self.proj() / "s.jsonl",
            [
                call("2026-09-14T10:00:00Z", "m1", out=5),
                call("2026-09-14T10:00:01Z", "m1", out=100),  # partial, final
                call("2026-09-14T10:01:00Z", "m2", out=200),
                call("2026-09-14T10:01:01Z", "m2", out=7),  # final, partial
            ],
        )
        self.assertEqual(self.run_main(), 0, self.stderr)
        total = self.csv_rows("weekly_by_model_effort.csv")[-1]
        self.assertEqual((int(total["api_calls"]), int(total["output"])), (2, 300))

    def test_line_fallback_key_keeps_each_line(self):
        lines = [call(f"2026-09-14T10:0{i}:00Z", None, out=i + 1) for i in range(3)]
        fx.write_jsonl(self.proj() / "s.jsonl", lines)
        self.assertEqual(self.run_main(), 0, self.stderr)
        total = self.csv_rows("weekly_by_model_effort.csv")[-1]
        self.assertEqual((int(total["api_calls"]), int(total["output"])), (3, 6))
        self.assertEqual(
            self.load("manifest.json")["missing_fields"]["message.id and requestId"], 3
        )

    def test_tie_prefers_non_fork_then_earliest(self):
        self.main_session()
        d = self.proj() / "s-main" / "subagents"
        same = dict(out=50, session="s-main", isSidechain=True)
        fx.write_jsonl(
            d / "agent-a.jsonl", [call("2026-09-14T10:00:00Z", "dup", **same)]
        )  # fork, sorted first
        fx.write_json(d / "agent-a.meta.json", {"agentType": "fork"})
        fx.write_jsonl(
            d / "agent-b.jsonl", [call("2026-09-14T10:00:05Z", "dup", **same)]
        )
        fx.write_json(d / "agent-b.meta.json", {"agentType": "general-purpose"})
        fx.write_jsonl(
            d / "agent-c.jsonl", [call("2026-09-14T10:00:03Z", "dup2", **same)]
        )
        fx.write_json(d / "agent-c.meta.json", {"agentType": "Explore"})
        fx.write_jsonl(
            d / "agent-d.jsonl", [call("2026-09-14T10:00:01Z", "dup2", **same)]
        )
        fx.write_json(d / "agent-d.meta.json", {"agentType": "Plan"})
        self.assertEqual(self.run_main(), 0, self.stderr)
        rows = {
            r["agent_type"]: int(r["api_calls"])
            for r in self.csv_rows("weekly_by_agent.csv")
        }
        self.assertEqual(rows, {"": 1, "general-purpose": 1, "Plan": 1})


class WeeksAndTz(Home):
    def test_iso_year_boundary(self):
        self.assertEqual(ur.week_of(dt.date(2026, 12, 31)), "2026-W53")
        self.assertEqual(ur.week_of(dt.date(2027, 1, 1)), "2026-W53")
        self.assertEqual(ur.week_of(dt.date(2027, 1, 3)), "2026-W53")  # Sunday
        self.assertEqual(ur.week_of(dt.date(2027, 1, 4)), "2027-W01")  # Monday
        fx.write_jsonl(
            self.proj() / "s.jsonl",
            [
                call("2026-12-31T12:00:00Z", "a"),
                call("2027-01-01T12:00:00Z", "b"),
                call("2027-01-03T12:00:00Z", "c"),
                call("2027-01-04T12:00:00Z", "d"),
            ],
        )
        self.assertEqual(self.run_main(), 0, self.stderr)
        weeks = [
            r["week"].split(" ")[0]
            for r in self.csv_rows("weekly_by_model_effort.csv")
            if r["model"] == "SUBTOTAL"
        ]
        self.assertEqual(weeks, ["2026-W53", "2027-W01"])

    def test_dst_time_zone(self):
        """Europe-style DST: UTC+0, then UTC+1 from 2026-03-29 01:00 UTC."""
        switch = dt.datetime(2026, 3, 29, 1, tzinfo=dt.timezone.utc)

        class Dst(dt.tzinfo):
            def utcoffset(self, d):
                return self.dst(d)

            def dst(self, d):
                naive = d.replace(tzinfo=None)
                return dt.timedelta(
                    hours=1 if naive >= switch.replace(tzinfo=None) else 0
                )

            def fromutc(self, d):
                off = (
                    dt.timedelta(hours=1)
                    if d.replace(tzinfo=None) >= switch.replace(tzinfo=None)
                    else dt.timedelta()
                )
                return d + off

            def tzname(self, d):
                return "DST-test"

        fx.write_jsonl(
            self.proj() / "s.jsonl",
            [
                call(
                    "2026-03-22T23:30:00Z", "before"
                ),  # Sunday 23:30 local (UTC+0) -> W12
                call(
                    "2026-03-29T23:30:00Z", "after"
                ),  # Monday 00:30 local (UTC+1) -> W14
            ],
        )
        import contextlib
        import io

        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            rc = ur.main(
                ["--out", str(self.tmp / "o")],
                home=self.home,
                env={},
                today=fx_today(),
                tz=Dst(),
            )
        self.assertEqual(rc, 0)
        rows = [
            r
            for r in self.csv_rows("weekly_by_model_effort.csv", "o")
            if r["model"] == "SUBTOTAL"
        ]
        self.assertEqual(
            [r["week"].split(" ")[0] for r in rows], ["2026-W12", "2026-W14"]
        )


def fx_today():
    return dt.date(2026, 9, 28)


class ManifestRange(Home):
    def test_closed_range_manifest_is_reproducible(self):
        fx.write_jsonl(self.proj() / "a.jsonl", good_lines(day="2026-09-14") + ["{bad"])
        args = ("--start", "2026-09-14", "--end", "2026-09-14")
        self.assertEqual(self.run_main(*args, out="r1/out"), 0, self.stderr)
        fx.write_jsonl(
            self.proj() / "later.jsonl", good_lines(day="2026-09-20") + ["{bad", "{bad"]
        )
        fx.write_jsonl(
            self.proj() / "drift.jsonl",
            [
                dict(
                    type="assistant",
                    timestamp="2026-09-21T10:00:00Z",
                    message=dict(id="x", model="m"),
                )
            ],
        )
        self.assertEqual(self.run_main(*args, out="r2/out"), 0, self.stderr)
        keys = (
            "files_scanned",
            "lines_read",
            "assistant_lines",
            "lines_skipped",
            "missing_fields",
            "schema_drift",
            "reconciliation",
        )
        m1, m2 = (
            self.load("manifest.json", "r1/out"),
            self.load("manifest.json", "r2/out"),
        )
        self.assertEqual({k: m1[k] for k in keys}, {k: m2[k] for k in keys})
        self.assertEqual(
            (m1["files_scanned"], m1["lines_read"], m1["lines_skipped"]),
            (1, 41, {"bad json": 1}),
        )


class PreviousRun(Home):
    def setUp(self):
        super().setUp()
        fx.build_sessions(self.home / ".claude", 1, 3, w5=100)

    def fake_run(self, name, data_end, tool=ur.TOOL):
        d = self.tmp / "runs" / name
        fx.write_json(
            d / "manifest.json",
            {
                "tool": tool,
                "generated_at": "2026-09-01",
                "date_range": {"data_end": data_end},
            },
        )
        fx.write_json(d / "diagnostics.json", {"summary": {"sessions": 1, "calls": 1}})
        return d

    def test_chosen_by_manifest_not_name(self):
        self.fake_run("zzz", "2026-08-01")
        newer = self.fake_run("aaa", "2026-09-01")
        self.fake_run("zzzz", "2026-12-01", tool="other")
        other = self.tmp / "runs" / "zzzzz"
        fx.write_json(
            other / "diagnostics.json", {"summary": {"sessions": 1}}
        )  # no manifest
        self.assertEqual(self.run_main(out="runs/new"), 0, self.stderr)
        self.assertEqual(
            self.load("diagnostics.json", "runs/new")["previous_run"]["path"],
            str(newer),
        )

    def test_parent_without_tool_manifest_is_ignored(self):
        other = self.tmp / "runs" / "x"
        fx.write_json(other / "diagnostics.json", {"summary": {"sessions": 1}})
        self.assertEqual(self.run_main(out="runs/new"), 0, self.stderr)
        self.assertIsNone(self.load("diagnostics.json", "runs/new")["previous_run"])


class InventoryAllowlist(Home):
    def test_objects_not_copied(self):
        fx.build_profile_a(self.home / ".claude")
        st = json.loads((self.home / ".claude" / "settings.json").read_text())
        st["modelSettings"] = {
            "claude-opus-5": {
                "effortLevel": "high",
                "note": fx.MARKER,
                "maxThinkingTokens": {"x": fx.MARKER},
            },
            f"bad {fx.MARKER}": {"effortLevel": "low"},
        }
        st["outputStyle"] = f"My style {fx.MARKER}"
        st["model"] = {"nested": fx.MARKER}
        st["effortLevel"] = f"high {fx.MARKER}"
        fx.write_json(self.home / ".claude" / "settings.json", st)
        self.assertEqual(self.run_main(), 0, self.stderr)
        s = self.load("inventory.json")["profiles"]["claude"]["settings"]
        self.assertEqual(
            s["model_settings"],
            {"claude-opus-5": {"effortLevel": "high", "maxThinkingTokens": None}},
        )
        self.assertEqual(s["output_style"], "custom")
        self.assertEqual((s["keys"]["model"], s["keys"]["effortLevel"]), (None, None))
        self.assertNotIn(
            fx.MARKER, self.out("inventory.json").read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
