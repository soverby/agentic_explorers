#!/usr/bin/env python3
"""PII, binary, hidden-Unicode, and high-risk-pattern scanner for PRs.

Stdlib only. Emits GitHub workflow annotations. Never prints a matched value
in full: every value is masked to its last 2-4 characters, because the logs of
a public repository are public.

Usage:
    scan_content.py --all                     # working tree: tracked + untracked-not-ignored
    scan_content.py --rev REV                 # every file in commit REV (read from git blobs)
    scan_content.py --base SHA --head SHA     # files changed in base...head (read from head blobs)
    scan_content.py PATH [PATH ...]           # explicit working-tree files

--rev and --base/--head read file content from git objects, not from the
working tree, so .gitattributes filters or encodings can not change what is
scanned.

Exit codes: 0 no FAIL findings, 1 at least one FAIL, 2 usage/config error.
Rule ids and override syntax: see scripts/ci/README.md.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import posixpath
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass

FAIL = "error"
WARN = "warning"

MAX_BYTES = 5 * 1024 * 1024
MAX_SCRIPT_LINE = 4096  # longer lines skip the bounded (slow) script regexes; see LONG_LINE_RISK
ALLOWLIST_DEFAULT = ".github/scan-allowlist.txt"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    description: str


RULES = {
    r.id: r
    for r in [
        Rule("phone-number", FAIL, "phone number"),
        Rule("us-ssn", FAIL, "US social security number"),
        Rule("credit-card", FAIL, "payment card number (Luhn valid)"),
        Rule("iban", FAIL, "IBAN (mod-97 valid)"),
        Rule("private-key", FAIL, "private key header"),
        Rule("whatsapp-export", FAIL, "WhatsApp chat export line"),
        Rule("executable-binary", FAIL, "executable binary"),
        Rule(
            "archive-file",
            FAIL,
            "archive file or embedded archive (hides contents from review)",
        ),
        Rule(
            "image-polyglot",
            FAIL,
            "image with data after its end marker, or malformed image",
        ),
        Rule("large-file", FAIL, "file larger than 5 MB"),
        Rule("symlink-escape", FAIL, "symlink that points outside the repository"),
        Rule("submodule", FAIL, "git submodule (gitlink) entry"),
        Rule("gitattributes-diff", FAIL, ".gitattributes that hides diffs (-diff, binary, diff=, -text)"),
        Rule(
            "git-lfs",
            FAIL,
            "Git LFS pointer or filter=lfs attribute (content not reviewable)",
        ),
        Rule("bidi-unicode", FAIL, "bidirectional control character (Trojan Source)"),
        Rule("zero-width-unicode", FAIL, "zero-width character"),
        Rule(
            "invisible-unicode",
            FAIL,
            "invisible / filler / variation-selector character",
        ),
        Rule("unicode-tag", FAIL, "Unicode tag character (invisible ASCII smuggling)"),
        Rule("reverse-shell", FAIL, "reverse shell pattern"),
        Rule(
            "pipe-to-shell",
            FAIL,
            "downloaded content executed by a shell or interpreter",
        ),
        Rule("base64-exec", FAIL, "base64-decoded content executed"),
        Rule("crypto-miner", FAIL, "crypto-miner indicator"),
        Rule("credential-exfil", FAIL, "credential file sent over the network"),
        Rule("email-address", WARN, "email address"),
        Rule("prompt-injection", WARN, "prompt-injection phrase"),
        Rule("svg-script", WARN, "script or event handler in SVG"),
        Rule("soft-hyphen", WARN, "soft hyphen U+00AD (invisible when rendered)"),
        Rule("long-line", FAIL, "line longer than 4096 characters with a shell/network/decode keyword: split the line"),
        Rule("binary-file", WARN, "binary file (review manually)"),
    ]
}

# Only these rules accept an inline `scan-allow:` comment. PII, secrets,
# binaries, hidden Unicode, LFS and submodules can only be skipped by path in
# .github/scan-allowlist.txt, which the maintainer reviews.
INLINE_OVERRIDABLE = frozenset(
    {
        "reverse-shell",
        "pipe-to-shell",
        "base64-exec",
        "crypto-miner",
        "email-address",
        "prompt-injection",
        "svg-script",
        "soft-hyphen",
    }
)

# --------------------------------------------------------------------------
# File-type classification
# --------------------------------------------------------------------------

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".avif"}
EXEC_EXT_RE = re.compile(r"\.(?:exe|dll|dylib|msi|scr|wasm|so(?:\.\d+)*)$", re.I)
ARCHIVE_EXT_RE = re.compile(
    r"\.(?:zip|tar|tgz|tbz2?|txz|gz|bz2|xz|zst|7z|rar|jar|war|ear|whl|apk|dmg|iso|cab|deb|rpm)$",
    re.I,
)
PROMPT_EXTS = {".md", ".mdx", ".markdown"}
LFS_POINTER = b"version https://git-lfs.github.com/spec/"

EXEC_MAGIC = [
    (b"\x7fELF", "ELF"),
    (b"\xfe\xed\xfa\xce", "Mach-O"),
    (b"\xfe\xed\xfa\xcf", "Mach-O"),
    (b"\xce\xfa\xed\xfe", "Mach-O"),
    (b"\xcf\xfa\xed\xfe", "Mach-O"),
    (b"\xca\xfe\xba\xbe", "Mach-O fat / Java class"),
    (b"\x00asm", "WebAssembly"),
]
ARCHIVE_MAGIC = [
    (b"PK\x03\x04", "zip"),
    (b"PK\x05\x06", "zip"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
    (b"Rar!\x1a\x07", "rar"),
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bzip2"),
    (b"\xfd7zXZ\x00", "xz"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"MSCF", "cab"),
    (b"!<arch>\n", "ar / deb"),
    (b"\xed\xab\xee\xdb", "rpm"),
]
# Archive signatures that are searched for anywhere in a file (polyglots).
EMBEDDED_ARCHIVE = [
    (b"PK\x03\x04", "zip local header"),
    (b"PK\x05\x06", "zip end-of-directory"),
    (b"Rar!\x1a\x07", "rar"),
    (b"7z\xbc\xaf\x27\x1c", "7z"),
]


def classify_binary(head: bytes) -> tuple[str | None, str | None]:
    """Return (rule_id, kind) for executable/archive magic bytes, else (None, None)."""
    for magic, kind in EXEC_MAGIC:
        if head.startswith(magic):
            return "executable-binary", kind
    if head.startswith(b"MZ") and b"\x00" in head[:1024]:
        return "executable-binary", "PE (MZ)"
    for magic, kind in ARCHIVE_MAGIC:
        if head.startswith(magic):
            # BZh magic is short; require binary content to avoid text FPs.
            if kind == "bzip2" and b"\x00" not in head[:1024]:
                continue
            return "archive-file", kind
    if len(head) >= 262 and head[257:262] == b"ustar":
        return "archive-file", "tar"
    return None, None


def _u32be(b: bytes, i: int) -> int:
    return int.from_bytes(b[i : i + 4], "big")


def _u32le(b: bytes, i: int) -> int:
    return int.from_bytes(b[i : i + 4], "little")


def _image_end(ext: str, d: bytes) -> int | None:
    """Offset where the image data ends, -1 if malformed, None if magic does not match ext."""
    n = len(d)
    if ext == ".png":
        if not d.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        pos = 8
        while pos + 12 <= n:
            length = _u32be(d, pos)
            ctype = d[pos + 4 : pos + 8]
            pos += 12 + length
            if ctype == b"IEND":
                return pos if pos <= n else -1
        return -1
    if ext in (".jpg", ".jpeg"):
        if not d.startswith(b"\xff\xd8\xff"):
            return None
        i = d.rfind(b"\xff\xd9")
        return i + 2 if i >= 0 else -1
    if ext == ".gif":
        if not d.startswith((b"GIF87a", b"GIF89a")):
            return None
        i = d.rfind(b";")
        return i + 1 if i >= 0 else -1
    if ext == ".webp":
        if not (d.startswith(b"RIFF") and d[8:12] == b"WEBP"):
            return None
        end = _u32le(d, 4) + 8
        end += end % 2
        return end if end <= n else -1
    if ext == ".bmp":
        if not d.startswith(b"BM"):
            return None
        end = _u32le(d, 2)
        return end if 0 < end <= n else -1
    if ext == ".ico":
        if not d.startswith(b"\x00\x00\x01\x00") or n < 6:
            return None
        count = int.from_bytes(d[4:6], "little")
        end = 6 + 16 * count
        for k in range(count):
            e = 6 + 16 * k
            if e + 16 > n:
                return -1
            end = max(end, _u32le(d, e + 12) + _u32le(d, e + 8))
        return end if end <= n else -1
    if ext == ".avif":
        if d[4:8] != b"ftyp" or d[8:12] not in (b"avif", b"avis", b"mif1"):
            return None
        pos = 0
        while pos + 8 <= n:
            size = _u32be(d, pos)
            if size == 1:
                size = int.from_bytes(d[pos + 8 : pos + 16], "big")
            elif size == 0:
                return n
            if size < 8:
                return -1
            pos += size
        return pos if pos == n else -1
    return None


def check_image(ext: str, data: bytes) -> tuple[str, str]:
    """('ok'|'mismatch'|'polyglot', detail)."""
    end = _image_end(ext, data)
    if end is None:
        return "mismatch", ""
    if end < 0:
        return "polyglot", "malformed image structure"
    trailing = data[end:]
    if trailing.strip(b"\x00"):
        return "polyglot", f"{len(trailing)} bytes after image end marker"
    return "ok", ""


# --------------------------------------------------------------------------
# PII detectors
# --------------------------------------------------------------------------

# ITU country calling codes. 1- and 2-digit codes exactly; 3-digit codes as a set.
_CC1 = {"1", "7"}
_CC2 = set(
    "20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 "
    "60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98".split()
)


def _cc3() -> set[str]:
    out = {"211", "212", "213", "216", "218", "290", "291", "297", "298", "299"}
    out |= {str(x) for x in range(220, 259)} | {str(x) for x in range(260, 270)}
    out |= {str(x) for x in range(350, 360)} | {str(x) for x in range(370, 390)}
    out |= {"420", "421", "423", "800", "808"}
    out |= {str(x) for x in range(500, 510)} | {str(x) for x in range(590, 600)}
    out |= {str(x) for x in range(670, 693)}
    out |= {
        "850",
        "852",
        "853",
        "855",
        "856",
        "870",
        "878",
        "880",
        "881",
        "882",
        "883",
        "886",
        "888",
    }
    out |= {str(x) for x in range(960, 969)} | {str(x) for x in range(970, 980)}
    out |= {str(x) for x in range(992, 999)}
    return out


_CC3 = _cc3()


def country_code(digits: str) -> str | None:
    if digits[:1] in _CC1:
        return digits[:1]
    if digits[:2] in _CC2:
        return digits[:2]
    if digits[:3] in _CC3:
        return digits[:3]
    return None


# International: leading "+", country code, digits with optional separators.
PHONE_INTL_RE = re.compile(r"(?<![\w+])\+\(?\d[\d \-.()]{6,22}\d(?![\w])")
# North American: (NXX) NXX-XXXX or NXX-NXX-XXXX with one consistent "-" or "."
# separator. Space-separated digit groups are not matched: too many number lists.
PHONE_NANP_RE = re.compile(
    r"(?<![\w+\-.])(?:\([2-9]\d{2}\)\s?[2-9]\d{2}[\-.]\d{4}"
    r"|[2-9]\d{2}([\-.])[2-9]\d{2}\1\d{4})(?![\w\-.]*\d)"
)


def _valid_intl_phone(s: str) -> bool:
    if s.count("(") != s.count(")"):
        return False
    if re.search(r"[ \-.]{2,}", s):
        return False
    groups = re.findall(r"\d+", s)
    digits = "".join(groups)
    if not 8 <= len(digits) <= 15:
        return False
    cc = country_code(digits)
    if cc is None:
        return False
    if len(groups) > 1 and groups[0] != cc:
        return False  # "+12 345 678", "+2026 09 27": first group is not a country code
    if cc in ("1", "7"):
        if len(digits) != 11:
            return False
        if cc == "1" and (digits[1] in "01" or digits[4] in "01"):
            return False
    seps = set(re.findall(r"[ \-.]", s))
    if "." in seps:
        # Dotted form must look like +CC.XXX.XXX.XXXX, not a decimal number.
        if len(seps) > 1 or len(groups) < 3 or any(len(g) < 2 for g in groups[1:]):
            return False
    return True


_DATE_IN_PHONE = re.compile(r"(?<!\d)(?:19|20)\d\d[-/. ](?:0?[1-9]|1[0-2])[-/. ](?:0?[1-9]|[12]\d|3[01])(?!\d)")


def find_phones(line: str):
    for m in PHONE_INTL_RE.finditer(line):
        s = m.group(0).rstrip(" -.(")
        if not _valid_intl_phone(s):
            continue
        # "+1 2026-09-27 14:34:05": a date, or a time right after the match.
        if _DATE_IN_PHONE.search(s) or re.match(r":\d\d", line[m.start() + len(s) :]):
            continue
        yield m.start(), s
    for m in PHONE_NANP_RE.finditer(line):
        yield m.start(), m.group(0)


SSN_RE = re.compile(
    r"(?<![\w\-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\w\-])"
)
# Undashed / space-separated SSNs only next to an explicit keyword.
SSN_KEYWORD_RE = re.compile(
    r"\b(?:ssn|social\s+security(?:\s+(?:number|no\.?|#))?)\b\W{0,3}"
    r"(?!000|666|9\d\d)(\d{3})[ ]?(?!00)(\d{2})[ ]?(?!0000)(\d{4})(?![\w\-])",
    re.I,
)

_SEP = r"(?:[ \t]+|-)"
CARD_RE = re.compile(
    r"(?<![\w\-.+/])(?:"
    r"\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{4}" + _SEP + r"\d{1,7}"  # 4-4-4-4(-3)
    r"|\d{4}" + _SEP + r"\d{6}" + _SEP + r"\d{4,5}"  # Amex / Diners 4-6-5, 4-6-4
    r"|\d{13,19}"
    r")(?![\w\-])"
)


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def card_brand_ok(d: str) -> bool:
    n = len(d)
    p2, p3, p4, p6 = int(d[:2]), int(d[:3]), int(d[:4]), int(d[:6])
    if d[0] == "4":
        return n in (13, 16, 19)
    if 51 <= p2 <= 55 or 222100 <= p6 <= 272099:
        return n == 16
    if p2 in (34, 37):
        return n == 15
    if p4 == 6011 or p2 == 65 or 644 <= p3 <= 649:
        return 16 <= n <= 19
    if 3528 <= p4 <= 3589:
        return 16 <= n <= 19
    if p2 in (36, 38) or 300 <= p3 <= 305:
        return 14 <= n <= 19
    if p2 == 62:
        return 16 <= n <= 19
    return False


def find_cards(line: str):
    for m in CARD_RE.finditer(line):
        d = re.sub(r"\D", "", m.group(0))
        if 13 <= len(d) <= 19 and card_brand_ok(d) and luhn_ok(d):
            yield m.start(), d


IBAN_LENGTHS = {
    "AD": 24,
    "AE": 23,
    "AL": 28,
    "AT": 20,
    "AZ": 28,
    "BA": 20,
    "BE": 16,
    "BG": 22,
    "BH": 22,
    "BR": 29,
    "BY": 28,
    "CH": 21,
    "CR": 22,
    "CY": 28,
    "CZ": 24,
    "DE": 22,
    "DK": 18,
    "DO": 28,
    "EE": 20,
    "EG": 29,
    "ES": 24,
    "FI": 18,
    "FO": 18,
    "FR": 27,
    "GB": 22,
    "GE": 22,
    "GI": 23,
    "GL": 18,
    "GR": 27,
    "GT": 28,
    "HR": 21,
    "HU": 28,
    "IE": 22,
    "IL": 23,
    "IQ": 23,
    "IS": 26,
    "IT": 27,
    "JO": 30,
    "KW": 30,
    "KZ": 20,
    "LB": 28,
    "LC": 32,
    "LI": 21,
    "LT": 20,
    "LU": 20,
    "LV": 21,
    "MC": 27,
    "MD": 24,
    "ME": 22,
    "MK": 19,
    "MR": 27,
    "MT": 31,
    "MU": 30,
    "NL": 18,
    "NO": 15,
    "PK": 24,
    "PL": 28,
    "PS": 29,
    "PT": 25,
    "QA": 29,
    "RO": 24,
    "RS": 22,
    "SA": 24,
    "SC": 31,
    "SE": 24,
    "SI": 19,
    "SK": 24,
    "SM": 27,
    "ST": 25,
    "SV": 28,
    "TL": 23,
    "TN": 24,
    "TR": 26,
    "UA": 29,
    "VA": 22,
    "VG": 24,
    "XK": 20,
}
IBAN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}(?: ?[A-Za-z0-9]){11,30}(?![A-Za-z0-9])"
)


def iban_ok(s: str) -> bool:
    rearranged = s[4:] + s[:4]
    num = "".join(str(int(c, 36)) for c in rearranged)
    return int(num) % 97 == 1


def find_ibans(line: str):
    for m in IBAN_RE.finditer(line):
        raw = m.group(0)
        compact = raw.replace(" ", "").upper()
        want = IBAN_LENGTHS.get(compact[:2])
        if not want or len(compact) < want:
            continue
        if " " not in raw and len(compact) != want:
            continue  # unspaced: whole token must be the IBAN (avoids hex ids)
        if iban_ok(compact[:want]):
            yield m.start(), compact[:want]


PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----")

_WA_TIME = r"\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\s?[AaPp]\.?\s?[Mm]\.?)?"
_WA_DATE_SLASH = r"\d{1,4}[/.]\d{1,2}[/.]\d{1,4}"
_WA_DATE_ANY = r"\d{1,4}[/.\-]\d{1,2}[/.\-]\d{1,4}"
# Comma is optional for / and . dates; ISO-style dashed dates need the comma so
# that ordinary log lines "[2024-01-15 10:00:00] INFO:" do not match.
_WA_DT = r"(?:" + _WA_DATE_SLASH + r",?|" + _WA_DATE_ANY + r",)\s+" + _WA_TIME
_WA_PRE = r"^\s*(?:[-*|>]\s*)*[\u200e\u200f]?"
WHATSAPP_RES = [
    # iOS:     [12/31/24, 10:15:00 PM] Name: text   /   [2024/12/31 22:15] Name: text
    re.compile(_WA_PRE + r"\[" + _WA_DT + r"\]\s?[^:\n]{1,80}:(?:\s|$)"),
    # Android: 12/31/24, 22:15 - Name: text
    re.compile(_WA_PRE + _WA_DT + r"\s?[-\u2013]\s[^:\n]{1,80}:(?:\s|$)"),
    # System line present in every export.
    re.compile(
        _WA_PRE + r"\[?" + _WA_DT + r"\]?\s?(?:[-\u2013]\s)?.{0,300}?end-to-end encrypted", re.I
    ),
]

EMAIL_RE = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"
)
EMAIL_ALLOWED_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "users.noreply.github.com",
    "localhost",
}
EMAIL_ALLOWED_SUFFIXES = (
    ".example",
    ".test",
    ".invalid",
    ".localhost",
    ".users.noreply.github.com",
)
EMAIL_ALLOWED_LOCAL = {"noreply", "no-reply", "donotreply", "do-not-reply"}
EMAIL_GIT_HOSTS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "ssh.dev.azure.com",
    "codeberg.org",
}
EMAIL_FILE_TLDS = {"png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "avif"}


def email_allowed(addr: str) -> bool:
    local, _, domain = addr.lower().rpartition("@")
    if domain in EMAIL_ALLOWED_DOMAINS or domain.endswith(EMAIL_ALLOWED_SUFFIXES):
        return True
    if local in EMAIL_ALLOWED_LOCAL:
        return True
    if local == "git" and domain in EMAIL_GIT_HOSTS:
        return True
    if domain.rsplit(".", 1)[-1] in EMAIL_FILE_TLDS:  # icon@2x.png
        return True
    return False


# --------------------------------------------------------------------------
# High-risk script patterns (line-local, low false-positive by design)
# --------------------------------------------------------------------------

_BIN = r"(?:(?:/usr)?(?:/local)?/bin/)?"
_SHELL = r"(?<![\w./-])" + _BIN + r"(?:ba|z|k|da|fi)?sh\b"
_INTERP_NAME = r"(?:python[0-9.]*|perl|ruby|node|php)"
# An interpreter that reads its program from stdin: bare, or with a lone "-".
_INTERP_STDIN = r"(?<![\w./-])" + _BIN + _INTERP_NAME + r"(?:\s+-)?\s*(?:$|[;&|)#])"
_RUNNER = r"(?:sudo\s+(?:-\S+\s+)*)?(?:" + _BIN + r"env\s+(?:-\S+\s+)*)?"
_TO_SHELL = r"\|&?\s*" + _RUNNER + r"(?:" + _SHELL + r"|" + _INTERP_STDIN + r")"
_B64D = r"\bbase64(?:\s+-[a-zA-Z0-9]+)*\s+(?:-[a-zA-Z]*[dD][a-zA-Z]*|--decode)\b"
# A file name captured as group "f", then executed later on the same line.
_FILE = r"[\"']?(?P<f>[^\s;&|\"'<>]{1,200})[\"']?"
_EXEC_FILE = (
    r"[^\n]{0,300}?(?:&&|;|\|\|)\s*(?:chmod\s+\S+\s+\S+\s*(?:&&|;)\s*)?"
    + _RUNNER
    + r"(?:(?:"
    + _SHELL
    + r"|"
    + _BIN
    + _INTERP_NAME
    + r")\s+(?:-\S+\s+)*)?(?:\./)?(?P=f)(?![^\s;&|)])"
)
_DL_TO_FILE = r"\b(?:curl|wget)\b[^\n;|]{0,300}?(?:\s-[a-zA-Z]*[oO]\s*|\s--output(?:-document)?[=\s]\s*|\s*>\s*)"

SCRIPT_PATTERNS = {
    "reverse-shell": [
        re.compile(r"/dev/(?:tcp|udp)/"),
        re.compile(
            r"\b(?:nc|ncat|netcat)\b[^\n|;&]{0,200}\s-[a-zA-Z]{0,10}[ec]\s+\S{0,100}(?:sh|cmd|powershell)\b",
            re.I,
        ),
        re.compile(
            _SHELL
            + r"\s+-i\b[^\n]{0,300}?(?:[0-9]?>&|\|&?\s*(?:nc|ncat|netcat|telnet|openssl)\b)"
        ),
        re.compile(r"\bmkfifo\b[^\n]{0,300}?\b(?:nc|ncat|netcat|telnet|openssl)\b"),
        re.compile(r"\bsocat\b[^\n]{0,300}?\bexec:", re.I),
        re.compile(r"\bpty\.spawn\(\s*[\"']" + _BIN + r"(?:ba|z|k|da|fi)?sh[\"']"),
        re.compile(r"\bos\.dup2\([^\n]{0,300}?\bsocket\b|\bsocket\b[^\n]{0,300}?\bos\.dup2\("),
        re.compile(r"New-Object\s+System\.Net\.Sockets\.TCPClient", re.I),
    ],
    "pipe-to-shell": [
        # curl/wget must have at least one real argument before the first pipe;
        # later pipeline stages (tee, grep, ...) may sit before the shell.
        re.compile(r"\b(?:curl|wget)\s+(?=[^|\n]{0,300}?[A-Za-z0-9$])[^\n;]{0,400}?" + _TO_SHELL),
        re.compile(_SHELL + r"\s+(?:-c\s+)?[\"']?\$\(\s*(?:curl|wget)\b"),
        re.compile(r"(?:" + _SHELL + r"|\bsource|(?:^|\s)\.)\s+<\(\s*(?:curl|wget)\b"),
        re.compile(
            r"\b(?:iex|Invoke-Expression)\s*[\s(]*"
            r"(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|New-Object\s+(?:System\.)?Net\.WebClient)\b"
            r"|\.DownloadString\([^\n]{0,300}?\|\s*(?:iex|Invoke-Expression)\b",
            re.I,
        ),
        re.compile(
            r"\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n|]{0,300}\|\s*(?:iex|Invoke-Expression)\b",
            re.I,
        ),
        re.compile(_DL_TO_FILE + _FILE + _EXEC_FILE),
    ],
    "base64-exec": [
        re.compile(_B64D + r"[^\n;]{0,300}?" + _TO_SHELL),
        re.compile(r"\beval\b[^\n]{0,300}?" + _B64D),
        re.compile(r"\b(?:exec|eval)\s*\([^\n]{0,300}?\b(?:b64decode|atob|decodebytes)\s*\("),
        re.compile(r"\bFunction\s*\(\s*atob\s*\("),
        re.compile(
            r"\b(?:powershell|pwsh)(?:\.exe)?\b[^\n]{0,300}?\s-e(?:nc|ncodedcommand)?\s+[A-Za-z0-9+/]{20,}={0,2}",
            re.I,
        ),
        re.compile(
            r"FromBase64String[^\n]{0,300}?\b(?:iex|Invoke-Expression)\b"  # scan-allow: base64-exec
            r"|\b(?:iex|Invoke-Expression)\b[^\n]{0,300}?FromBase64String",
            re.I,
        ),
        re.compile(_B64D + r"[^\n;|]{0,300}?>\s*" + _FILE + _EXEC_FILE),
    ],
    "crypto-miner": [
        re.compile(
            r"\bxmrig\b|\bstratum\+(?:tcp|ssl|tls)://|\bcryptonight\b"  # scan-allow: crypto-miner
            r"|--donate-level\b|\bminergate\b|\bsupportxmr\.com\b",
            re.I,
        ),
    ],
}

# Every SCRIPT_PATTERNS entry needs one of these tokens. A line too long for the
# full patterns fails if it holds one, so padding a line cannot hide a command.
LONG_LINE_RISK = re.compile(
    r"\b(?:curl|wget|base64|atob|b64decode|decodebytes|dup2|TCPClient|powershell|pwsh"
    r"|iex|Invoke-Expression"
    r"|iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString"
    r"|FromBase64String"
    r"|nc|ncat|netcat|socat|telnet"
    r"|mkfifo"
    r"|openssl"
    r"|xmrig|stratum"  # scan-allow: crypto-miner
    r"|cryptonight|minergate|donate-level)\b"  # scan-allow: crypto-miner
    r"|/dev/(?:tcp|udp)/|\bpty\.spawn\b|\b(?:ba|z|k|da|fi)?sh\s+-i\b",
    re.I,
)

_CRED_PATH = re.compile(
    r"(?<!-i )(?<!-i=)(?<!IdentityFile )"
    r"(?:~|\$HOME|\$\{HOME\}|/home/[\w.-]+|/root|/Users/[\w.-]+)/\."
    r"(?:ssh/id_(?:rsa|dsa|ecdsa|ed25519)(?:_sk)?\b(?!\.pub)|aws/credentials|netrc\b|git-credentials"
    r"|docker/config\.json|kube/config|config/gh/hosts\.yml)"
    r"|(?:@|<\s*|\bcat\s+)\.env\b(?![\w.-])"
)
_NET_SEND = re.compile(
    r"\b(?:curl|wget|nc|ncat|netcat|Invoke-WebRequest|Invoke-RestMethod)\b"
    r"|\brequests\.(?:post|put)\b|\burllib\.request\b|\bfetch\s*\(|\baxios\.(?:post|put)\b",
    re.I,
)

PROMPT_INJECTION_RES = [
    re.compile(
        r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+)?(?:of\s+)?(?:the\s+|your\s+|my\s+)?"
        r"(?:previous|prior|above|earlier|preceding|original)\s+(?:instructions?|prompts?|rules|directions|context)",
        re.I,
    ),
    re.compile(
        r"\b(?:ignore|disregard|forget|reveal|leak)\s+(?:your|the)\s+system\s+prompt",
        re.I,
    ),
    re.compile(
        r"\byou\s+are\s+now\s+(?:in\s+)?(?:DAN\b|developer\s+mode|jailbroken|unrestricted)",
        re.I,
    ),
    re.compile(r"\bdo\s+not\s+(?:tell|inform|alert|notify)\s+the\s+user\b", re.I),
    re.compile(r"<\|(?:im_start|im_end|system|endoftext)\|>"),
]

SVG_SCRIPT_RE = re.compile(
    r"<script\b|\bon(?:load|error|click|mouseover|focus|begin)\s*=|javascript:", re.I
)
LFS_ATTR_RE = re.compile(r"\bfilter\s*=\s*lfs\b")
ALLOW_RE = re.compile(r"scan-allow:\s*([a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)")

# --------------------------------------------------------------------------
# Hidden Unicode
# --------------------------------------------------------------------------

BIDI = set(range(0x202A, 0x202F)) | set(range(0x2066, 0x206A))
ZERO_WIDTH = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
# Invisible operators, Hangul fillers, Mongolian vowel separator.
INVISIBLE = {
    0x2061, 0x2062, 0x2063, 0x2064,  # invisible math operators
    0x115F, 0x1160, 0x3164, 0xFFA0,  # Hangul fillers
    0x180E,  # Mongolian vowel separator
    0x2800,  # braille pattern blank
    0x034F,  # combining grapheme joiner
    0x061C,  # Arabic letter mark
    0xFFF9, 0xFFFA, 0xFFFB,  # interlinear annotation controls
    0x17B4, 0x17B5,  # Khmer inherent vowels (invisible)
}
VS_EXTRA_BASES = {0x203C, 0x2049, 0x2139, 0x3030, 0x303D}


def _is_vs(o: int) -> bool:
    return 0xFE00 <= o <= 0xFE0F


def _emojiish(ch: str) -> bool:
    o = ord(ch)
    if _is_vs(o) or 0x1F3FB <= o <= 0x1F3FF:
        return True
    return o >= 0x2190 and unicodedata.category(ch) in ("So", "Sk", "Me")


def _vs_base_ok(prev: str, nxt: str) -> bool:
    """A variation selector is legitimate after an emoji / symbol base, or in a keycap."""
    if not prev or _is_vs(ord(prev)):
        return False
    if prev in "0123456789#*" and nxt == "\u20e3":
        return True
    o = ord(prev)
    return (
        o in VS_EXTRA_BASES
        or o >= 0x1F000
        or unicodedata.category(prev) in ("So", "Sm", "Sk")
    )


def _nonlatin_letter(ch: str) -> bool:
    return ord(ch) >= 0x0590 and unicodedata.category(ch)[0] in ("L", "M")


def hidden_unicode(line: str, first_line: bool):
    """Yield (rule_id, codepoint) for suspicious invisible characters in a line."""
    n = len(line)
    i = 0
    while i < n:
        ch = line[i]
        o = ord(ch)
        prev = line[i - 1] if i > 0 else ""
        nxt = line[i + 1] if i + 1 < n else ""
        if o in BIDI:
            yield "bidi-unicode", o
        elif o in ZERO_WIDTH:
            if o == 0xFEFF and first_line and i == 0:
                pass  # byte-order mark
            elif o == 0x200D and prev and nxt and _emojiish(prev) and _emojiish(nxt):
                pass  # emoji ZWJ sequence
            elif (
                o in (0x200C, 0x200D)
                and prev
                and nxt
                and _nonlatin_letter(prev)
                and _nonlatin_letter(nxt)
            ):
                pass  # ZWNJ/ZWJ inside Arabic/Persian/Indic words
            else:
                yield "zero-width-unicode", o
        elif o in INVISIBLE or 0xE0100 <= o <= 0xE01EF:
            yield "invisible-unicode", o
        elif _is_vs(o):
            if not _vs_base_ok(prev, nxt):
                yield "invisible-unicode", o
        elif o == 0x00AD:
            yield "soft-hyphen", o
        elif 0xE0000 <= o <= 0xE007F:
            # Allow emoji subdivision flags: U+1F3F4 followed by tags ending in U+E007F.
            j = i
            while j < n and 0xE0000 <= ord(line[j]) <= 0xE007F:
                j += 1
            run = line[i:j]
            if (
                prev == "\U0001f3f4"
                and run.endswith("\U000e007f")
                and all(
                    0xE0061 <= ord(c) <= 0xE007A or 0xE0030 <= ord(c) <= 0xE0039
                    for c in run[:-1]
                )
            ):
                i = j
                continue
            yield "unicode-tag", o
            i = j
            continue
        i += 1


# --------------------------------------------------------------------------
# Findings, masking, allowlist
# --------------------------------------------------------------------------


@dataclass
class Finding:
    rule: str
    path: str
    line: int | None
    detail: str

    @property
    def severity(self) -> str:
        return RULES[self.rule].severity


def mask(value: str) -> str:
    """Show only the last 2-4 significant characters (separators removed)."""
    v = re.sub(r"[\s\-()]", "", value)
    keep = 4 if len(v) >= 10 else 2
    if len(v) <= keep + 2:
        return "****"
    return "****" + v[-keep:]


class Allowlist:
    """Path globs per rule, from .github/scan-allowlist.txt.

    Line format: `<rule-id> <glob>` (whitespace separated). `#` starts a comment.
    Globs use fnmatch: `*` also matches `/`. An unknown rule id is a hard error.
    """

    def __init__(self, entries: list[tuple[str, str]] | None = None):
        self.entries = entries or []

    @classmethod
    def load(cls, path: str | None) -> "Allowlist":
        if not path or not os.path.isfile(path):
            return cls()
        entries = []
        with open(path, encoding="utf-8") as fh:
            for n, raw in enumerate(fh, 1):
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 2:
                    raise ValueError(f"{path}:{n}: expected '<rule-id> <glob>'")
                rule, glob = parts
                if rule not in RULES:
                    raise ValueError(f"{path}:{n}: unknown rule id '{rule}'")
                entries.append((rule, glob))
        return cls(entries)

    def skips(self, rule: str, relpath: str) -> bool:
        return any(
            r == rule and fnmatch.fnmatchcase(relpath, g) for r, g in self.entries
        )


def _line_allows(lines: list[str], idx: int) -> set[str]:
    allowed: set[str] = set()
    for j in (idx, idx - 1):
        if 0 <= j < len(lines):
            for m in ALLOW_RE.finditer(lines[j]):
                allowed.update(x.strip() for x in m.group(1).split(","))
    return allowed


# --------------------------------------------------------------------------
# Decoding and text rules
# --------------------------------------------------------------------------


def _sniff_utf16(data: bytes) -> str | None:
    sample = data[:8192]
    half = len(sample) // 2
    if half < 2:
        return None
    even = sum(1 for k in range(0, 2 * half, 2) if sample[k] == 0) / half
    odd = sum(1 for k in range(1, 2 * half, 2) if sample[k] == 0) / half
    if odd > 0.3 and even < 0.05:
        return "utf-16-le"
    if even > 0.3 and odd < 0.05:
        return "utf-16-be"
    return None


def decode(data: bytes) -> tuple[str, bool]:
    """Return (text, looks_binary). Never gives up: binary data is NUL-stripped."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), False
    if b"\x00" in data:
        enc = _sniff_utf16(data)
        if enc:
            return data.decode(enc, errors="replace"), False
        return data.replace(b"\x00", b"").decode("utf-8", errors="replace"), True
    return data.decode("utf-8", errors="replace"), False


