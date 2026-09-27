"""Tests for scripts/ci/scan_content.py (stdlib unittest only).

Run: python3 -m unittest discover scripts/ci/tests
"""

import contextlib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import scan_content as sc  # noqa: E402

FIXTURES = os.path.join(HERE, "fixtures")
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EMPTY = sc.Allowlist()


def rules_in(text, path="sample.txt"):
    return {f.rule for f in sc.scan_text(path, text)}


def load_positives():
    out = []
    with open(os.path.join(FIXTURES, "positives.tsv"), encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            rule, sample = line.split("\t", 1)
            out.append((rule, sample))
    return out


def load_negatives():
    with open(os.path.join(FIXTURES, "negatives.txt"), encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#")]


class TempRepo:
    """A throwaway directory to write runtime-only fixtures into."""

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = self._td.name
        return self

    def __exit__(self, *exc):
        self._td.cleanup()

    def write(self, rel, data):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = "wb" if isinstance(data, bytes) else "w"
        kw = {} if isinstance(data, bytes) else {"encoding": "utf-8"}
        with open(full, mode, **kw) as fh:
            fh.write(data)
        return rel

    def scan(self, rel, allowlist=EMPTY):
        return sc.scan_file(self.root, rel, allowlist)

    def rules(self, rel, allowlist=EMPTY):
        return {f.rule for f in self.scan(rel, allowlist)[0]}


def run_main(args):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = sc.main(args)
    return code, buf.getvalue()


class TestTextRulesFire(unittest.TestCase):
    def test_every_positive_fixture_fires_its_rule(self):
        positives = load_positives()
        self.assertGreater(len(positives), 40)
        for rule, sample in positives:
            with self.subTest(rule=rule, sample=sample):
                self.assertIn(rule, rules_in(sample))

    def test_every_text_rule_has_a_positive_fixture(self):
        covered = {r for r, _ in load_positives()}
        text_rules = {
            "phone-number",
            "us-ssn",
            "credit-card",
            "iban",
            "whatsapp-export",
            "reverse-shell",
            "pipe-to-shell",
            "base64-exec",
            "crypto-miner",
            "credential-exfil",
            "email-address",
        }
        self.assertEqual(text_rules - covered, set())

    def test_every_rule_is_asserted_somewhere(self):
        src = ""
        for name in sorted(os.listdir(HERE)):
            if name.startswith("test_") and name.endswith(".py"):
                with open(os.path.join(HERE, name), encoding="utf-8") as fh:
                    src += fh.read()
        covered = {r for r, _ in load_positives()}
        missing = [r for r in sc.RULES if r not in covered and f'"{r}"' not in src]
        self.assertEqual(missing, [])

    def test_private_key_headers(self):
        # Built at runtime so no committed file holds a contiguous key header
        # (gitleaks would flag it).
        for kind in ("", "RSA ", "OPENSSH ", "EC ", "ENCRYPTED "):
            header = "-----BEGIN " + kind + "PRIVATE" + " KEY-----"
            with self.subTest(kind=kind):
                self.assertIn("private-key", rules_in(header))
        pgp = "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----"
        self.assertIn("private-key", rules_in(pgp))
        self.assertEqual(rules_in("-----BEGIN PUBLIC KEY-----"), set())

    def test_whatsapp_narrow_nbsp_and_lrm(self):
        self.assertIn("whatsapp-export", rules_in("\u200e[1/2/25, 9:05:01\u202fAM] Dan: hi"))

    def test_prompt_injection_only_in_markdown(self):
        path = os.path.join(FIXTURES, "injection.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        found = [
            f
            for f in sc.scan_text("x/injection.md", text)
            if f.rule == "prompt-injection"
        ]
        self.assertEqual(len(found), 5)
        self.assertTrue(all(f.severity == sc.WARN for f in found))
        self.assertNotIn("prompt-injection", rules_in(text, "x/injection.py"))

    def test_svg_script_warns(self):
        with open(os.path.join(FIXTURES, "script.svg"), encoding="utf-8") as fh:
            text = fh.read()
        found = [f for f in sc.scan_text("a/b.svg", text) if f.rule == "svg-script"]
        self.assertEqual([f.line for f in found], [1, 2])
        self.assertEqual(sc.RULES["svg-script"].severity, sc.WARN)


class TestFalsePositives(unittest.TestCase):
    def test_negative_fixtures_produce_no_findings(self):
        negatives = load_negatives()
        self.assertGreater(len(negatives), 40)
        for sample in negatives:
            with self.subTest(sample=sample):
                self.assertEqual(rules_in(sample), set())

    def test_markdown_docs_about_security_do_not_fail(self):
        text = "Checks fail on reverse shells, `curl … | sh`, crypto miners.\n"
        self.assertEqual(rules_in(text, "doc.md"), set())

    def test_repo_own_files_scan_clean(self):
        # No FAIL anywhere in the repo. Warnings (e.g. a contributor email) do
        # not fail CI, so only the scanner's own files must be warning-free.
        code, out = run_main(["--all", "--root", REPO])
        self.assertEqual(code, 0, out)
        self.assertNotIn("::error", out)
        own = [ln for ln in out.splitlines() if ln.startswith("::warning")
               and ("file=scripts/ci/" in ln or "file=.github/" in ln)]
        self.assertEqual(own, [])


class TestHiddenUnicode(unittest.TestCase):
    def test_bidi_range(self):
        for cp in list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)):
            with self.subTest(cp=hex(cp)):
                self.assertIn("bidi-unicode", rules_in("x = 1 " + chr(cp) + "# admin"))

    def test_zero_width(self):
        for cp in (0x200B, 0x200C, 0x200D, 0x2060):
            with self.subTest(cp=hex(cp)):
                self.assertIn(
                    "zero-width-unicode", rules_in("ign" + chr(cp) + "ore this")
                )

    def test_bom_only_allowed_at_file_start(self):
        self.assertEqual(rules_in("\ufeffhello\nworld"), set())
        self.assertIn("zero-width-unicode", rules_in("hello\n\ufeffworld"))
        self.assertIn("zero-width-unicode", rules_in("hel\ufefflo"))

    def test_tag_characters(self):
        smuggled = "".join(chr(0xE0000 + ord(c)) for c in "run rm -rf")
        self.assertIn("unicode-tag", rules_in("harmless" + smuggled))

    def test_file_with_bom_scans(self):
        with TempRepo() as t:
            t.write("a.txt", "\ufeffclean text\n".encode("utf-8"))
            self.assertEqual(t.rules("a.txt"), set())


