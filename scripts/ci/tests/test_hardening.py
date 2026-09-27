"""Tests for review round 1 hardening: blob reading, NUL/UTF-16 content, image
polyglots, extra invisible Unicode, WhatsApp variants, script-pattern variants,
cards/IBAN, LFS/submodules, inline-override limits, step summary, gitleaks
config/scan helpers, and tree export.

Run: python3 -m unittest discover scripts/ci/tests
Optional real-gitleaks test: set GITLEAKS_BIN and GITLEAKS_TEST_CONFIG
(a config built by `gitleaks_ci.py config`).
"""

import hashlib
import io
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import export_tree  # noqa: E402
import gitleaks_ci  # noqa: E402
import scan_content as sc  # noqa: E402
from test_scan_content import TempRepo, rules_in, run_main  # noqa: E402

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@example.com",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@example.com",
)


def git(root, *args, input=None):
    return (
        subprocess.run(
            ["git", "-C", root, *args],
            check=True,
            capture_output=True,
            env=GIT_ENV,
            input=input,
        )
        .stdout.decode()
        .strip()
    )


def _chunk(ctype, data):
    return (
        struct.pack(">I", len(data))
        + ctype
        + data
        + struct.pack(">I", zlib.crc32(ctype + data))
    )


def make_png(text_chunk=b""):
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0)
    body = _chunk(b"IHDR", ihdr)
    if text_chunk:
        body += _chunk(b"tEXt", b"c\x00" + text_chunk)
    body += _chunk(b"IDAT", zlib.compress(b"\x00\x00")) + _chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + body


JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF\x00" + b"\x01" * 20 + b"\xff\xd9"
GIF = (
    b"GIF89a"
    + b"\x01\x00\x01\x00\x00\x00\x00"
    + b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def zip_bytes():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hidden.txt", "payload")
    return buf.getvalue()


def init_repo(root):
    git(root, "init", "-q", "-b", "main")
    with open(os.path.join(root, "README"), "w") as fh:
        fh.write("base\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return git(root, "rev-parse", "HEAD")


class TestBlobReading(unittest.TestCase):
    def test_working_tree_encoding_attribute_can_not_hide_content(self):
        with TempRepo() as t:
            base = init_repo(t.root)
            t.write(".gitattributes", "*.md working-tree-encoding=UTF-16LE\n")
            leak = "ssn 078-05-1120\n+44 7911 123456\n"
            t.write(
                "leak.md", leak.encode("utf-16-le")
            )  # BOM-less, as git would check it out
            git(t.root, "add", "-A")
            git(t.root, "commit", "-q", "-m", "pr")
            head = git(t.root, "rev-parse", "HEAD")
            # Blob is stored as UTF-8 by git.
            blob = subprocess.run(
                ["git", "-C", t.root, "show", f"{head}:leak.md"], capture_output=True
            ).stdout
            self.assertEqual(blob, leak.encode())
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
            self.assertIn("title=us-ssn", out)
            self.assertIn("title=phone-number", out)
            # Working-tree scan also catches it (BOM-less UTF-16 sniffing).
            self.assertLessEqual({"us-ssn", "phone-number"}, t.rules("leak.md"))

    def test_head_blob_wins_over_working_tree(self):
        with TempRepo() as t:
            base = init_repo(t.root)
            t.write("a.md", "call +44 7911 123456\n")
            git(t.root, "add", "-A")
            git(t.root, "commit", "-q", "-m", "pr")
            head = git(t.root, "rev-parse", "HEAD")
            t.write("a.md", "clean now\n")  # uncommitted working-tree change
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
            self.assertEqual(code, 1)
            self.assertIn("file=a.md", out)
            code, out = run_main(
                ["--root", t.root, "--rev", head, "--allowlist", os.devnull]
            )
            self.assertEqual(code, 1)

    def test_rev_mode_symlink_and_gitlink(self):
        with TempRepo() as t:
            base = init_repo(t.root)
            os.symlink("/etc/passwd", os.path.join(t.root, "esc"))
            os.symlink("../../../etc/hosts", os.path.join(t.root, "esc2"))
            os.symlink("README", os.path.join(t.root, "ok"))
            git(
                t.root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{base},vendored",
            )
            git(t.root, "add", "esc", "esc2", "ok")
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
            self.assertEqual(code, 1)
            self.assertIn("file=esc,title=symlink-escape", out)
            self.assertIn("file=esc2,title=symlink-escape", out)
            self.assertNotIn("file=ok,", out)
            self.assertIn("file=vendored,title=submodule", out)
            self.assertEqual(sc.analyze(sc.Entry("x", "gitlink"))[0][0].rule, "submodule")
            # Working-tree --all mode also reports the gitlink from the index.
            code, out = run_main(["--root", t.root, "--all", "--allowlist", os.devnull])
            self.assertIn("file=vendored,title=submodule", out)

    def test_hostile_file_names(self):
        with TempRepo() as t:
            base = init_repo(t.root)
            for name in ("new\nline::error::x.md", "\u00fcn\u00efcode \u540d.md", "space name.md"):
                t.write(name, "ssn 078-05-1120\n")
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
            self.assertEqual(code, 1)
            self.assertEqual(out.count("title=us-ssn"), 3)
            self.assertIn("file=new%0Aline%3A%3Aerror%3A%3Ax.md", out)
            self.assertFalse(
                any(ln.startswith("::error::x") for ln in out.splitlines())
            )


class TestNulAndUtf16(unittest.TestCase):
    def test_nul_prefixed_text_still_fails(self):
        with TempRepo() as t:
            t.write("nul.md", b"\x00\nssn 123-45-6789\n+44 7911 123456\n")
            rules = t.rules("nul.md")
            self.assertLessEqual({"us-ssn", "phone-number", "binary-file"}, rules)

    def test_bomless_utf16_be_and_le(self):
        for enc in ("utf-16-le", "utf-16-be"):
            with self.subTest(enc=enc), TempRepo() as t:
                t.write("c.txt", "[12/31/24, 10:15:00 PM] Alice: hi\n".encode(enc))
                self.assertEqual(t.rules("c.txt"), {"whatsapp-export"})


class TestImages(unittest.TestCase):
    def test_valid_images_pass(self):
        with TempRepo() as t:
            for name, data in (("a.png", make_png()), ("a.jpg", JPEG), ("a.gif", GIF)):
                with self.subTest(name=name):
                    t.write(name, data)
                    self.assertEqual(t.rules(name), set())

    def test_trailing_data_is_polyglot(self):
        with TempRepo() as t:
            for name, data in (("a.png", make_png()), ("a.jpg", JPEG), ("a.gif", GIF)):
                with self.subTest(name=name):
                    t.write(name, data + b"<script>payload</script>")
                    self.assertIn("image-polyglot", t.rules(name))

    def test_zip_appended_to_image_and_markdown(self):
        z = zip_bytes()
        with TempRepo() as t:
            t.write("p.png", make_png() + z)
            self.assertLessEqual({"archive-file", "image-polyglot"}, t.rules("p.png"))
            t.write("p.md", b"# notes\n" + z)
            found = t.scan("p.md")[0]
            self.assertIn(
                ("archive-file", sc.FAIL), [(f.rule, f.severity) for f in found]
            )

    def test_text_named_as_image_is_scanned(self):
        with TempRepo() as t:
            t.write("chat.png", "[12/31/24, 10:15:00 PM] Alice: hi\nssn 078-05-1120\n")
            self.assertLessEqual({"whatsapp-export", "us-ssn"}, t.rules("chat.png"))

    def test_image_decoders_on_other_formats(self):
        webp = b"RIFF" + struct.pack("<I", 4) + b"WEBP"
        bmp = b"BM" + struct.pack("<I", 10) + b"\x00" * 4
        ico = (
            b"\x00\x00\x01\x00\x01\x00"
            + b"\x00" * 8
            + struct.pack("<II", 1, 22)
            + b"\x00"
        )
        for ext, data in ((".webp", webp), (".bmp", bmp), (".ico", ico)):
            with self.subTest(ext=ext):
                self.assertEqual(sc.check_image(ext, data)[0], "ok")
                self.assertEqual(sc.check_image(ext, data + b"extra")[0], "polyglot")


class TestInvisibleUnicode(unittest.TestCase):
    def test_new_invisible_characters_fail(self):
        for cp in (
            0x2061,
            0x2062,
            0x2063,
            0x2064,
            0x115F,
            0x1160,
            0x3164,
            0xFFA0,
            0x180E,
            0xE0100,
            0xE01EF,
        ):
            with self.subTest(cp=hex(cp)):
                self.assertIn("invisible-unicode", rules_in("a" + chr(cp) + "b"))

    def test_variation_selectors(self):
        self.assertIn("invisible-unicode", rules_in("a\ufe0fb"))  # after a letter
        self.assertIn("invisible-unicode", rules_in("\u2764\ufe0f\ufe0f"))  # run of two
        self.assertIn("invisible-unicode", rules_in("\ufe0f start"))
        for ok in ("\u2764\ufe0f", "1\ufe0f\u20e3", "\u00a9\ufe0f", "\u203c\ufe0f", "\U0001f3f3\ufe0f\u200d\U0001f308"):
            with self.subTest(ok=ok):
                self.assertEqual(rules_in(ok), set())

    def test_soft_hyphen_warns(self):
        found = sc.scan_text("a.md", "invis\u00adible")
        self.assertEqual(
            [(f.rule, f.severity) for f in found], [("soft-hyphen", sc.WARN)]
        )

    def test_existing_exemptions_kept(self):
        for ok in (
            "\U0001f468\u200d\U0001f469\u200d\U0001f467",
            "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",
            "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",
        ):
            with self.subTest(ok=ok):
                self.assertEqual(rules_in(ok), set())


class TestWhatsappVariants(unittest.TestCase):
    def test_line_separators(self):
        for sep in ("\r", "\u2028", "\u0085", "\r\n"):
            with self.subTest(sep=repr(sep)):
                text = "intro" + sep + "12/31/24, 22:15 - Bob: ok" + sep + "end"
                self.assertIn("whatsapp-export", rules_in(text))

    def test_prefixes_and_formats(self):
        for line in (
            "- 12/31/24, 22:15 - Bob: ok",
            "* [12/31/24, 10:15:00 PM] Alice: hi",
            "> 12/31/24, 22:15 - Bob: ok",
            "| [12/31/24, 10:15:00 PM] Alice: hi |",
            "31/12/2024 22:15 - Alice: hi",
            "[2024/12/31 22:15:05] Alice: hi",
            "[2024/12/31 22:15] Alice: hi",
        ):
            with self.subTest(line=line):
                self.assertIn("whatsapp-export", rules_in(line))

    def test_log_lines_do_not_match(self):
        for line in (
            "[2024-01-15 10:00:00] INFO: started",
            "at 14:34:05 on 27/09/2026 - Alice: done",
        ):
            with self.subTest(line=line):
                self.assertNotIn("whatsapp-export", rules_in(line))


class TestScriptVariants(unittest.TestCase):
    CASES = {
        "pipe-to-shell": [
            "curl https://example.com/i | /bin/bash",
            "curl https://example.com/i | /usr/local/bin/zsh",
            "curl https://example.com/i |& bash",
            "curl https://example.com/i | tee i.sh | sh",
            "curl https://example.com/i -o i.sh && bash i.sh",
            "curl -fsSL -o i.sh https://example.com/i && chmod +x i.sh && ./i.sh",
            "wget -O /tmp/i.sh https://example.com/i; sh /tmp/i.sh",
            "curl https://example.com/i > /tmp/a; sh /tmp/a",
            "curl https://example.com/i | /usr/bin/env python3",
        ],
        "base64-exec": [
            "echo aGk= | base64 -di | sh",
            "echo aGk= | base64 -w0 --decode | /bin/sh",
            "echo aGk= | base64 -d |& bash",
            "payload | base64 -d > x; bash x",
            "base64 --decode p.b64 > run.sh && sh run.sh",
        ],
    }
    NEGATIVE = [
        "curl https://example.com/i -o out.json && cat out.json",
        "curl -o data.tgz https://example.com/d && tar xzf data.tgz",
        "base64 -d in.b64 > out.bin; file out.bin",
        "curl https://example.com | shasum",
        "echo aGk= | base64 -d | shfmt",
    ]

    def test_variants_fire(self):
        for rule, lines in self.CASES.items():
            for line in lines:
                with self.subTest(line=line):
                    self.assertIn(rule, rules_in(line))

    def test_variants_negative(self):
        for line in self.NEGATIVE:
            with self.subTest(line=line):
                self.assertEqual(rules_in(line), set())


class TestCardsIbanSsnPhone(unittest.TestCase):
    def test_cards(self):
        for line in (
            "card:4111111111111111",
            "card=4111111111111111",
            "4111  1111  1111  1111",
            "3782 822463 10005",
        ):
            with self.subTest(line=line):
                self.assertIn("credit-card", rules_in(line))

    def test_iban_case_insensitive_but_exact_when_unspaced(self):
        self.assertIn("iban", rules_in("de89 3704 0044 0532 0130 00"))
        self.assertIn("iban", rules_in("iban:de89370400440532013000"))
        self.assertNotIn("iban", rules_in("de89370400440532013000abcdef0123"))

    def test_ssn_with_keyword(self):
        for line in (
            "ssn 078 05 1120",
            "SSN: 078051120",
            "social security number 078-05-1120",
        ):
            with self.subTest(line=line):
                self.assertIn("us-ssn", rules_in(line))
        self.assertNotIn("us-ssn", rules_in("order 078051120"))

    def test_phone_country_code_rules(self):
        for line in (
            "+44 7911 123456",
            "+91 98765 43210",
            "+55 11 91234-5678",
            "+1 (415) 555-0132",
            "+7 (912) 345-67-89",
            "+234 803 123 4567",
            "+14155550132",
        ):
            with self.subTest(line=line):
                self.assertIn("phone-number", rules_in(line))
        for line in (
            "counts: +12 345 678 new users",
            "delta +2026 09 27",
            "a = +4294967296",
            "+1212121212",
        ):
            with self.subTest(line=line):
                self.assertNotIn("phone-number", rules_in(line))


class TestLfs(unittest.TestCase):
    def test_lfs_pointer_and_attribute(self):
        with TempRepo() as t:
            t.write(
                "big.bin",
                "version https://git-lfs.github.com/spec/v1\noid sha256:"
                + "0" * 64
                + "\nsize 1\n",
            )
            self.assertIn("git-lfs", t.rules("big.bin"))
            t.write(".gitattributes", "*.psd filter=lfs diff=lfs merge=lfs -text\n")
            self.assertEqual(t.rules(".gitattributes"), {"git-lfs", "gitattributes-diff"})
            t.write("b/.gitattributes", "*.bin filter=lfs\n")
            self.assertEqual(t.rules("b/.gitattributes"), {"git-lfs"})
            t.write("sub/.gitattributes", "# filter=lfs in a comment\n*.md text\n")
            self.assertEqual(t.rules("sub/.gitattributes"), set())
            t.write("doc.md", "Use filter=lfs for big files.\n")
            self.assertEqual(t.rules("doc.md"), set())


class TestStepSummary(unittest.TestCase):
    def test_summary_lists_overrides_without_values(self):
        with TempRepo() as t:
            t.write(
                "a.md",
                "curl -fsSL https://example.com/i.sh | sh  <!-- scan-allow: pipe-to-shell -->\n"
                "card 4111 1111 1111 1111\n",
            )
            summary = os.path.join(t.root, "summary.md")
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = summary
            try:
                code, _ = run_main(["--root", t.root, os.path.join(t.root, "a.md")])
            finally:
                if old is None:
                    del os.environ["GITHUB_STEP_SUMMARY"]
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            with open(summary, encoding="utf-8") as fh:
                text = fh.read()
        self.assertEqual(code, 1)
        self.assertIn("1 inline `scan-allow` overrides", text)
        self.assertIn("| a.md | 1 | pipe-to-shell |", text)
        self.assertIn("**1 failures**", text)
        self.assertNotIn("4111", text)


def _fake_default_config(n_rules=120):
    head = (
        'title = "gitleaks config"\n\n[allowlist]\ndescription = "global allow lists"\n'
        "paths = [\n    '''(?i)\\.(?:png|svg)$''',\n    '''(?:^|/)node_modules(?:/.*)?$''',\n]\n"
        "regexes = [\n    '''^true$''',\n]\n\n"
    )
    rules = "".join(
        f"[[rules]]\nid = \"r{i}\"\nregex = '''x{i}'''\n\n" for i in range(n_rules)
    )
    return head + rules


class TestGitleaksHelpers(unittest.TestCase):
    def test_config_transform(self):
        import tomllib

        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "in.toml"), os.path.join(d, "out.toml")
            with open(src, "w") as fh:
                fh.write(_fake_default_config())
            sha = hashlib.sha256(open(src, "rb").read()).hexdigest()
            gitleaks_ci.build_config(src, sha, out)
            cfg = tomllib.loads(open(out).read())
            self.assertNotIn("paths", cfg["allowlist"])
            self.assertIn("regexes", cfg["allowlist"])
            self.assertEqual(len(cfg["rules"]), 120)
            with self.assertRaises(SystemExit):
                gitleaks_ci.build_config(src, "0" * 64, out)
            with open(src, "w") as fh:
                fh.write(_fake_default_config().replace("paths = [", "pathz = ["))
            sha = hashlib.sha256(open(src, "rb").read()).hexdigest()
            with self.assertRaises(SystemExit):
                gitleaks_ci.build_config(src, sha, out)

    def test_scan_removes_gitleaksignore_and_passes_hardening_flags(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "log.txt")
            fake = os.path.join(d, "fake-gitleaks")
            with open(fake, "w") as fh:
                fh.write(
                    "#!/usr/bin/env python3\nimport os,sys\n"
                    f"open({log!r},'w').write(repr((sys.argv[1:], os.path.exists(sys.argv[-1]+'/.gitleaksignore'),"
                    " sorted(k for k in os.environ if k.startswith('GITLEAKS_')))))\n"
                )
            os.chmod(fake, 0o755)
            src = os.path.join(d, "repo")
            os.makedirs(os.path.join(src, "sub"))
            subprocess.run(["git", "init", "-q", src], check=True)
            open(os.path.join(src, ".gitleaksignore"), "w").write("x:y:z:1\n")
            open(os.path.join(src, "sub", ".gitleaksignore"), "w").write("x:y:z:1\n")
            os.environ["GITLEAKS_CONFIG"] = "/evil.toml"
            try:
                rc = gitleaks_ci.scan(fake, "/cfg.toml", "a..b", source=src)
            finally:
                del os.environ["GITLEAKS_CONFIG"]
            args, ignore_present, env_keys = eval(open(log).read())
            self.assertFalse(os.path.exists(os.path.join(src, "sub", ".gitleaksignore")))
            with open(os.path.join(src, ".git", "info", "attributes")) as fh:
                self.assertEqual(fh.read(), gitleaks_ci.FORCED_ATTRIBUTES)
            with self.assertRaises(SystemExit):
                gitleaks_ci.scan(fake, "/cfg.toml", "a..b; rm -rf /", source=src)
        self.assertEqual(rc, 0)
        self.assertFalse(ignore_present)
        self.assertEqual(env_keys, [])
        for flag in ("--redact", "--ignore-gitleaks-allow", "--log-opts=--text --diff-merges=first-parent a..b"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--config") + 1], "/cfg.toml")

    @unittest.skipUnless(
        os.environ.get("GITLEAKS_BIN") and os.environ.get("GITLEAKS_TEST_CONFIG"),
        "set GITLEAKS_BIN and GITLEAKS_TEST_CONFIG to run real gitleaks",
    )
    def test_real_gitleaks_selftest(self):
        self.assertEqual(
            gitleaks_ci.selftest(
                os.environ["GITLEAKS_BIN"], os.environ["GITLEAKS_TEST_CONFIG"]
            ),
            0,
        )


class TestExportTree(unittest.TestCase):
    def test_exports_committed_bytes_only(self):
        with TempRepo() as t, tempfile.TemporaryDirectory() as dest:
            init_repo(t.root)
            t.write(
                ".gitattributes",
                "*.md working-tree-encoding=UTF-16LE\n*.txt export-ignore\n",
            )
            t.write("a.md", "hello\n".encode("utf-16-le"))
            t.write("hidden.txt", "still exported\n")
            os.symlink("/etc/passwd", os.path.join(t.root, "esc"))
            git(t.root, "add", "-A")
            git(t.root, "commit", "-q", "-m", "c")
            n = export_tree.export(t.root, "HEAD", dest)
            self.assertEqual(n, 4)  # README, .gitattributes, a.md, hidden.txt
            self.assertEqual(open(os.path.join(dest, "a.md"), "rb").read(), b"hello\n")
            self.assertTrue(os.path.exists(os.path.join(dest, "hidden.txt")))
            self.assertFalse(os.path.lexists(os.path.join(dest, "esc")))


if __name__ == "__main__":
    unittest.main()