def scan_text(relpath: str, text: str) -> list[Finding]:
    """Line rules. Returns all findings before any suppression.

    Lines are numbered by "\\n". Inside each line, rules also run on every
    segment split at other line breaks (CR, U+2028, U+0085, ...).
    """
    out: list[Finding] = []
    seen: set[tuple[str, int, str]] = set()

    def add(rule: str, ln: int, detail: str) -> None:
        key = (rule, ln, detail)
        if key not in seen:
            seen.add(key)
            out.append(Finding(rule, relpath, ln, detail))

    ext = os.path.splitext(relpath)[1].lower()
    is_gitattributes = posixpath.basename(relpath) == ".gitattributes"
    for i, line in enumerate(text.split("\n")):
        ln = i + 1
        for rule, cp in hidden_unicode(line, i == 0):
            add(rule, ln, f"U+{cp:04X} {unicodedata.name(chr(cp), 'UNNAMED')}")
        for seg in line.splitlines() or [""]:
            for _, v in find_phones(seg):
                add("phone-number", ln, mask(v))
            for m in SSN_RE.finditer(seg):
                add("us-ssn", ln, mask(m.group(0)))
            for m in SSN_KEYWORD_RE.finditer(seg):
                add("us-ssn", ln, mask("".join(m.groups())))
            for _, v in find_cards(seg):
                add("credit-card", ln, mask(v))
            for _, v in find_ibans(seg):
                add("iban", ln, mask(v))
            if PRIVATE_KEY_RE.search(seg):
                add("private-key", ln, "header present")
            if any(r.search(seg) for r in WHATSAPP_RES):
                add("whatsapp-export", ln, "chat export line format")
            if len(seg) > MAX_SCRIPT_LINE:
                if LONG_LINE_RISK.search(seg):
                    add("long-line", ln, f"{len(seg)} characters")
            else:
                for rule, pats in SCRIPT_PATTERNS.items():
                    if any(p.search(seg) for p in pats):
                        add(rule, ln, "pattern match")
            if _CRED_PATH.search(seg) and _NET_SEND.search(seg):
                add(
                    "credential-exfil", ln, "credential path + network send on one line"
                )
            for m in EMAIL_RE.finditer(seg):
                if not email_allowed(m.group(0)):
                    add("email-address", ln, mask(m.group(0)))
            if ext in PROMPT_EXTS and any(r.search(seg) for r in PROMPT_INJECTION_RES):
                add("prompt-injection", ln, "phrase match")
            if ext == ".svg" and SVG_SCRIPT_RE.search(seg):
                add("svg-script", ln, "script/handler")
            if is_gitattributes and not seg.lstrip().startswith("#"):
                if LFS_ATTR_RE.search(seg):
                    add("git-lfs", ln, "filter=lfs attribute")
                attrs = seg.split()[1:]
                if any(a in ("-diff", "binary", "-text") or a.startswith("diff=") for a in attrs):
                    add("gitattributes-diff", ln, "attribute hides or alters diffs")
    return out