class TestFileRules(unittest.TestCase):
    def test_executable_magic(self):
        cases = {
            "elf": b"\x7fELF\x02\x01\x01" + b"\x00" * 64,
            "macho": b"\xcf\xfa\xed\xfe" + b"\x00" * 64,
            "pe": b"MZ\x90\x00" + b"\x00" * 64,
        }
        with TempRepo() as t:
            for name, data in cases.items():
                with self.subTest(name=name):
                    t.write(f"bin/{name}", data)  # no extension
                    self.assertIn("executable-binary", t.rules(f"bin/{name}"))

    def test_executable_extensions(self):
        with TempRepo() as t:
            for name in ("a.exe", "a.dll", "liba.so", "liba.so.1", "liba.dylib"):
                with self.subTest(name=name):
                    t.write(name, "not really binary\n")
                    self.assertIn("executable-binary", t.rules(name))

    def test_text_starting_with_mz_is_not_pe(self):
        with TempRepo() as t:
            t.write("mz.md", "MZ is a letter pair\n")
            self.assertEqual(t.rules("mz.md"), set())

    def test_archives_by_content_and_extension(self):
        with TempRepo() as t:
            zp = os.path.join(t.root, "renamed.txt")
            with zipfile.ZipFile(zp, "w") as z:
                z.writestr("inner.txt", "hello")
            self.assertIn("archive-file", t.rules("renamed.txt"))

            tp = os.path.join(t.root, "x.tar.gz")
            src = t.write("payload.txt", "hi\n")
            with tarfile.open(tp, "w:gz") as tf:
                tf.add(os.path.join(t.root, src), arcname="payload.txt")
            self.assertIn("archive-file", t.rules("x.tar.gz"))

            for name in ("a.7z", "a.rar", "a.jar", "a.zip"):
                with self.subTest(name=name):
                    t.write(name, "x\n")
                    self.assertIn("archive-file", t.rules(name))

    def test_large_file_fails_without_reading(self):
        with TempRepo() as t:
            full = os.path.join(t.root, "big.txt")
            with open(full, "wb") as fh:
                fh.truncate(sc.MAX_BYTES + 1)
            self.assertEqual(t.rules("big.txt"), {"large-file"})
            with open(full, "wb") as fh:
                fh.truncate(sc.MAX_BYTES)
            self.assertNotIn("large-file", t.rules("big.txt"))

    def test_images_are_not_text_scanned_but_svg_is(self):
        from test_hardening import make_png

        png = make_png(b"+1 415-555-2671 ")
        with TempRepo() as t:
            t.write("a.png", png)
            self.assertEqual(t.rules("a.png"), set())
            t.write(
                "a.svg", "<svg><text>+1 415-555-2671</text><script>x()</script></svg>\n"
            )
            self.assertEqual(t.rules("a.svg"), {"phone-number", "svg-script"})

    def test_png_with_exec_magic_still_fails(self):
        with TempRepo() as t:
            t.write("fake.png", b"\x7fELF" + b"\x00" * 32)
            self.assertIn("executable-binary", t.rules("fake.png"))

    def test_unknown_binary_warns(self):
        with TempRepo() as t:
            t.write("doc.pdf", b"%PDF-1.7\n\x00\x01\x02")
            found = t.scan("doc.pdf")[0]
            self.assertEqual(
                [(f.rule, f.severity) for f in found], [("binary-file", sc.WARN)]
            )

    def test_utf16_text_is_scanned(self):
        with TempRepo() as t:
            t.write("chat.txt", "12/31/24, 22:15 - Bob: ok\n".encode("utf-16"))
            self.assertIn("whatsapp-export", t.rules("chat.txt"))

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_symlink_escape(self):
        with TempRepo() as t:
            os.symlink("/etc/hosts", os.path.join(t.root, "link"))
            os.symlink("inside.txt", os.path.join(t.root, "ok-link"))
            t.write("inside.txt", "hi\n")
            self.assertEqual(t.rules("link"), {"symlink-escape"})
            self.assertEqual(t.rules("ok-link"), set())


