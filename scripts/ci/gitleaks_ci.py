#!/usr/bin/env python3
"""gitleaks helpers for the `secrets` CI job (stdlib only).

Subcommands:
  config SOURCE SHA256 OUT   Verify the upstream default gitleaks.toml by sha256,
                             remove every allowlist `paths` entry (global and
                             per rule; they skip .svg, .pdf, lock files,
                             node_modules, vendor/, @octokit/..., ...), write OUT.
  scan GITLEAKS CONFIG RANGE Scan git history of the current directory. Neutralises
                             PR-controlled inputs first (see scan()).
  selftest GITLEAKS CONFIG   Build a throwaway repo that uses every known bypass
                             and fail unless `scan` reports every planted token.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
import tempfile
import tomllib

# Allowlist keys that still suppress something once `paths` is gone.
ALLOWLIST_EFFECTIVE_KEYS = ("regexes", "stopwords", "commits")


def _strip_one(al: dict) -> dict | None:
    """Allowlist without `paths`, or None if it should be dropped.

    With condition AND, `paths` limited where the other criteria applied, so
    dropping only `paths` would widen the allowlist: drop the whole allowlist.
    Also drop it when nothing effective (regexes / stopwords / commits) is left.
    """
    if "paths" in al and str(al.get("condition", "OR")).upper() == "AND":
        return None
    al = {k: v for k, v in al.items() if k != "paths"}
    if not any(k in al for k in ALLOWLIST_EFFECTIVE_KEYS):
        return None
    return al


def strip_path_allowlists(cfg: dict) -> dict:
    """Remove path-based allowlisting from the global and every rule allowlist."""
    if isinstance(cfg.get("allowlist"), dict):
        g = _strip_one(cfg["allowlist"])
        if g is None:
            del cfg["allowlist"]
        else:
            cfg["allowlist"] = g
    for holder in [cfg] + list(cfg.get("rules", [])):
        if holder is not cfg and isinstance(holder.get("allowlist"), dict):
            one = _strip_one(holder["allowlist"])
            if one is None:
                del holder["allowlist"]
            else:
                holder["allowlist"] = one
        if isinstance(holder.get("allowlists"), list):
            kept = [x for x in (_strip_one(a) for a in holder["allowlists"]) if x is not None]
            if kept:
                holder["allowlists"] = kept
            else:
                del holder["allowlists"]
    return cfg


def _has_paths(obj) -> bool:
    if isinstance(obj, dict):
        return "paths" in obj or any(_has_paths(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_paths(v) for v in obj)
    return False


# -- minimal TOML writer (enough for gitleaks configs: str/int/float/bool/lists/tables)


def _toml_key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _toml_str(k)


def _toml_str(s: str) -> str:
    out = json.dumps(s, ensure_ascii=False)
    return out.replace("\x7f", "\\u007F")


def _toml_val(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return _toml_str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_toml_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return (
            "{"
            + ", ".join(f"{_toml_key(k)} = {_toml_val(x)}" for k, x in v.items())
            + "}"
        )
    raise TypeError(f"unsupported TOML value {type(v)}")


def _is_table_array(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, dict) for x in v)


def dump_toml(d: dict, prefix: str = "") -> str:
    lines: list[str] = []
    for k, v in d.items():
        if not isinstance(v, dict) and not _is_table_array(v):
            lines.append(f"{_toml_key(k)} = {_toml_val(v)}")
    for k, v in d.items():
        name = prefix + _toml_key(k)
        if isinstance(v, dict):
            lines += ["", f"[{name}]", dump_toml(v, name + ".")]
        elif _is_table_array(v):
            for item in v:
                lines += ["", f"[[{name}]]", dump_toml(item, name + ".")]
    return "\n".join(x for x in lines if x is not None)


def build_config(source: str, expected_sha256: str, out: str) -> None:
    with open(source, "rb") as fh:
        raw = fh.read()
    got = hashlib.sha256(raw).hexdigest()
    if got != expected_sha256.lower():
        raise SystemExit(
            f"gitleaks config sha256 mismatch: got {got}, want {expected_sha256}"
        )
    cfg = tomllib.loads(raw.decode("utf-8"))
    n_rules = len(cfg.get("rules", []))
    if n_rules < 100:
        raise SystemExit("source config has too few rules")
    if "paths" not in cfg.get("allowlist", {}):
        raise SystemExit(
            "source config has no global allowlist paths; upstream format changed, review"
        )
    strip_path_allowlists(cfg)
    text = dump_toml(cfg) + "\n"
    again = tomllib.loads(text)  # round-trip check of the writer
    if again != cfg or _has_paths(again) or len(again["rules"]) != n_rules:
        raise SystemExit("transformed config failed round-trip / no-paths check")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)


# -- scanning

# Highest-precedence attributes: force a text diff for every path so a PR
# .gitattributes (`-diff`, `binary`, `diff=driver`, filters) can not hide content.
FORCED_ATTRIBUTES = "* diff -filter -text -working-tree-encoding\n"
# --text: never "Binary files differ"; first-parent: show what a merge commit adds.
LOG_OPTS_PREFIX = "--text --diff-merges=first-parent"


def _git_path(source: str, rel: str) -> str:
    out = subprocess.run(
        ["git", "-C", source, "rev-parse", "--git-path", rel],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return out if os.path.isabs(out) else os.path.join(source, out)


def _remove(path: str) -> None:
    if os.path.islink(path) or os.path.isfile(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def scan(
    gitleaks: str,
    config: str,
    rev_range: str,
    source: str = ".",
    report: str | None = None,
) -> int:
    """Run gitleaks over `rev_range` with PR-controlled inputs neutralised.

    - `.gitleaksignore` anywhere in the working tree is deleted (gitleaks reads
      <source>/.gitleaksignore even when --gitleaks-ignore-path points elsewhere).
    - .git/info/attributes forces text diffs; --text in the log options.
    - --diff-merges=first-parent so content added in a merge commit is scanned.
    - --config (repo .gitleaks.toml ignored), --ignore-gitleaks-allow, GITLEAKS_* env cleared.
    """
    if not re.fullmatch(r"[0-9A-Za-z_./^~-]+(\.\.[0-9A-Za-z_./^~-]+)?", rev_range):
        raise SystemExit(f"unexpected revision range: {rev_range!r}")
    for dirpath, dirnames, filenames in os.walk(source):
        if ".git" in dirnames:
            dirnames.remove(".git")
        for name in list(dirnames) + filenames:
            if name == ".gitleaksignore":
                _remove(os.path.join(dirpath, name))
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        if ".gitleaksignore" in filenames or ".gitleaksignore" in dirnames:
            raise SystemExit(".gitleaksignore still present")
    attrs = _git_path(source, "info/attributes")
    os.makedirs(os.path.dirname(attrs), exist_ok=True)
    with open(attrs, "w") as fh:
        fh.write(FORCED_ATTRIBUTES)
    empty = tempfile.mkdtemp(prefix="gitleaks-empty-ignore-")
    cmd = [
        gitleaks,
        "git",
        "--no-banner",
        "--redact",
        "--verbose",
        "--no-color",
        "--config",
        config,
        "--gitleaks-ignore-path",
        empty,
        "--ignore-gitleaks-allow",
        f"--log-opts={LOG_OPTS_PREFIX} {rev_range}",
    ]
    if report:
        cmd += ["--report-format", "json", "--report-path", report]
    cmd.append(source)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GITLEAKS_")}
    return subprocess.run(cmd, env=env).returncode


# -- self-test


def _fake_token() -> str:
    alphabet = string.ascii_letters + string.digits
    return "ghp_" + "".join(secrets.choice(alphabet) for _ in range(36))


# Paths skipped by the upstream global or rule-level path allowlists.
SELFTEST_PATHS = [
    "logo.svg",
    "notes.pdf",
    "package-lock.json",
    "node_modules/x/index.js",
    "x/@octokit/auth-token/README.md",
    "docs/a.md",
    "vendor/github.com/x/y/z.go",
]
MERGE_PATH = "merge-only.txt"


def selftest(gitleaks: str, config: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        env = dict(
            os.environ,
            GIT_AUTHOR_NAME="t",
            GIT_AUTHOR_EMAIL="t@example.com",
            GIT_COMMITTER_NAME="t",
            GIT_COMMITTER_EMAIL="t@example.com",
        )

        def git(*a: str) -> str:
            return subprocess.run(
                ["git", "-C", repo, *a],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            ).stdout.strip()

        def write(rel: str, text: str) -> None:
            full = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write(text)

        git("init", "-q", "-b", "main")
        write("README", "base\n")
        git("add", "-A")
        git("commit", "-q", "-m", "base")
        base = git("rev-parse", "HEAD")
        git("checkout", "-q", "-b", "side")
        write("side.txt", "side\n")
        git("add", "-A")
        git("commit", "-q", "-m", "side")
        git("checkout", "-q", "main")
        # Bypass 1: attributes that turn every diff into "Binary files differ".
        write(".gitattributes", "* -diff\n*.md binary\n*.json diff=nothing\n")
        for p in SELFTEST_PATHS:
            write(p, f'token = "{_fake_token()}"  // gitleaks:allow\n')
        git("add", "-A")
        git("commit", "-q", "-m", "add")
        # Bypass 2: a token that only a merge commit introduces.
        git("merge", "-q", "--no-ff", "--no-commit", "side")
        write(MERGE_PATH, f'token = "{_fake_token()}"\n')
        git("add", "-A")
        git("commit", "-q", "-m", "merge side")
        head = git("rev-parse", "HEAD")
        # Bypass 3: ignore files (root, sub-dir, symlink) and a repo config.
        lines = "".join(
            f"{c}:{p}:github-pat:1\n"
            for c in (git("rev-parse", "HEAD~1"), head)
            for p in SELFTEST_PATHS + [MERGE_PATH]
        )
        write("ign.txt", lines)
        write("sub/.gitleaksignore", lines)
        os.symlink("ign.txt", os.path.join(repo, ".gitleaksignore"))
        write(
            ".gitleaks.toml",
            "[extend]\nuseDefault = true\n[allowlist]\npaths = ['''.*''']\n",
        )
        report = os.path.join(tmp, "report.json")
        rc = scan(gitleaks, config, f"{base}..{head}", source=repo, report=report)
        with open(report) as fh:
            found = {f["File"] for f in json.load(fh) if f.get("RuleID") == "github-pat"}
    want = set(SELFTEST_PATHS) | {MERGE_PATH}
    missing = sorted(want - found)
    if rc != 1 or missing:
        print(f"::error::gitleaks self-test failed: exit {rc}, not reported: {missing}")
        return 1
    print(f"gitleaks self-test OK: all {len(want)} planted tokens reported")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 4 and argv[0] == "config":
        build_config(argv[1], argv[2], argv[3])
        return 0
    if len(argv) == 4 and argv[0] == "scan":
        return scan(argv[1], argv[2], argv[3])
    if len(argv) == 3 and argv[0] == "selftest":
        return selftest(argv[1], argv[2])
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