# --------------------------------------------------------------------------
# Entries (one file-like thing from the working tree or from git objects)
# --------------------------------------------------------------------------


@dataclass
class Entry:
    path: str
    kind: str  # "file", "symlink", "gitlink"
    size: int = 0
    data: bytes | None = None  # None when larger than MAX_BYTES
    link_target: str | None = None
    escapes: bool | None = None  # precomputed symlink resolution (git mode)


def resolve_link(parts: list[str], target: str, links: dict[str, str], depth: int = 0) -> list[str] | None:
    """Resolve `target` from directory `parts`, following the tree's own symlinks.

    Returns the resolved path components, or None if resolution leaves the
    repository (absolute target, too many "..", or a symlink loop).
    """
    if depth > 40 or target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", target):
        return None
    parts = list(parts)
    for comp in target.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(comp)
        cur = "/".join(parts)
        if cur in links:
            sub = resolve_link(parts[:-1], links[cur], links, depth + 1)
            if sub is None:
                return None
            parts = sub
    return parts


def _link_escapes(path: str, target: str) -> bool:
    if target.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", target):
        return True
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    return resolved == ".." or resolved.startswith("../")


def analyze(entry: Entry) -> tuple[list[Finding], list[str]]:
    """All findings for one entry, plus its text lines (for inline overrides)."""
    p = entry.path
    raw: list[Finding] = []
    lines: list[str] = []
    if entry.kind == "gitlink":
        return [Finding("submodule", p, None, "gitlink (mode 160000)")], []
    if entry.kind == "symlink":
        esc = entry.escapes if entry.escapes is not None else _link_escapes(p, entry.link_target or "")
        if esc:
            raw.append(Finding("symlink-escape", p, None, "target outside repository"))
        return raw, []

    if EXEC_EXT_RE.search(p):
        raw.append(Finding("executable-binary", p, None, "extension"))
    if ARCHIVE_EXT_RE.search(p):
        raw.append(Finding("archive-file", p, None, "extension"))
    if entry.size > MAX_BYTES or entry.data is None:
        raw.append(Finding("large-file", p, None, f"{entry.size} bytes"))
        return raw, []

    data = entry.data
    rule, kind = classify_binary(data[:1024])
    if rule and not any(f.rule == rule for f in raw):
        raw.append(Finding(rule, p, None, kind))
    if rule != "archive-file":
        for sig, what in EMBEDDED_ARCHIVE:
            off = data.find(sig)
            if off > 0:
                raw.append(
                    Finding("archive-file", p, None, f"embedded {what} at byte {off}")
                )
                break
    if data.startswith(LFS_POINTER):
        raw.append(Finding("git-lfs", p, None, "LFS pointer file"))

    ext = os.path.splitext(p)[1].lower()
    if ext in IMAGE_EXTS:
        status, detail = check_image(ext, data)
        if status == "ok":
            return raw, []
        if status == "polyglot":
            raw.append(Finding("image-polyglot", p, None, detail))
            return raw, []
        # "mismatch": not really this image type; fall through and scan as text.
    if rule:
        return raw, []  # executable / archive: already a FAIL; text is noise

    text, looks_binary = decode(data)
    if looks_binary:
        raw.append(
            Finding(
                "binary-file",
                p,
                None,
                "non-text content (NUL bytes); text rules still applied",
            )
        )
    lines = text.split("\n")
    raw.extend(scan_text(p, text))
    return raw, lines


