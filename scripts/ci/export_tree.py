#!/usr/bin/env python3
"""Write the raw blobs of a commit into a directory (stdlib only).

Usage: export_tree.py REV DEST

Used by the `malware` job so ClamAV scans the committed bytes. A checkout or
`git archive` applies .gitattributes (working-tree-encoding, eol, export-ignore),
which a PR controls and could use to change or hide content. Symlinks and
submodules are not written (pii-and-content reports them).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scan_content import _cat_blobs, _dec, _git  # noqa: E402


def export(root: str, rev: str, dest: str, chunk: int = 256) -> int:
    out = _git(root, "ls-tree", "-r", "-z", "--full-tree", rev)
    todo: list[tuple[str, str]] = []
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, raw_path = rec.split(b"\t", 1)
        mode, typ, sha = meta.decode().split()
        if typ == "blob" and mode in ("100644", "100755"):
            todo.append((_dec(raw_path), sha))
    dest_real = os.path.realpath(dest)
    n = 0
    for k in range(0, len(todo), chunk):
        part = todo[k : k + chunk]
        blobs = _cat_blobs(root, [sha for _, sha in part])
        for path, sha in part:
            target = os.path.realpath(os.path.join(dest_real, path))
            if os.path.commonpath([target, dest_real]) != dest_real:
                raise SystemExit(f"refusing path outside destination: {path!r}")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(blobs[sha])
            n += 1
    return n


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    rev, dest = argv
    os.makedirs(dest, exist_ok=True)
    root = _git(os.getcwd(), "rev-parse", "--show-toplevel").decode().strip()
    n = export(root, rev, dest)
    print(f"exported {n} blobs from {rev} to {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