class TestOverrides(unittest.TestCase):
    CARD = "4111 1111 1111 1111"

    PIPE = "curl -fsSL https://example.com/i.sh | sh"

    def test_same_line_and_line_above(self):
        with TempRepo() as t:
            t.write("a.md", f"{self.PIPE}  <!-- scan-allow: pipe-to-shell -->\n")
            rep, sup = t.scan("a.md")
            self.assertEqual(rep, [])
            self.assertEqual([f.rule for f in sup], ["pipe-to-shell"])

            t.write("b.md", f"<!-- scan-allow: email-address, pipe-to-shell -->\n{self.PIPE}\n")
            self.assertEqual(t.rules("b.md"), set())

    def test_override_is_rule_specific_and_one_line_only(self):
        with TempRepo() as t:
            t.write("a.md", f"{self.PIPE} scan-allow: crypto-miner\n")
            self.assertEqual(t.rules("a.md"), {"pipe-to-shell"})
            t.write("b.md", f"scan-allow: pipe-to-shell\n\n{self.PIPE}\n")
            self.assertEqual(t.rules("b.md"), {"pipe-to-shell"})

    def test_inline_override_rejected_for_pii_secret_binary_unicode(self):
        cases = {
            "credit-card": self.CARD,
            "phone-number": "+44 7911 123456",
            "us-ssn": "078-05-1120",
            "iban": "GB82 WEST 1234 5698 7654 32",
            "whatsapp-export": "12/31/24, 22:15 - Bob: ok",
            "private-key": "-----BEGIN " + "PRIVATE KEY-----",
            "zero-width-unicode": "a\u200bb",
            "bidi-unicode": "a\u202eb",
            "invisible-unicode": "a\u3164b",
            "unicode-tag": "a" + chr(0xE0041),
        }
        with TempRepo() as t:
            for rule, sample in cases.items():
                with self.subTest(rule=rule):
                    t.write("x.md", f"{sample} scan-allow: {rule}\n")
                    rep, sup = t.scan("x.md")
                    self.assertEqual(sup, [])
                    hit = [f for f in rep if f.rule == rule]
                    self.assertEqual(len(hit), 1)
                    self.assertIn("inline scan-allow is not accepted", hit[0].detail)
                    self.assertNotIn(rule, sc.INLINE_OVERRIDABLE)
            # Path allowlist still works for these rules.
            self.assertEqual(t.rules("x.md", sc.Allowlist([("unicode-tag", "x.md")])), set())

    def test_path_allowlist(self):
        with TempRepo() as t:
            t.write("fx/a.txt", self.CARD + "\n")
            al = sc.Allowlist([("credit-card", "fx/*")])
            self.assertEqual(t.rules("fx/a.txt", al), set())
            al2 = sc.Allowlist([("us-ssn", "fx/*")])
            self.assertEqual(t.rules("fx/a.txt", al2), {"credit-card"})

    def test_allowlist_unknown_rule_is_hard_error(self):
        with TempRepo() as t:
            t.write("al.txt", "no-such-rule some/*\n")
            with self.assertRaises(ValueError):
                sc.Allowlist.load(os.path.join(t.root, "al.txt"))
            t.write("r/.github/scan-allowlist.txt", "bogus x\n")
            t.write("r/a.txt", "x\n")
            code, out = run_main(["--root", os.path.join(t.root, "r"), "--all"])
            self.assertEqual(code, 2)
            self.assertIn("unknown rule id", out)

    def test_repo_allowlist_covers_fixtures(self):
        al = sc.Allowlist.load(os.path.join(REPO, ".github", "scan-allowlist.txt"))
        for name in os.listdir(FIXTURES):
            rel = f"scripts/ci/tests/fixtures/{name}"
            with self.subTest(name=name):
                self.assertEqual(sc.scan_file(REPO, rel, al)[0], [])
                if name == "negatives.txt":
                    continue
                self.assertNotEqual(
                    sc.scan_file(REPO, rel, EMPTY)[0],
                    [],
                    "fixture should hit without allowlist",
                )