@dataclass
class Result:
    reported: list[Finding]
    suppressed: list[Finding]
    path_skipped: int


def apply_overrides(
    entry_path: str, raw: list[Finding], lines: list[str], allowlist: Allowlist
) -> Result:
    reported, suppressed, skipped = [], [], 0
    for f in raw:
        if allowlist.skips(f.rule, entry_path):
            skipped += 1
            continue
        if f.line is not None and f.rule in _line_allows(lines, f.line - 1):
            if f.rule in INLINE_OVERRIDABLE:
                suppressed.append(f)
                continue
            f.detail += " (inline scan-allow is not accepted for this rule; only .github/scan-allowlist.txt)"
        reported.append(f)
    return Result(reported, suppressed, skipped)


def scan_entry(entry: Entry, allowlist: Allowlist) -> Result:
    raw, lines = analyze(entry)
    return apply_overrides(entry.path, raw, lines, allowlist)


# --------------------------------------------------------------------------
# Entry sources
# --------------------------------------------------------------------------


def _git(root: str, *args: str, input: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", root, *args], check=True, capture_output=True, input=input
    ).stdout


def _dec(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def worktree_entry(
    root: str, relpath: str, gitlinks: set[str] | None = None
) -> Entry | None:
    if gitlinks and relpath in gitlinks:
        return Entry(relpath, "gitlink")
    full = os.path.join(root, relpath)
    try:
        st = os.lstat(full)
    except FileNotFoundError:
        return None
    if os.path.islink(full):
        target = os.readlink(full)
        root_real = os.path.realpath(root)
        real = os.path.realpath(full)
        escapes = (
            _link_escapes(relpath, target)
            or os.path.commonpath([real, root_real]) != root_real
        )
        return Entry(relpath, "symlink", link_target="/" if escapes else target)
    if not os.path.isfile(full):
        return None
    data = None
    if st.st_size <= MAX_BYTES:
        with open(full, "rb") as fh:
            data = fh.read()
    return Entry(relpath, "file", st.st_size, data)


def scan_file(
    root: str, relpath: str, allowlist: Allowlist
) -> tuple[list[Finding], list[Finding]]:
    """Scan one working-tree file. Returns (reported, suppressed_by_inline_override)."""
    entry = worktree_entry(root, relpath)
    if entry is None:
        return [], []
    r = scan_entry(entry, allowlist)
    return r.reported, r.suppressed


def git_entries(root: str, rev: str, only: set[str] | None = None, chunk: int = 256):
    """Yield Entry objects for every tree entry at `rev` (optionally only `only` paths).

    Content comes from git blobs, so .gitattributes (filters, working-tree-encoding)
    can not alter it.
    """
    out = _git(root, "ls-tree", "-r", "-z", "-l", "--full-tree", rev)
    todo: list[tuple[str, str, str, int]] = []
    link_shas: dict[str, str] = {}
    for rec in out.split(b"\0"):
        if not rec:
            continue
        meta, raw_path = rec.split(b"\t", 1)
        mode, typ, sha, size = meta.decode().split()
        path = _dec(raw_path)
        if mode == "120000":
            link_shas[path] = sha
        if only is not None and path not in only:
            continue
        if mode == "160000" or typ == "commit":
            yield Entry(path, "gitlink")
            continue
        if typ != "blob":
            continue
        todo.append((path, mode, sha, int(size)))

    # Every symlink in the tree (not only changed ones), to resolve chains.
    link_blobs = _cat_blobs(root, sorted(set(link_shas.values()))) if link_shas else {}
    links = {p: _dec(link_blobs[s]) for p, s in link_shas.items()}

    for k in range(0, len(todo), chunk):
        part = todo[k : k + chunk]
        want = [t for t in part if t[3] <= MAX_BYTES or t[1] == "120000"]
        blobs = _cat_blobs(root, [t[2] for t in want]) if want else {}
        for path, mode, sha, size in part:
            data = blobs.get(sha)
            if mode == "120000":
                target = links[path]
                escapes = resolve_link(path.split("/")[:-1], target, links) is None
                yield Entry(path, "symlink", size, link_target=target, escapes=escapes)
            else:
                yield Entry(path, "file", size, data if size <= MAX_BYTES else None)


def _cat_blobs(root: str, shas: list[str]) -> dict[str, bytes]:
    out = _git(root, "cat-file", "--batch", input=("\n".join(shas) + "\n").encode())
    res: dict[str, bytes] = {}
    pos = 0
    while pos < len(out):
        nl = out.index(b"\n", pos)
        header = out[pos:nl].split()
        if len(header) < 3:
            raise RuntimeError(f"git cat-file: unexpected header {header!r}")
        sha, size = header[0].decode(), int(header[2])
        res[sha] = out[nl + 1 : nl + 1 + size]
        pos = nl + 1 + size + 1
    return res


def repo_root() -> str:
    try:
        return _git(os.getcwd(), "rev-parse", "--show-toplevel").decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return os.getcwd()


def list_all(root: str) -> list[str]:
    try:
        out = _git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        return sorted({p for p in _dec(out).split("\0") if p})
    except (subprocess.CalledProcessError, FileNotFoundError):
        files = []
        for d, dirs, names in os.walk(root):
            dirs[:] = [x for x in dirs if x != ".git"]
            files += [os.path.relpath(os.path.join(d, n), root) for n in names]
        return sorted(files)


def worktree_gitlinks(root: str) -> set[str]:
    try:
        out = _git(root, "ls-files", "-z", "--stage")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    links = set()
    for rec in out.split(b"\0"):
        if rec.startswith(b"160000 "):
            links.add(_dec(rec.split(b"\t", 1)[1]))
    return links


def list_changed(root: str, base: str, head: str) -> list[str]:
    out = _git(
        root,
        "diff",
        "-z",
        "--name-only",
        "--no-renames",
        "--diff-filter=ACMRT",
        f"{base}...{head}",
    )
    return sorted({p for p in _dec(out).split("\0") if p})


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _printable(s: str) -> str:
    return s.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def _esc_prop(s: str) -> str:
    s = _printable(s)
    return (
        s.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _esc_msg(s: str) -> str:
    return _printable(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def annotate(kind: str, f: Finding, suffix: str = "") -> str:
    props = f"file={_esc_prop(f.path)}"
    if f.line is not None:
        props += f",line={f.line}"
    props += f",title={f.rule}"
    msg = f"[{f.rule}] {RULES[f.rule].description}: {f.detail}{suffix}"
    return f"::{kind} {props}::{_esc_msg(msg)}"


def _md_cell(s: str) -> str:
    s = _printable(s)
    return re.sub(r"[\r\n\x00-\x1f]", " ", s).replace("|", "\\|").replace("`", "'")


def write_step_summary(
    path: str,
    n_files: int,
    fails: int,
    warns: int,
    suppressed: list[Finding],
    skipped: int,
) -> None:
    lines = [
        "## pii-and-content",
        "",
        f"{n_files} files scanned: **{fails} failures**, {warns} warnings, "
        f"{len(suppressed)} inline `scan-allow` overrides, {skipped} findings skipped by path allowlist.",
        "",
    ]
    if suppressed:
        lines += [
            "### Inline overrides (maintainer: review each)",
            "",
            "| File | Line | Rule |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {_md_cell(f.path)} | {f.line} | {f.rule} |" for f in suppressed]
        lines.append("")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="scan working tree: tracked + untracked-not-ignored files",
    )
    ap.add_argument("--rev", help="scan every file in this commit (from git objects)")
    ap.add_argument(
        "--base", help="base commit (with --head): scan files changed in base...head"
    )
    ap.add_argument(
        "--head", help="head commit; content is read from this commit's blobs"
    )
    ap.add_argument(
        "--allowlist", help=f"path allowlist (default: <repo>/{ALLOWLIST_DEFAULT})"
    )
    ap.add_argument("--root", help="repository root (default: git toplevel or cwd)")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args(argv)

    modes = sum([bool(a.all), bool(a.rev), bool(a.base or a.head), bool(a.paths)])
    if modes != 1 or bool(a.base) != bool(a.head):
        ap.error("use exactly one of: --all, --rev, --base/--head, or PATHs")

    root = os.path.abspath(a.root) if a.root else repo_root()
    try:
        allowlist = Allowlist.load(a.allowlist or os.path.join(root, ALLOWLIST_DEFAULT))
        if a.all:
            links = worktree_gitlinks(root)
            files = sorted(set(list_all(root)) | links)
            entries = (worktree_entry(root, p, links) for p in files)
        elif a.rev:
            entries = list(git_entries(root, a.rev))
            files = [e.path for e in entries]
        elif a.base:
            changed = list_changed(root, a.base, a.head)
            entries = list(git_entries(root, a.head, set(changed)))
            files = changed
        else:
            files = [os.path.relpath(os.path.abspath(p), root) for p in a.paths]
            entries = (worktree_entry(root, p) for p in files)

        fails = warns = skipped = 0
        suppressed_all: list[Finding] = []
        for entry in entries:
            if entry is None:
                continue
            r = scan_entry(entry, allowlist)
            skipped += r.path_skipped
            for f in r.suppressed:
                suppressed_all.append(f)
                print(
                    annotate(
                        "notice",
                        f,
                        " (suppressed by inline scan-allow; maintainer: review this override)",
                    )
                )
            for f in r.reported:
                print(annotate(f.severity, f))
                if f.severity == FAIL:
                    fails += 1
                else:
                    warns += 1
    except ValueError as e:
        print(f"::error::scan config error: {_esc_msg(str(e))}")
        return 2
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace").strip() if e.stderr else ""
        print(f"::error::git failed: {_esc_msg(err)}")
        return 2

    print(
        f"scan_content: {len(files)} files, {fails} failures, {warns} warnings, "
        f"{len(suppressed_all)} inline overrides, {skipped} path-allowlisted findings"
    )
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        write_step_summary(summary, len(files), fails, warns, suppressed_all, skipped)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