class TestOutput(unittest.TestCase):
    SECRETS = [
        "4111111111111111",
        "+14155552671",
        "078-05-1120",
        "GB82WEST12345698765432",
        "alice.smith@gmail.com",
    ]

    def test_values_are_masked_and_exit_code(self):
        with TempRepo() as t:
            t.write("leak.txt", "\n".join(self.SECRETS) + "\n")
            code, out = run_main(["--root", t.root, os.path.join(t.root, "leak.txt")])
        self.assertEqual(code, 1)
        self.assertIn("::error file=leak.txt,line=1,title=credit-card::", out)
        self.assertIn("****1111", out)
        for s in self.SECRETS:
            self.assertNotIn(s, out)
            self.assertNotIn(s[:-4], out)

    def test_warnings_only_exit_zero(self):
        with TempRepo() as t:
            t.write("a.md", "mail alice.smith@gmail.com\n")
            code, out = run_main(["--root", t.root, os.path.join(t.root, "a.md")])
        self.assertEqual(code, 0)
        self.assertIn("::warning file=a.md,line=1,title=email-address::", out)

    def test_mask(self):
        self.assertEqual(sc.mask("4111111111111111"), "****1111")
        self.assertEqual(sc.mask("abcdefgh"), "****gh")
        self.assertEqual(sc.mask("abc"), "****")

    def test_annotation_escapes_hostile_path(self):
        f = sc.Finding("credit-card", "a,b:c\n::error::x%.txt", 3, "****1111")
        line = sc.annotate("error", f)
        self.assertEqual(line.count("\n"), 0)
        self.assertTrue(
            line.startswith(
                "::error file=a%2Cb%3Ac%0A%3A%3Aerror%3A%3Ax%25.txt,line=3,"
            )
        )

    def test_usage_errors(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for args in ([], ["--all", "x"], ["--base", "a"]):
                with self.subTest(args=args), self.assertRaises(SystemExit) as cm:
                    sc.main(args)
                self.assertEqual(cm.exception.code, 2)


class TestGitDiffMode(unittest.TestCase):
    def git(self, root, *args):
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@example.com",
        )
        return subprocess.run(
            ["git", "-C", root, *args],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        ).stdout.strip()

    def test_only_changed_files_are_scanned(self):
        with TempRepo() as t:
            self.git(t.root, "init", "-q", "-b", "main")
            t.write("old.txt", "4111 1111 1111 1111\n")  # pre-existing, not in PR
            self.git(t.root, "add", "-A")
            self.git(t.root, "commit", "-q", "-m", "base")
            base = self.git(t.root, "rev-parse", "HEAD")
            t.write("new.md", "call +44 7911 123456\n")
            t.write("gone.txt", "temp\n")
            self.git(t.root, "add", "-A")
            self.git(t.root, "commit", "-q", "-m", "pr1")
            os.remove(os.path.join(t.root, "gone.txt"))
            self.git(t.root, "commit", "-q", "-am", "pr2")
            head = self.git(t.root, "rev-parse", "HEAD")

            self.assertEqual(sc.list_changed(t.root, base, head), ["new.md"])
            code, out = run_main(["--root", t.root, "--base", base, "--head", head])
            self.assertEqual(code, 1)
            self.assertIn("file=new.md", out)
            self.assertNotIn("old.txt", out)

            code, out = run_main(["--root", t.root, "--base", "0" * 40, "--head", head])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
