#!/usr/bin/env python3
"""Weekly Claude Code token usage, diagnostics and findings from local transcripts.

Python 3.10+, standard library only. Runs on macOS, Linux and Windows.

Exit codes:
  0  success
  2  input problem: bad arguments or config file, no transcript files,
     no usage in the date range, or schema drift (see SCHEMA_DRIFT_MAX_FRACTION)
  3  an invariant failed (see check_invariants); no output is written

Outputs (in --out): weekly_by_model_effort.csv, weekly_by_agent.csv,
diagnostics.json, inventory.json, findings.json, report.md, manifest.json.
The outputs contain only numbers, ids, tool names, file paths and model names.
They never contain message text or tool input/output content.
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import statistics
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

VERSION = "1.0.0"
TOOL = "usage-review"

EXIT_OK = 0
EXIT_INPUT = 2
EXIT_INVARIANT = 3

# ---------------------------------------------------------------- thresholds
SCHEMA_DRIFT_MAX_FRACTION = Decimal("0.05")  # assistant lines without usage/timestamp
MIN_SESSIONS_PER_SIDE = 10  # before/after: smaller samples give "insufficient data"
BEFORE_AFTER_WINDOW_DAYS = 14  # days on each side of a change date
CHARS_PER_TOKEN = 4  # approximation for tool results and config files
REPEATED_READ_MIN = 3  # same file read this often in one stream
LARGE_TOOL_RESULT_CHARS = 20_000
IDLE_GAP_5M_SECONDS = 300
IDLE_GAP_1H_SECONDS = 3600
REWRITE_MIN_CONTEXT = 20_000  # a cache rewrite must re-send at least this context
REWRITE_MIN_WRITE_SHARE = Decimal("0.5")  # and write at least this share of it
CONTEXT_WINDOW_STANDARD = 200_000
CONTEXT_WINDOW_LARGE = (
    1_000_000  # assumed when any call in the session exceeds the standard window
)
NEAR_LIMIT_FRACTION = Decimal("0.8")
PEAK_CONTEXT_THRESHOLD = 300_000
START_CONTEXT_THRESHOLD = 30_000
REVIEWER_ROUNDS_THRESHOLD = 2
PEAK_BUCKETS = (200_000, 300_000, 500_000, 800_000)
TOP_N = 10
CONFIDENCE = {"measured": Decimal("1.0"), "inferred": Decimal("0.5")}
THRESHOLD_NAMES = (
    "SCHEMA_DRIFT_MAX_FRACTION",
    "MIN_SESSIONS_PER_SIDE",
    "BEFORE_AFTER_WINDOW_DAYS",
    "CHARS_PER_TOKEN",
    "REPEATED_READ_MIN",
    "LARGE_TOOL_RESULT_CHARS",
    "IDLE_GAP_5M_SECONDS",
    "IDLE_GAP_1H_SECONDS",
    "REWRITE_MIN_CONTEXT",
    "REWRITE_MIN_WRITE_SHARE",
    "CONTEXT_WINDOW_STANDARD",
    "CONTEXT_WINDOW_LARGE",
    "NEAR_LIMIT_FRACTION",
    "PEAK_CONTEXT_THRESHOLD",
    "START_CONTEXT_THRESHOLD",
    "REVIEWER_ROUNDS_THRESHOLD",
    "PEAK_BUCKETS",
    "TOP_N",
)
VERDICT_LINES = {"VERDICT: APPROVE": "approve", "VERDICT: CHANGES": "changes"}

TOKEN_FIELDS = ("inp", "w5", "w1", "read", "out", "think")
CSV_TOKEN_COLUMNS = (
    "input_uncached",
    "cache_write_5m",
    "cache_write_1h",
    "cache_read",
    "output",
    "thinking",
)
HDR = [
    "week",
    "model",
    "effort",
    "sessions",
    "api_calls",
    *CSV_TOKEN_COLUMNS,
    "cache_hit_pct",
    "cost_weighted_input_usd",
    "cost_weighted_output_usd",
]
HDR_AGENT = ["week", "split", "agent_type", *HDR[3:]]

SETTINGS_KEYS = (
    "model",
    "effortLevel",
    "modelSettings",
    "alwaysThinkingEnabled",
    "autoCompactWindow",
    "promptCacheTtl",
    "subagentPromptCacheTtl",
    "cleanupPeriodDays",
    "outputStyle",
)
SETTINGS_ENV_KEYS = (
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
    "MAX_THINKING_TOKENS",
    "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
    "BASH_MAX_OUTPUT_LENGTH",
    "MAX_MCP_OUTPUT_TOKENS",
    "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
    "DISABLE_PROMPT_CACHING",
    "ENABLE_PROMPT_CACHING_1H",
    "CLAUDE_CODE_EFFORT_LEVEL",
)
AGENT_KEYS = ("model", "effort", "tools", "disallowedTools", "omitClaudeMd", "cacheTtl")

METRICS = (
    "sessions",
    "calls",
    "input_uncached",
    "cache_write_5m",
    "cache_write_1h",
    "cache_read",
    "output",
    "cache_hit_pct",
    "write_share_pct",
    "median_peak_context",
    "max_peak_context",
    "median_first_call_context",
    "tokens_per_session",
    "cost_weighted_input_usd",
    "compactions",
    "auto_compactions",
    "limit_hits",
)
SCOPES = ("all", "main", "subagent")

CENT = Decimal("0.01")
TENTH = Decimal("0.1")
MILLION = Decimal(1_000_000)
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class InputError(Exception):
    """Bad arguments, config or data. Exit code 2."""


class InvariantError(Exception):
    """An invariant failed. Exit code 3."""


# ------------------------------------------------------------------- helpers
def q(value: Decimal | None, step: Decimal = CENT) -> Decimal | None:
    return None if value is None else value.quantize(step, rounding=ROUND_HALF_UP)


def pct(num: int, den: int) -> Decimal:
    return q(Decimal(100 * num) / Decimal(den), TENTH) if den else Decimal("0.0")


def parse_date(text: str, what: str) -> dt.date:
    if not DATE_RE.match(text or ""):
        raise InputError(f"{what}: expected YYYY-MM-DD, got {text!r}")
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise InputError(f"{what}: {exc}") from None


def json_default(obj):
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"not serializable: {type(obj).__name__}")


def dumps(obj) -> str:
    return (
        json.dumps(
            obj, indent=1, sort_keys=True, default=json_default, ensure_ascii=False
        )
        + "\n"
    )


def read_json(path: Path, what: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, ValueError) as exc:
        raise InputError(f"{what} {path}: {exc}") from None


def median_int(values) -> int | None:
    values = list(values)
    return int(statistics.median(values)) if values else None


def p90_int(values) -> int | None:
    values = sorted(values)
    if not values:
        return None
    idx = max(0, -(-9 * len(values) // 10) - 1)  # ceil(0.9 n) - 1
    return int(values[idx])


# -------------------------------------------------------------------- prices
class Prices:
    def __init__(self, path: Path):
        data = read_json(path, "prices file")
        self.path = path
        try:
            self.checked = str(data["checked"])
            self.source = str(data["source"])
            mult = data["cache_write_mult"]
            self.w5_mult = Decimal(mult["5m"])
            self.w1_mult = Decimal(mult["1h"])
            self.models = {}
            for prefix, row in data["models"].items():
                self.models[prefix] = (
                    Decimal(row["input"]),
                    Decimal(row["output"]),
                    Decimal(row["cache_read_mult"]),
                )
        except (KeyError, TypeError, AttributeError, ArithmeticError) as exc:
            raise InputError(
                f"prices file {path}: missing or bad field ({exc})"
            ) from None
        parse_date(self.checked, f"prices file {path}: checked")

    def lookup(self, model: str | None):
        hits = [k for k in self.models if model and model.startswith(k)]
        return self.models[max(hits, key=len)] if hits else None

    def weights(self, r) -> tuple[Decimal, Decimal] | None:
        p = self.lookup(r["model"])
        if p is None:
            return None
        inp, out, rd = p
        w_in = (
            (
                Decimal(r["inp"])
                + r["w5"] * self.w5_mult
                + r["w1"] * self.w1_mult
                + r["read"] * rd
            )
            * inp
            / MILLION
        )
        return w_in, Decimal(r["out"]) * out / MILLION


# ------------------------------------------------------------------- changes
def load_changes(path: Path) -> list[dict]:
    data = read_json(path, "changes file")
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        raise InputError(
            f"changes file {path}: expected an object with a 'changes' list"
        )
    out = []
    for i, c in enumerate(data["changes"]):
        where = f"changes file {path}: entry {i}"
        if not isinstance(c, dict):
            raise InputError(f"{where}: not an object")
        date = parse_date(str(c.get("date", "")), f"{where}: date")
        desc = c.get("description")
        if not isinstance(desc, str) or not desc.strip():
            raise InputError(f"{where}: description is required")
        metrics = c.get("metrics")
        if not isinstance(metrics, list) or not metrics:
            raise InputError(f"{where}: metrics must be a non-empty list")
        bad = [m for m in metrics if m not in METRICS]
        if bad:
            raise InputError(f"{where}: unknown metrics {bad}; known: {list(METRICS)}")
        scope = c.get("scope", "all")
        if scope not in SCOPES:
            raise InputError(f"{where}: scope must be one of {list(SCOPES)}")
        out.append(
            dict(date=date, description=desc, metrics=list(metrics), scope=scope)
        )
    return out


# ------------------------------------------------------------------ profiles
def resolve_profiles(
    flags: list[str], home: Path, env: dict
) -> list[tuple[str, Path, bool]]:
    """Return (name, path, explicit). Default ~/.claude, then $CLAUDE_CONFIG_DIR, then flags."""
    items: list[tuple[str, Path, bool]] = [("claude", home / ".claude", False)]
    ccd = env.get("CLAUDE_CONFIG_DIR")
    if ccd:
        p = Path(ccd).expanduser()
        items.append((p.name.lstrip(".") or "config-dir", p, False))
    for flag in flags or []:
        name, sep, raw = flag.partition("=")
        if not sep or not NAME_RE.match(name) or not raw:
            raise InputError(
                f"--profile {flag!r}: expected NAME=PATH (NAME: letters, digits, _ . -)"
            )
        p = Path(raw).expanduser()
        if not p.is_dir():
            raise InputError(f"--profile {name}: directory not found: {p}")
        items.append((name, p, True))
    # One entry per resolved directory; a flag wins over a default.
    by_path: dict[str, tuple[str, Path, bool]] = {}
    for name, p, explicit in items:
        key = os.path.normcase(str(p.resolve()))
        if key not in by_path or explicit:
            by_path[key] = (name, p, explicit)
    result = list(by_path.values())
    names = [n for n, _, _ in result]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise InputError(
            f"duplicate profile names {dup}; name them with --profile NAME=PATH"
        )
    return result


# ------------------------------------------------------------------- parsing
def parse_ts(text, tz):
    if not isinstance(text, str) or not text:
        return None
    try:
        t = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return t.astimezone(tz) if tz is not None else t.astimezone()


def content_chars(c) -> int:
    if c is None:
        return 0
    if isinstance(c, str):
        return len(c)
    return len(json.dumps(c, ensure_ascii=False))


def last_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def verdict_of(text: str) -> str:
    found = {
        VERDICT_LINES[ln.strip()]
        for ln in text.splitlines()
        if ln.strip() in VERDICT_LINES
    }
    return found.pop() if len(found) == 1 else "unclear"


class Scan:
    """Everything read from the transcripts. Holds no message text."""

    def __init__(self):
        self.files = collections.Counter()  # profile -> files
        self.lines = 0
        self.assistant_lines = 0
        self.drift = collections.Counter()  # assistant lines lacking usage / timestamp
        self.skipped = collections.Counter()
        self.missing = collections.Counter()
        self.calls: dict[str, dict] = {}
        self.raw: list[dict] = []  # every usage line (for reconciliation)
        self.compacts: dict[str, dict] = {}
        self.limits: dict[str, dict] = {}
        self.tool_uses: dict[str, dict] = {}
        self.tool_results: dict[str, dict] = {}
        self.reviewer_final: dict[str, str] = {}  # run id -> verdict
        self.cwds = collections.Counter()


def scan(profiles, tz) -> Scan:
    s = Scan()
    for prof, root, _ in profiles:
        proj = root / "projects"
        if not proj.is_dir():
            continue
        for f in sorted(proj.glob("**/*.jsonl")):
            s.files[prof] += 1
            scan_file(s, prof, f, tz)
    return s


def scan_file(s: Scan, prof: str, f: Path, tz) -> None:
    in_sub_dir = f.parent.name == "subagents"
    meta = {}
    if in_sub_dir:
        m = f.with_name(f.stem + ".meta.json")
        if m.is_file():
            try:
                meta = json.loads(m.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                s.missing["subagent meta.json unreadable"] += 1
        else:
            s.missing["subagent meta.json"] += 1
        if not isinstance(meta, dict):
            meta = {}
    parent_session = f.parent.parent.name if in_sub_dir else None
    run_id = f.stem if in_sub_dir else None
    agent_type = str(meta.get("agentType") or "unknown") if in_sub_dir else None
    reviewer_text = None
    with f.open(encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, 1):
            s.lines += 1
            line = line.strip()
            if not line:
                s.skipped["blank line"] += 1
                continue
            try:
                d = json.loads(line)
            except ValueError:
                s.skipped["bad json"] += 1
                continue
            if not isinstance(d, dict):
                s.skipped["not a json object"] += 1
                continue
            t = d.get("type")
            tstamp = d.get("timestamp")
            sess = parent_session or d.get("sessionId") or d.get("session_id") or f.stem
            sub = in_sub_dir or bool(d.get("isSidechain"))
            stream = (
                (prof, "sub", run_id)
                if in_sub_dir
                else ((prof, "side", sess) if sub else (prof, "main", sess))
            )
            if t == "system" and d.get("subtype") == "compact_boundary":
                cm = (
                    d.get("compactMetadata")
                    if isinstance(d.get("compactMetadata"), dict)
                    else {}
                )
                key = d.get("uuid") or f"{f}:{lineno}"
                s.compacts.setdefault(
                    key,
                    dict(
                        profile=prof,
                        session=sess,
                        run=run_id,
                        ts=parse_ts(tstamp, tz),
                        trigger=cm.get("trigger")
                        if isinstance(cm.get("trigger"), str)
                        else "unknown",
                        pre_tokens=cm.get("preTokens")
                        if isinstance(cm.get("preTokens"), int)
                        else None,
                        post_tokens=cm.get("postTokens")
                        if isinstance(cm.get("postTokens"), int)
                        else None,
                    ),
                )
                continue
            msg = d.get("message") if isinstance(d.get("message"), dict) else {}
            content = msg.get("content")
            if t == "user":
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            tid = b.get("tool_use_id")
                            if tid and tid not in s.tool_results:
                                s.tool_results[tid] = dict(
                                    chars=content_chars(b.get("content")),
                                    ts=parse_ts(tstamp, tz),
                                    stream=stream,
                                    sub=sub,
                                )
                continue
            if t != "assistant":
                continue
            ts = parse_ts(tstamp, tz)
            if isinstance(content, list):
                for b in content:
                    if (
                        isinstance(b, dict)
                        and b.get("type") == "tool_use"
                        and b.get("id") not in s.tool_uses
                    ):
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        fp = (
                            inp.get("file_path")
                            if isinstance(inp.get("file_path"), str)
                            else None
                        )
                        s.tool_uses[b.get("id")] = dict(
                            name=str(b.get("name") or "unknown"),
                            file_path=fp,
                            ts=ts,
                            stream=stream,
                            session=sess,
                            sub=sub,
                        )
            model = msg.get("model")
            if agent_type == "reviewer":
                txt = last_text(content)
                if txt.strip():
                    reviewer_text = txt
            if model == "<synthetic>":
                s.skipped["<synthetic> model"] += 1
                if d.get("error") == "rate_limit":
                    key = d.get("uuid") or f"{f}:{lineno}"
                    s.limits.setdefault(
                        key, dict(profile=prof, session=sess, run=run_id, ts=ts)
                    )
                continue
            s.assistant_lines += 1
            u = msg.get("usage")
            if not isinstance(u, dict):
                s.drift["message.usage"] += 1
                s.skipped["assistant line without message.usage"] += 1
                continue
            if ts is None:
                s.drift["timestamp"] += 1
                s.skipped["assistant line without a valid timestamp"] += 1
                continue
            key = msg.get("id") or d.get("requestId")
            if not key:
                s.missing["message.id and requestId"] += 1
                key = f"{f}:{lineno}"
            eff = d.get("perTurnEffort") or d.get("effort") or "unknown"
            if eff == "unknown":
                s.missing["effort"] += 1
            if not model:
                s.missing["message.model"] += 1
                model = "unknown"
            cc = (
                u.get("cache_creation")
                if isinstance(u.get("cache_creation"), dict)
                else {}
            )
            w5 = cc.get("ephemeral_5m_input_tokens")
            w1 = cc.get("ephemeral_1h_input_tokens")
            if w5 is None and w1 is None:
                s.missing["cache_creation 5m/1h split (counted as 5m)"] += 1
                w5, w1 = u.get("cache_creation_input_tokens") or 0, 0
            details = (
                u.get("output_tokens_details")
                if isinstance(u.get("output_tokens_details"), dict)
                else {}
            )
            rec = dict(
                profile=prof,
                sub=sub,
                agent=agent_type or ("unknown" if sub else "main"),
                run=run_id,
                session=str(sess),
                stream=stream,
                ts=ts,
                model=str(model),
                effort=str(eff),
                inp=int(u.get("input_tokens") or 0),
                w5=int(w5 or 0),
                w1=int(w1 or 0),
                read=int(u.get("cache_read_input_tokens") or 0),
                out=int(u.get("output_tokens") or 0),
                think=int(details.get("thinking_tokens") or 0),
                key=key,
            )
            if not sub and isinstance(d.get("cwd"), str):
                s.cwds[(prof, d["cwd"], rec["session"])] += 1
            s.raw.append(rec)
            old = s.calls.get(key)
            if old is None or rec["out"] >= old["out"]:
                s.calls[key] = rec
    if agent_type == "reviewer":
        s.reviewer_final[run_id] = verdict_of(reviewer_text or "")


# ------------------------------------------------------------------- weeks
def week_of(d: dt.date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def partial_weeks(
    weeks, start_bound: dt.date, end_bound: dt.date, today: dt.date
) -> dict[str, str]:
    """A week is partial if the covered range does not include all 7 days of it."""
    out = {}
    last_complete = min(end_bound, today - dt.timedelta(days=1))
    for w in weeks:
        monday = dt.date.fromisocalendar(int(w[:4]), int(w[6:]), 1)
        sunday = monday + dt.timedelta(days=6)
        reasons = []
        if monday < start_bound:
            reasons.append(f"range starts {start_bound}")
        if sunday > last_complete:
            reasons.append(f"range complete to {last_complete}")
        if reasons:
            out[w] = "; ".join(reasons)
    return out


# --------------------------------------------------------------- aggregation
def ctx(r) -> int:
    return r["inp"] + r["w5"] + r["w1"] + r["read"]


def total_tokens(r) -> int:
    return ctx(r) + r["out"]


def agg(rs, prices: Prices) -> dict:
    a = {k: sum(r[k] for r in rs) for k in TOKEN_FIELDS}
    a["calls"] = len(rs)
    a["sessions"] = len({r["session"] for r in rs})
    a["hit"] = pct(a["read"], a["inp"] + a["w5"] + a["w1"] + a["read"])
    ws = [prices.weights(r) for r in rs]
    if any(w is None for w in ws):
        a["cw_in"] = a["cw_out"] = None
    else:
        a["cw_in"] = sum((w[0] for w in ws), Decimal(0))
        a["cw_out"] = sum((w[1] for w in ws), Decimal(0))
    return a


def row(keys, a) -> list:
    return [
        *keys,
        a["sessions"],
        a["calls"],
        *(a[k] for k in TOKEN_FIELDS),
        a["hit"],
        "" if a["cw_in"] is None else q(a["cw_in"]),
        "" if a["cw_out"] is None else q(a["cw_out"]),
    ]


def build_tables(recs, weeks, partial, prices):
    label = {w: w + (" (partial)" if w in partial else "") for w in weeks}
    t1, t2, blocks = [HDR], [HDR_AGENT], []
    by_week = collections.defaultdict(list)
    for r in recs:
        by_week[r["week"]].append(r)
    for w in weeks:
        wr = by_week[w]
        g1 = collections.defaultdict(list)
        for r in wr:
            g1[(r["model"], r["effort"])].append(r)
        groups = sorted(
            g1.items(), key=lambda kv: (-sum(total_tokens(x) for x in kv[1]), kv[0])
        )
        rows1 = [(k, agg(rs, prices)) for k, rs in groups]
        sub = agg(wr, prices)
        for (m, e), a in rows1:
            t1.append(row([label[w], m, e], a))
        t1.append(row([label[w], "SUBTOTAL", ""], sub))
        g2 = collections.defaultdict(list)
        for r in wr:
            g2[("subagent", r["agent"]) if r["sub"] else ("main", "")].append(r)
        rows2 = [(k, agg(rs, prices)) for k, rs in sorted(g2.items())]
        for (split, agent), a in rows2:
            t2.append([label[w], split, agent, *row([], a)])
        blocks.append(
            dict(
                week=w,
                rows1=[a for _, a in rows1],
                rows2=[a for _, a in rows2],
                subtotal=sub,
            )
        )
    total = agg(recs, prices)
    t1.append(row(["ALL", "TOTAL", ""], total))
    return t1, t2, blocks, total


# --------------------------------------------------------------- invariants
def reconciliation(scan_: Scan, recs, in_range) -> dict:
    raw = [r for r in scan_.raw if in_range(r["ts"].date())]
    raw_sums = {k: sum(r[k] for r in raw) for k in TOKEN_FIELDS}
    dedup_sums = {k: sum(r[k] for r in recs) for k in TOKEN_FIELDS}
    return dict(
        method="raw = every usage line in range, no dedup; dedup = one line per message.id/requestId "
        "(largest output_tokens kept)",
        raw_lines=len(raw),
        dedup_calls=len(recs),
        duplicate_lines=len(raw) - len(recs),
        raw_sums=raw_sums,
        dedup_sums=dedup_sums,
        removed_by_dedup={k: raw_sums[k] - dedup_sums[k] for k in TOKEN_FIELDS},
    )


def check_invariants(recon, blocks, total, recs, profile_names) -> dict:
    results = {}
    failures = []
    bad = [k for k in TOKEN_FIELDS if recon["raw_sums"][k] < recon["dedup_sums"][k]]
    results["raw_sum_ge_dedup_sum"] = "fail" if bad else "pass"
    if bad:
        failures.append(f"raw_sum_ge_dedup_sum: raw < dedup for {bad}")
    keys = (*TOKEN_FIELDS, "calls")
    errs = []
    for b in blocks:
        for rows_name in ("rows1", "rows2"):
            for k in keys:
                if sum(a[k] for a in b[rows_name]) != b["subtotal"][k]:
                    errs.append(f"{b['week']} {rows_name} {k}")
            cws = [a["cw_in"] for a in b[rows_name]]
            if (
                b["subtotal"]["cw_in"] is not None
                and sum(cws, Decimal(0)) != b["subtotal"]["cw_in"]
            ):
                errs.append(f"{b['week']} {rows_name} cost_weighted_input")
    for k in keys:
        if sum(b["subtotal"][k] for b in blocks) != total[k]:
            errs.append(f"weekly subtotals vs TOTAL {k}")
    for k in TOKEN_FIELDS:
        if total[k] != recon["dedup_sums"][k]:
            errs.append(f"TOTAL vs dedup sum {k}")
    results["weekly_subtotals_sum_to_total"] = "fail" if errs else "pass"
    if errs:
        failures.append("weekly_subtotals_sum_to_total: " + ", ".join(errs[:10]))
    unknown = sorted({r["profile"] for r in recs} - set(profile_names))
    results["every_row_has_known_profile"] = "fail" if unknown else "pass"
    if unknown:
        failures.append(f"every_row_has_known_profile: {unknown}")
    if failures:
        raise InvariantError("; ".join(failures))
    return results


# --------------------------------------------------------------- diagnostics
def stream_groups(recs):
    g = collections.defaultdict(list)
    for r in recs:
        g[r["stream"]].append(r)
    for rs in g.values():
        rs.sort(key=lambda r: (r["ts"], r["key"]))
    return g


def assumed_window(rs) -> int:
    return (
        CONTEXT_WINDOW_LARGE
        if any(ctx(r) > CONTEXT_WINDOW_STANDARD for r in rs)
        else CONTEXT_WINDOW_STANDARD
    )


def rewrites(streams) -> dict:
    """Calls after an idle gap that re-wrote most of the context to the cache."""
    out = {}
    for kind in ("main", "sub"):
        buckets = {
            b: dict(events=0, tokens=0, w5=0, w1=0, streams=collections.Counter())
            for b in ("<5m", "5m-1h", ">1h")
        }
        for key, rs in streams.items():
            if (key[1] == "main") != (kind == "main"):
                continue
            for a, b in zip(rs, rs[1:]):
                c = ctx(b)
                written = b["w5"] + b["w1"]
                if (
                    c <= REWRITE_MIN_CONTEXT
                    or Decimal(written) <= REWRITE_MIN_WRITE_SHARE * c
                ):
                    continue
                gap = (b["ts"] - a["ts"]).total_seconds()
                bucket = (
                    ">1h"
                    if gap > IDLE_GAP_1H_SECONDS
                    else "5m-1h"
                    if gap > IDLE_GAP_5M_SECONDS
                    else "<5m"
                )
                x = buckets[bucket]
                x["events"] += 1
                x["tokens"] += written
                x["w5"] += b["w5"]
                x["w1"] += b["w1"]
                x["streams"][key[2]] += written
        out[kind] = {
            b: dict(
                events=x["events"],
                tokens=x["tokens"],
                cache_write_5m=x["w5"],
                cache_write_1h=x["w1"],
                top_ids=[
                    k
                    for k, _ in sorted(
                        x["streams"].items(), key=lambda kv: (-kv[1], kv[0])
                    )[:5]
                ],
            )
            for b, x in buckets.items()
        }
    return out


def diagnostics(recs, scan_: Scan, in_range, prices: Prices) -> dict:
    D: dict = {}
    streams = stream_groups(recs)
    main = {k: rs for k, rs in streams.items() if k[1] == "main"}
    subs = {k: rs for k, rs in streams.items() if k[1] == "sub"}

    sessions = []
    for (prof, _, sess), rs in main.items():
        pk = max(rs, key=lambda r: (ctx(r), r["key"]))
        win = assumed_window(rs)
        sessions.append(
            dict(
                profile=prof,
                session=sess,
                start=rs[0]["ts"].date(),
                calls=len(rs),
                peak_context=ctx(pk),
                last_context=ctx(rs[-1]),
                first_context=ctx(rs[0]),
                model=pk["model"],
                assumed_window=win,
                ended_near_limit=Decimal(ctx(rs[-1])) >= NEAR_LIMIT_FRACTION * win,
            )
        )
    sessions.sort(key=lambda x: (-x["peak_context"], x["session"]))
    D["sessions"] = sessions
    D["peak_context_buckets"] = {
        str(b): sum(x["peak_context"] > b for x in sessions) for b in PEAK_BUCKETS
    }
    D["sessions_near_context_limit"] = dict(
        rule=f"last call context >= {NEAR_LIMIT_FRACTION} x assumed window "
        f"({CONTEXT_WINDOW_LARGE} if any call > {CONTEXT_WINDOW_STANDARD}, else {CONTEXT_WINDOW_STANDARD})",
        count=sum(x["ended_near_limit"] for x in sessions),
        sessions=[x["session"] for x in sessions if x["ended_near_limit"]],
    )

    comp = sorted(
        (c for c in scan_.compacts.values() if c["ts"] and in_range(c["ts"].date())),
        key=lambda c: (c["ts"], c["session"]),
    )
    D["compactions"] = dict(
        total=len(comp),
        by_trigger=dict(collections.Counter(c["trigger"] for c in comp)),
        events=[
            dict(
                profile=c["profile"],
                session=c["session"],
                run=c["run"],
                ts=c["ts"],
                trigger=c["trigger"],
                pre_tokens=c["pre_tokens"],
                post_tokens=c["post_tokens"],
            )
            for c in comp
        ],
    )
    lim = sorted(
        (x for x in scan_.limits.values() if x["ts"] and in_range(x["ts"].date())),
        key=lambda x: (x["ts"], x["session"]),
    )
    D["limit_hits"] = dict(
        rule="assistant line with model <synthetic> and error == rate_limit",
        total=len(lim),
        sessions=sorted({x["session"] for x in lim}),
        events=[
            dict(profile=x["profile"], session=x["session"], ts=x["ts"]) for x in lim
        ],
    )

    firsts = [x["first_context"] for x in sessions]
    D["start_context"] = dict(
        rule="context of the first API call of each main session (input + cache write + cache read)",
        sessions=len(firsts),
        median=median_int(firsts),
        p90=p90_int(firsts),
        by_profile={
            p: median_int(x["first_context"] for x in sessions if x["profile"] == p)
            for p in sorted({x["profile"] for x in sessions})
        },
    )

    # tool results
    uses = {
        k: v for k, v in scan_.tool_uses.items() if v["ts"] and in_range(v["ts"].date())
    }
    results = {
        k: v
        for k, v in scan_.tool_results.items()
        if v["ts"] and in_range(v["ts"].date())
    }
    by_tool = collections.defaultdict(lambda: dict(count=0, total_chars=0, max_chars=0))
    top = []
    for tid, tr in results.items():
        name = uses.get(tid, scan_.tool_uses.get(tid, {})).get("name", "unknown")
        b = by_tool[name]
        b["count"] += 1
        b["total_chars"] += tr["chars"]
        b["max_chars"] = max(b["max_chars"], tr["chars"])
        top.append(
            (tr["chars"], name, "sub" if tr["sub"] else "main", tr["ts"].date(), tid)
        )
    D["tool_results_by_tool"] = sorted(
        (
            dict(tool=k, est_tokens=v["total_chars"] // CHARS_PER_TOKEN, **v)
            for k, v in by_tool.items()
        ),
        key=lambda x: (-x["total_chars"], x["tool"]),
    )
    top.sort(key=lambda x: (-x[0], x[4]))
    D["largest_tool_results"] = [
        dict(chars=c, tool=n, where=w, date=d, tool_use_id=t)
        for c, n, w, d, t in top[:TOP_N]
    ]
    large = [x for x in top if x[0] > LARGE_TOOL_RESULT_CHARS]
    D["large_tool_results"] = dict(
        threshold_chars=LARGE_TOOL_RESULT_CHARS,
        count=len(large),
        by_tool={
            n: dict(
                count=sum(1 for x in large if x[1] == n),
                chars_over=sum(
                    x[0] - LARGE_TOOL_RESULT_CHARS for x in large if x[1] == n
                ),
            )
            for n in sorted({x[1] for x in large})
        },
    )

    # repeated reads per stream
    reads = collections.defaultdict(list)
    for tid, u in sorted(uses.items(), key=lambda kv: (kv[1]["ts"], kv[0])):
        if u["name"] == "Read" and u["file_path"]:
            reads[(u["stream"], u["file_path"])].append(tid)
    rep = []
    for (stream, path), tids in reads.items():
        if len(tids) >= REPEATED_READ_MIN:
            extra_chars = sum(results.get(t, {}).get("chars", 0) for t in tids[1:])
            rep.append(
                dict(
                    stream=stream[2],
                    kind=stream[1],
                    file_path=path,
                    reads=len(tids),
                    extra_chars=extra_chars,
                )
            )
    rep.sort(
        key=lambda x: (-x["reads"], -x["extra_chars"], x["stream"], x["file_path"])
    )
    D["repeated_reads"] = dict(
        min_reads=REPEATED_READ_MIN,
        pairs=len(rep),
        extra_reads=sum(x["reads"] - 1 for x in rep),
        extra_chars=sum(x["extra_chars"] for x in rep),
        worst=rep[:TOP_N],
    )

    # subagent re-reads of files the parent read before the subagent started
    parent_first = {}
    for tid, u in uses.items():
        if u["name"] == "Read" and u["file_path"] and u["stream"][1] == "main":
            k = (u["stream"][0], u["session"], u["file_path"])
            parent_first[k] = min(parent_first.get(k, u["ts"]), u["ts"])
    sub_start = {k[2]: rs[0]["ts"] for k, rs in subs.items()}
    sub_reads = overlap = overlap_chars = 0
    for tid, u in uses.items():
        if u["name"] == "Read" and u["file_path"] and u["stream"][1] == "sub":
            sub_reads += 1
            pt = parent_first.get((u["stream"][0], u["session"], u["file_path"]))
            st = sub_start.get(u["stream"][2])
            if pt and st and pt < st:
                overlap += 1
                overlap_chars += results.get(tid, {}).get("chars", 0)
    D["subagent_rereads"] = dict(
        subagent_reads=sub_reads,
        already_read_by_parent=overlap,
        already_read_chars=overlap_chars,
    )

    D["cache_rewrites"] = dict(
        rule=f"call after the gap writes > {REWRITE_MIN_WRITE_SHARE} of a context > {REWRITE_MIN_CONTEXT}",
        **rewrites(streams),
    )

    # subagent runs, rounds, verdicts
    runs = []
    for (prof, _, run), rs in subs.items():
        a = agg(rs, prices)
        runs.append(
            dict(
                run=run,
                profile=prof,
                session=rs[0]["session"],
                agent=rs[0]["agent"],
                model=rs[0]["model"],
                effort=rs[0]["effort"],
                date=rs[0]["ts"].date(),
                start=rs[0]["ts"],
                calls=a["calls"],
                total_input=a["inp"] + a["w5"] + a["w1"] + a["read"],
                output=a["out"],
                total_tokens=a["inp"] + a["w5"] + a["w1"] + a["read"] + a["out"],
                peak_context=max(ctx(r) for r in rs),
                cost_weighted_usd=None
                if a["cw_in"] is None
                else q(a["cw_in"] + a["cw_out"]),
                minutes=q(
                    Decimal(int((rs[-1]["ts"] - rs[0]["ts"]).total_seconds())) / 60,
                    TENTH,
                ),
                verdict=scan_.reviewer_final.get(run)
                if rs[0]["agent"] == "reviewer"
                else None,
            )
        )
    runs.sort(key=lambda x: (x["start"], x["run"]))
    by_agent = collections.defaultdict(list)
    for x in runs:
        by_agent[x["agent"]].append(x)
    D["subagent_by_type"] = {
        k: dict(
            runs=len(v),
            median_calls=median_int(x["calls"] for x in v),
            median_total_tokens=median_int(x["total_tokens"] for x in v),
            total_tokens=sum(x["total_tokens"] for x in v),
            models=dict(collections.Counter(f"{x['model']}/{x['effort']}" for x in v)),
        )
        for k, v in sorted(by_agent.items())
    }
    D["top_subagent_runs"] = sorted(runs, key=lambda x: (-x["total_tokens"], x["run"]))[
        :TOP_N
    ]
    rounds = collections.defaultdict(
        lambda: dict(
            builder_runs=0,
            reviewer_runs=0,
            builder_tokens=0,
            reviewer_tokens=0,
            approve=0,
            changes=0,
            unclear=0,
        )
    )
    for x in runs:
        if x["agent"] in ("builder", "reviewer"):
            r = rounds[x["session"]]
            r[f"{x['agent']}_runs"] += 1
            r[f"{x['agent']}_tokens"] += x["total_tokens"]
            if x["verdict"]:
                r[x["verdict"]] += 1
    br = [dict(session=k, **v) for k, v in sorted(rounds.items())]
    reviewer_runs = [x for x in runs if x["agent"] == "reviewer"]
    D["builder_reviewer"] = dict(
        sessions=br,
        sessions_with_rounds=len(br),
        median_reviewer_rounds=median_int(
            x["reviewer_runs"] for x in br if x["reviewer_runs"]
        ),
        median_tokens_per_builder_run=median_int(
            x["total_tokens"] for x in runs if x["agent"] == "builder"
        ),
        median_tokens_per_reviewer_run=median_int(
            x["total_tokens"] for x in reviewer_runs
        ),
    )
    D["reviewer_verdicts"] = dict(
        rule="exact line 'VERDICT: APPROVE' or 'VERDICT: CHANGES' in the reviewer's final text; else unclear",
        **{
            k: sum(1 for x in reviewer_runs if x["verdict"] == k)
            for k in ("approve", "changes", "unclear")
        },
    )
    D["subagent_runs"] = runs
    return D


# ----------------------------------------------------------- before / after
def metric_values(recs, scan_: Scan, lo: dt.date, hi: dt.date, prices: Prices) -> dict:
    """Metrics over records with lo <= date < hi."""
    rs = [r for r in recs if lo <= r["ts"].date() < hi]
    a = agg(rs, prices)
    streams = stream_groups(rs)
    peaks = [max(ctx(r) for r in v) for v in streams.values()]
    firsts = [ctx(v[0]) for k, v in streams.items() if k[1] == "main"]
    sessions = a["sessions"]
    ins = a["inp"] + a["w5"] + a["w1"] + a["read"]
    sess_ids = {r["session"] for r in rs}
    comp = [
        c
        for c in scan_.compacts.values()
        if c["ts"] and lo <= c["ts"].date() < hi and c["session"] in sess_ids
    ]
    lim = [
        x
        for x in scan_.limits.values()
        if x["ts"] and lo <= x["ts"].date() < hi and x["session"] in sess_ids
    ]
    return dict(
        sessions=sessions,
        calls=a["calls"],
        input_uncached=a["inp"],
        cache_write_5m=a["w5"],
        cache_write_1h=a["w1"],
        cache_read=a["read"],
        output=a["out"],
        cache_hit_pct=a["hit"],
        write_share_pct=pct(a["w5"] + a["w1"], ins),
        median_peak_context=median_int(peaks),
        max_peak_context=max(peaks) if peaks else None,
        median_first_call_context=median_int(firsts),
        tokens_per_session=(ins + a["out"]) // sessions if sessions else None,
        cost_weighted_input_usd=q(a["cw_in"]),
        compactions=len(comp),
        auto_compactions=sum(1 for c in comp if c["trigger"] == "auto"),
        limit_hits=len(lim),
    )


def as_number(v):
    """int or Decimal; money read back from JSON is a decimal string."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, Decimal)):
        return v
    if isinstance(v, str):
        try:
            return Decimal(v)
        except ArithmeticError:
            return None
    return None


def delta(before, after):
    before, after = as_number(before), as_number(after)
    if before is None or after is None:
        return None, None
    d = Decimal(after) - Decimal(before)
    return d, (q(100 * d / Decimal(before), TENTH) if before else None)


def compare(metrics, before: dict, after: dict) -> dict:
    enough = (
        before["sessions"] >= MIN_SESSIONS_PER_SIDE
        and after["sessions"] >= MIN_SESSIONS_PER_SIDE
    )
    rows = {}
    for m in metrics:
        d, p = delta(before.get(m), after.get(m))
        rows[m] = dict(before=before.get(m), after=after.get(m), delta=d, delta_pct=p)
    return dict(
        sessions_before=before["sessions"],
        sessions_after=after["sessions"],
        result="compared" if enough else "insufficient data",
        metrics=rows,
    )


def before_after(changes, recs, scan_, prices, range_end: dt.date) -> list[dict]:
    out = []
    for c in changes:
        scoped = [
            r for r in recs if c["scope"] == "all" or (c["scope"] == "main") != r["sub"]
        ]
        cut = c["date"]
        lo = cut - dt.timedelta(days=BEFORE_AFTER_WINDOW_DAYS)
        hi = min(
            cut + dt.timedelta(days=BEFORE_AFTER_WINDOW_DAYS),
            range_end + dt.timedelta(days=1),
        )
        b = metric_values(scoped, scan_, lo, cut, prices)
        a = metric_values(scoped, scan_, cut, max(hi, cut), prices)
        res = compare(c["metrics"], b, a)
        out.append(
            dict(
                date=cut,
                description=c["description"],
                scope=c["scope"],
                window_before=[lo, cut - dt.timedelta(days=1)],
                window_after=[cut, max(hi, cut) - dt.timedelta(days=1)],
                **res,
            )
        )
    return out


def previous_run(out_root: Path, out_dir: Path, summary: dict) -> dict | None:
    cands = []
    for d in sorted(p for p in out_root.glob("*") if p.is_dir()):
        if d.resolve() == out_dir.resolve():
            continue
        if (d / "diagnostics.json").is_file():
            cands.append(d)
    if not cands:
        return None
    prev = cands[-1]
    res = dict(path=prev, has_manifest=(prev / "manifest.json").is_file())
    try:
        pd = json.loads(
            (prev / "diagnostics.json").read_text(encoding="utf-8"), parse_float=Decimal
        )
    except (OSError, ValueError) as exc:
        return dict(res, comparable=False, reason=f"diagnostics.json unreadable: {exc}")
    ps = pd.get("summary") if isinstance(pd, dict) else None
    if not isinstance(ps, dict) or "sessions" not in ps:
        return dict(
            res,
            comparable=False,
            reason="older format: diagnostics.json has no summary block",
        )
    return dict(
        res,
        comparable=True,
        date_range=pd.get("date_range"),
        **compare([m for m in METRICS if m in ps], ps, summary),
    )


# ----------------------------------------------------------------- inventory
def frontmatter(text: str) -> dict:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out, key = {}, None
    for ln in lines[1:]:
        if ln.strip() == "---":
            break
        m = re.match(r"^([A-Za-z][\w-]*):\s*(.*)$", ln)
        if m:
            key, val = m.group(1), m.group(2).strip()
            out[key] = val
        elif key and ln.strip().startswith("- "):
            out[key] = (out[key] + "," if out[key] else "") + ln.strip()[2:].strip()
    return out


def file_size(p: Path) -> dict:
    n = p.stat().st_size
    return dict(path=p, bytes=n, approx_tokens=n // CHARS_PER_TOKEN)


def inventory(profiles, home: Path, scan_: Scan) -> dict:
    inv = {"approx_tokens_rule": f"bytes / {CHARS_PER_TOKEN}", "profiles": {}}
    for name, root, _ in profiles:
        p: dict = {"path": root, "exists": root.is_dir()}
        if not root.is_dir():
            inv["profiles"][name] = p
            continue
        sp = root / "settings.json"
        if sp.is_file():
            try:
                st = json.loads(sp.read_text(encoding="utf-8"), parse_float=Decimal)
            except (OSError, ValueError):
                st = None
            if isinstance(st, dict):
                env = st.get("env") if isinstance(st.get("env"), dict) else {}
                hooks = st.get("hooks") if isinstance(st.get("hooks"), dict) else {}
                p["settings"] = dict(
                    path=sp,
                    keys={k: st[k] for k in SETTINGS_KEYS if k in st},
                    env={k: env[k] for k in SETTINGS_ENV_KEYS if k in env},
                    hooks={
                        ev: len(v) if isinstance(v, list) else 1
                        for ev, v in sorted(hooks.items())
                    },
                    enabled_plugins=sorted(
                        k for k, v in (st.get("enabledPlugins") or {}).items() if v
                    )
                    if isinstance(st.get("enabledPlugins"), dict)
                    else [],
                )
            else:
                p["settings"] = dict(path=sp, error="unreadable or not a JSON object")
        cj = (
            home / ".claude.json"
            if root.resolve() == (home / ".claude").resolve()
            else root / ".claude.json"
        )
        if cj.is_file():
            try:
                cd = json.loads(cj.read_text(encoding="utf-8"))
                servers = cd.get("mcpServers") if isinstance(cd, dict) else None
                p["mcp_servers"] = dict(
                    path=cj, names=sorted(servers) if isinstance(servers, dict) else []
                )
            except (OSError, ValueError):
                p["mcp_servers"] = dict(path=cj, error="unreadable")
        cm = root / "CLAUDE.md"
        p["claude_md"] = file_size(cm) if cm.is_file() else None
        mem = sorted((root / "projects").glob("*/memory/**/*.md"))
        p["memory_files"] = dict(
            count=len(mem),
            bytes=sum(m.stat().st_size for m in mem),
            files=[file_size(m) for m in mem],
        )
        p["memory_files"]["approx_tokens"] = (
            p["memory_files"]["bytes"] // CHARS_PER_TOKEN
        )
        skills = []
        for sk in sorted((root / "skills").glob("*/SKILL.md")):
            fm = frontmatter(sk.read_text(encoding="utf-8", errors="replace"))
            skills.append(
                dict(
                    file_size(sk),
                    name=sk.parent.name,
                    description_bytes=len(fm.get("description", "").encode("utf-8")),
                    disable_model_invocation=fm.get("disable-model-invocation")
                    == "true",
                )
            )
        p["skills"] = skills
        agents = []
        for ag in sorted((root / "agents").glob("*.md")):
            fm = frontmatter(ag.read_text(encoding="utf-8", errors="replace"))
            item = dict(file_size(ag), name=ag.stem)
            for k in AGENT_KEYS:
                if k in fm:
                    v = fm[k]
                    item[k] = (
                        [t.strip() for t in v.strip("[]").split(",") if t.strip()]
                        if k.endswith("ools")
                        else v
                    )
            agents.append(item)
        p["agents"] = agents
        inv["profiles"][name] = p
    proj = {}
    for (prof, cwd, sess), _ in sorted(scan_.cwds.items()):
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md"):
            f = Path(cwd) / rel
            try:
                if f.is_file():
                    e = proj.setdefault(str(f), dict(file_size(f), sessions=set()))
                    e["sessions"].add(sess)
            except OSError:
                continue
    inv["project_claude_md"] = sorted(
        (dict(v, sessions=len(v["sessions"])) for v in proj.values()),
        key=lambda x: (-x["sessions"], str(x["path"])),
    )
    return inv


# ------------------------------------------------------------------ findings
RULES = {
    "cache_rewrite_after_idle": (
        "measured",
        "cache-write tokens on rewrite calls in the bucket / weeks in range",
    ),
    "peak_context_over_threshold": (
        "measured",
        f"sum over main calls of max(0, context - {PEAK_CONTEXT_THRESHOLD}) "
        "/ weeks in range",
    ),
    "start_context_size": (
        "measured",
        f"(median first-call context - {START_CONTEXT_THRESHOLD}) x main calls "
        "/ weeks in range",
    ),
    "reviewer_rounds_over_threshold": (
        "measured",
        f"tokens of builder+reviewer runs after round "
        f"{REVIEWER_ROUNDS_THRESHOLD} in each session / weeks in range",
    ),
    "repeated_file_reads": (
        "inferred",
        f"result chars of the 2nd and later reads / {CHARS_PER_TOKEN} / weeks",
    ),
    "large_tool_results": (
        "inferred",
        f"sum of (chars - {LARGE_TOOL_RESULT_CHARS}) / {CHARS_PER_TOKEN} / weeks",
    ),
    "subagent_rereads_parent_files": (
        "inferred",
        f"result chars / {CHARS_PER_TOKEN} / weeks",
    ),
    "sessions_near_context_limit": (
        "inferred",
        "0 (quality signal; no token estimate)",
    ),
    "usage_limit_hits": ("measured", "0 (quality signal; no token estimate)"),
    "reviewer_verdict_unclear": ("measured", "0 (quality signal; no token estimate)"),
    "auto_compactions": ("measured", "0 (quality signal; no token estimate)"),
    "unpriced_models": (
        "measured",
        "0 (cost weights missing; add the model to prices.json)",
    ),
}


def findings(D, recs, weeks_in_range: Decimal, unknown_models: dict) -> list[dict]:
    out = []

    def add(fid, rule, measured, threshold, evidence, tokens):
        conf_name = RULES[rule][0]
        est = (
            int(
                (Decimal(tokens) / weeks_in_range).quantize(
                    Decimal(1), rounding=ROUND_HALF_UP
                )
            )
            if tokens
            else 0
        )
        out.append(
            dict(
                id=fid,
                rule=rule,
                measured=measured,
                threshold=threshold,
                evidence=evidence,
                est_weekly_tokens=est,
                formula=RULES[rule][1],
                confidence=conf_name,
                confidence_value=CONFIDENCE[conf_name],
                score=int(
                    (est * CONFIDENCE[conf_name]).quantize(
                        Decimal(1), rounding=ROUND_HALF_UP
                    )
                ),
            )
        )

    for kind in ("main", "sub"):
        for bucket in ("5m-1h", ">1h"):
            x = D["cache_rewrites"][kind][bucket]
            if x["events"] >= 1:
                add(
                    f"cache_rewrite_after_idle:{kind}:{bucket}",
                    "cache_rewrite_after_idle",
                    x["tokens"],
                    ">= 1 rewrite event",
                    dict(events=x["events"], top_ids=x["top_ids"]),
                    x["tokens"],
                )
    main_recs = [r for r in recs if not r["sub"]]
    over = [r for r in main_recs if ctx(r) > PEAK_CONTEXT_THRESHOLD]
    if over:
        sess = sorted({r["session"] for r in over})
        add(
            "peak_context_over_threshold",
            "peak_context_over_threshold",
            len(sess),
            f"> {PEAK_CONTEXT_THRESHOLD}",
            dict(sessions=len(sess), calls=len(over), session_ids=sess[:TOP_N]),
            sum(ctx(r) - PEAK_CONTEXT_THRESHOLD for r in over),
        )
    med = D["start_context"]["median"]
    if med is not None and med > START_CONTEXT_THRESHOLD:
        add(
            "start_context_size",
            "start_context_size",
            med,
            f"> {START_CONTEXT_THRESHOLD}",
            dict(
                sessions=D["start_context"]["sessions"],
                p90=D["start_context"]["p90"],
                by_profile=D["start_context"]["by_profile"],
                main_calls=len(main_recs),
            ),
            (med - START_CONTEXT_THRESHOLD) * len(main_recs),
        )
    runs = D["subagent_runs"]
    extra, heavy = 0, []
    for s in D["builder_reviewer"]["sessions"]:
        if s["reviewer_runs"] > REVIEWER_ROUNDS_THRESHOLD:
            heavy.append(s["session"])
            for agent in ("builder", "reviewer"):
                rr = [
                    x
                    for x in runs
                    if x["session"] == s["session"] and x["agent"] == agent
                ]
                extra += sum(x["total_tokens"] for x in rr[REVIEWER_ROUNDS_THRESHOLD:])
    if heavy:
        add(
            "reviewer_rounds_over_threshold",
            "reviewer_rounds_over_threshold",
            len(heavy),
            f"> {REVIEWER_ROUNDS_THRESHOLD} reviewer runs per session",
            dict(session_ids=heavy[:TOP_N]),
            extra,
        )
    rr = D["repeated_reads"]
    if rr["pairs"]:
        add(
            "repeated_file_reads",
            "repeated_file_reads",
            rr["extra_reads"],
            f">= {REPEATED_READ_MIN} reads",
            dict(
                pairs=rr["pairs"], stream_ids=sorted({x["stream"] for x in rr["worst"]})
            ),
            rr["extra_chars"] // CHARS_PER_TOKEN,
        )
    for tool, v in D["large_tool_results"]["by_tool"].items():
        add(
            f"large_tool_results:{tool}",
            "large_tool_results",
            v["count"],
            f"> {LARGE_TOOL_RESULT_CHARS} chars",
            dict(tool=tool, count=v["count"]),
            v["chars_over"] // CHARS_PER_TOKEN,
        )
    sr = D["subagent_rereads"]
    if sr["already_read_by_parent"]:
        add(
            "subagent_rereads_parent_files",
            "subagent_rereads_parent_files",
            sr["already_read_by_parent"],
            ">= 1",
            dict(subagent_reads=sr["subagent_reads"]),
            sr["already_read_chars"] // CHARS_PER_TOKEN,
        )
    nl = D["sessions_near_context_limit"]
    if nl["count"]:
        add(
            "sessions_near_context_limit",
            "sessions_near_context_limit",
            nl["count"],
            f">= {NEAR_LIMIT_FRACTION} x assumed window",
            dict(session_ids=nl["sessions"][:TOP_N]),
            0,
        )
    lh = D["limit_hits"]
    if lh["total"]:
        add(
            "usage_limit_hits",
            "usage_limit_hits",
            lh["total"],
            ">= 1",
            dict(session_ids=lh["sessions"][:TOP_N]),
            0,
        )
    rv = D["reviewer_verdicts"]
    if rv["unclear"]:
        add(
            "reviewer_verdict_unclear",
            "reviewer_verdict_unclear",
            rv["unclear"],
            ">= 1",
            dict(
                approve=rv["approve"],
                changes=rv["changes"],
                unclear=rv["unclear"],
                run_ids=[x["run"] for x in runs if x["verdict"] == "unclear"][:TOP_N],
            ),
            0,
        )
    auto = D["compactions"]["by_trigger"].get("auto", 0)
    if auto:
        add(
            "auto_compactions",
            "auto_compactions",
            auto,
            ">= 1",
            dict(
                session_ids=sorted(
                    {
                        c["session"]
                        for c in D["compactions"]["events"]
                        if c["trigger"] == "auto"
                    }
                )[:TOP_N]
            ),
            0,
        )
    if unknown_models:
        add(
            "unpriced_models",
            "unpriced_models",
            len(unknown_models),
            ">= 1",
            dict(models=unknown_models),
            0,
        )
    out.sort(key=lambda f: (-f["score"], -f["est_weekly_tokens"], f["id"]))
    return out


# -------------------------------------------------------------------- report
def md_table(rows) -> str:
    head, *body = rows
    lines = ["| " + " | ".join(str(h) for h in head) + " |", "|" + "---|" * len(head)]
    lines += [
        "| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in body
    ]
    return "\n".join(lines)


def render_report(meta, t1, t2, D, F, recon, unknown_models, partial) -> str:
    L = [
        "# Claude Code usage review",
        "",
        f"Generated by `usage_report.py` {VERSION} on {meta['run_date']}. "
        f"Data {meta['data_start']} to {meta['data_end']}; profiles: {', '.join(meta['profiles'])}.",
        f"Prices: `{meta['prices_path']}` (checked {meta['prices_checked']}); cost-weighted columns are API list "
        "prices used as weights. A cost cell is empty if the row has a model with no price.",
        "",
    ]
    if partial:
        L += [
            "Partial weeks: "
            + "; ".join(f"{w} ({r})" for w, r in sorted(partial.items())),
            "",
        ]
    if unknown_models:
        L += [
            "Unpriced models (calls): "
            + ", ".join(f"{m} ({n})" for m, n in sorted(unknown_models.items())),
            "",
        ]
    L += [
        "## Weekly usage by model and effort",
        "",
        md_table(t1),
        "",
        "## Weekly usage by main and subagent type",
        "",
        md_table(t2),
        "",
        "## Reconciliation",
        "",
        f"Raw usage lines {recon['raw_lines']}, deduplicated calls {recon['dedup_calls']} "
        f"({recon['duplicate_lines']} duplicate lines removed).",
        "",
        md_table(
            [["field", "raw", "dedup", "removed"]]
            + [
                [
                    k,
                    recon["raw_sums"][k],
                    recon["dedup_sums"][k],
                    recon["removed_by_dedup"][k],
                ]
                for k in TOKEN_FIELDS
            ]
        ),
        "",
        "## Diagnostics",
        "",
    ]
    sc = D["start_context"]
    L += [
        f"- Start-of-session context: median {sc['median']}, p90 {sc['p90']} tokens over {sc['sessions']} "
        f"main sessions; by profile {sc['by_profile']}.",
        f"- Peak context: sessions over each bucket {D['peak_context_buckets']}.",
        f"- Sessions that ended near the context limit: {D['sessions_near_context_limit']['count']}.",
        f"- Compactions: {D['compactions']['total']} {D['compactions']['by_trigger']}.",
        f"- Usage-limit hits: {D['limit_hits']['total']} in {len(D['limit_hits']['sessions'])} sessions.",
        f"- Repeated reads (>= {REPEATED_READ_MIN} in one stream): {D['repeated_reads']['pairs']} file/stream "
        f"pairs, {D['repeated_reads']['extra_reads']} extra reads.",
        f"- Subagent reads of files the parent had already read: "
        f"{D['subagent_rereads']['already_read_by_parent']} of {D['subagent_rereads']['subagent_reads']}.",
        f"- Reviewer verdicts: approve {D['reviewer_verdicts']['approve']}, changes "
        f"{D['reviewer_verdicts']['changes']}, unclear {D['reviewer_verdicts']['unclear']}.",
        "",
        "### Cache rewrites after idle gaps",
        "",
        md_table(
            [["stream", "gap", "events", "tokens"]]
            + [
                [k, b, v["events"], v["tokens"]]
                for k in ("main", "sub")
                for b, v in D["cache_rewrites"][k].items()
            ]
        ),
        "",
        "### Largest tool results",
        "",
        md_table(
            [["tool", "chars", "where", "date"]]
            + [
                [x["tool"], x["chars"], x["where"], x["date"]]
                for x in D["largest_tool_results"]
            ]
        ),
        "",
        "### Subagents by type",
        "",
        md_table(
            [["agent", "runs", "median calls", "median tokens", "total tokens"]]
            + [
                [
                    k,
                    v["runs"],
                    v["median_calls"],
                    v["median_total_tokens"],
                    v["total_tokens"],
                ]
                for k, v in D["subagent_by_type"].items()
            ]
        ),
        "",
        "### Before/after changes",
        "",
    ]
    if not D["before_after"]:
        L += ["No entries in changes.json.", ""]
    for c in D["before_after"]:
        L += [
            f"**{c['date']}** ({c['scope']}): {c['result']}; sessions {c['sessions_before']} before, "
            f"{c['sessions_after']} after.",
            "",
            md_table(
                [["metric", "before", "after", "delta", "delta %"]]
                + [
                    [m, v["before"], v["after"], v["delta"], v["delta_pct"]]
                    for m, v in c["metrics"].items()
                ]
            ),
            "",
        ]
    pr = D.get("previous_run")
    L += [
        "### Previous run",
        "",
        "None found."
        if not pr
        else (
            f"`{pr['path']}`: {pr['result']} (sessions {pr['sessions_before']} then {pr['sessions_after']})."
            if pr.get("comparable")
            else f"`{pr['path']}`: not comparable ({pr['reason']})."
        ),
        "",
    ]
    L += [
        "## Findings",
        "",
        "Sorted by score = est_weekly_tokens x confidence. Details in `findings.json`.",
        "",
        md_table(
            [
                [
                    "id",
                    "measured",
                    "threshold",
                    "est weekly tokens",
                    "confidence",
                    "score",
                ]
            ]
            + [
                [
                    f["id"],
                    f["measured"],
                    f["threshold"],
                    f["est_weekly_tokens"],
                    f["confidence"],
                    f["score"],
                ]
                for f in F
            ]
        ),
        "",
    ]
    return "\n".join(L)


# ---------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Weekly Claude Code token usage, diagnostics and findings."
    )
    p.add_argument("--start", help="first day, YYYY-MM-DD (local time)")
    p.add_argument("--end", help="last day, YYYY-MM-DD (local time)")
    p.add_argument(
        "--out", help="output directory (default ~/Documents/claude-usage/<today>)"
    )
    p.add_argument(
        "--profile",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="extra Claude Code config directory; repeatable",
    )
    p.add_argument(
        "--changes",
        help="changes.json (default <out-root>/changes.json, else the bundled template)",
    )
    p.add_argument(
        "--prices", help="prices.json (default: the one next to this script)"
    )
    return p


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def csv_text(rows) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def run(argv, home: Path, env: dict, today: dt.date, tz) -> int:
    args = build_parser().parse_args(argv)
    start = parse_date(args.start, "--start") if args.start else None
    end = parse_date(args.end, "--end") if args.end else None
    if start and end and start > end:
        raise InputError("--start is after --end")
    here = Path(__file__).resolve().parent
    out_dir = (
        Path(args.out).expanduser()
        if args.out
        else home / "Documents" / "claude-usage" / today.isoformat()
    )
    out_root = out_dir.parent
    prices = Prices(
        Path(args.prices).expanduser() if args.prices else here / "prices.json"
    )
    if args.changes:
        changes_path = Path(args.changes).expanduser()
        if not changes_path.is_file():
            raise InputError(f"--changes: file not found: {changes_path}")
    elif (out_root / "changes.json").is_file():
        changes_path = out_root / "changes.json"
    else:
        changes_path = here / "changes.json"
    changes = load_changes(changes_path)
    profiles = resolve_profiles(args.profile, home, env)

    sc = scan(profiles, tz)
    nfiles = sum(sc.files.values())
    if nfiles == 0:
        raise InputError(
            "no transcript files found under <profile>/projects/**/*.jsonl for profiles: "
            + ", ".join(f"{n}={p}" for n, p, _ in profiles)
        )
    drift_n = sum(sc.drift.values())
    if sc.assistant_lines == 0:
        raise InputError(
            "schema drift: no assistant lines found in the transcript files"
        )
    if Decimal(drift_n) > SCHEMA_DRIFT_MAX_FRACTION * sc.assistant_lines:
        raise InputError(
            f"schema drift: {drift_n} of {sc.assistant_lines} assistant lines lack "
            f"{dict(sc.drift)} (limit {SCHEMA_DRIFT_MAX_FRACTION:%}). The transcript format may "
            "have changed; update usage_report.py and its tests."
        )

    def in_range(d: dt.date) -> bool:
        return (start is None or d >= start) and (end is None or d <= end)

    recs = sorted(
        (r for r in sc.calls.values() if in_range(r["ts"].date())),
        key=lambda r: (r["ts"], r["key"]),
    )
    if not recs:
        raise InputError(
            f"no API calls with usage in the date range {start or 'begin'}..{end or 'end'}"
        )
    for r in recs:
        r["week"] = week_of(r["ts"].date())
    data_start, data_end = recs[0]["ts"].date(), recs[-1]["ts"].date()
    start_bound, end_bound = start or data_start, end or data_end
    weeks = sorted({r["week"] for r in recs})
    partial = partial_weeks(weeks, start_bound, end_bound, today)
    unknown_models = dict(
        sorted(
            collections.Counter(
                r["model"] for r in recs if prices.lookup(r["model"]) is None
            ).items()
        )
    )

    t1, t2, blocks, total = build_tables(recs, weeks, partial, prices)
    recon = reconciliation(sc, recs, in_range)
    invariants = check_invariants(
        recon, blocks, total, recs, [n for n, _, _ in profiles]
    )

    D = diagnostics(recs, sc, in_range, prices)
    range_days = (end_bound - start_bound).days + 1
    weeks_in_range = Decimal(range_days) / 7
    D["summary"] = metric_values(
        recs, sc, start_bound, end_bound + dt.timedelta(days=1), prices
    )
    D["before_after"] = before_after(changes, recs, sc, prices, end_bound)
    D["previous_run"] = (
        previous_run(out_root, out_dir, D["summary"]) if out_root.is_dir() else None
    )
    D["date_range"] = [data_start, data_end]
    D["partial_weeks"] = partial
    D["thresholds"] = {k: globals()[k] for k in THRESHOLD_NAMES}
    F = findings(D, recs, weeks_in_range, unknown_models)
    inv = inventory(profiles, home, sc)

    out_dir.mkdir(parents=True, exist_ok=True)
    meta = dict(
        run_date=today,
        data_start=data_start,
        data_end=data_end,
        profiles=[n for n, _, _ in profiles],
        prices_path=prices.path,
        prices_checked=prices.checked,
    )
    outputs = {
        "weekly_by_model_effort.csv": csv_text(t1),
        "weekly_by_agent.csv": csv_text(t2),
        "diagnostics.json": dumps(D),
        "inventory.json": dumps(inv),
        "findings.json": dumps(
            dict(
                weeks_in_range=q(weeks_in_range),
                confidence=CONFIDENCE,
                rules={
                    k: dict(confidence=v[0], formula=v[1]) for k, v in RULES.items()
                },
                findings=F,
            )
        ),
        "report.md": render_report(meta, t1, t2, D, F, recon, unknown_models, partial),
    }
    for name, text in outputs.items():
        write_text(out_dir / name, text)
    manifest = dict(
        tool=TOOL,
        version=VERSION,
        run_date=today,
        args=dict(
            start=args.start,
            end=args.end,
            out=out_dir,
            profile=args.profile,
            changes=args.changes,
            prices=args.prices,
        ),
        date_range=dict(
            requested_start=start,
            requested_end=end,
            data_start=data_start,
            data_end=data_end,
            weeks=weeks,
            partial_weeks=partial,
        ),
        profiles=[
            dict(
                name=n, path=p, explicit=e, exists=p.is_dir(), files=sc.files.get(n, 0)
            )
            for n, p, e in profiles
        ],
        files_scanned=nfiles,
        lines_read=sc.lines,
        assistant_lines=sc.assistant_lines,
        lines_skipped=dict(sorted(sc.skipped.items())),
        missing_fields=dict(sorted(sc.missing.items())),
        schema_drift=dict(
            lines=drift_n,
            by_field=dict(sc.drift),
            limit_fraction=SCHEMA_DRIFT_MAX_FRACTION,
        ),
        unknown_models=unknown_models,
        prices=dict(path=prices.path, checked=prices.checked, source=prices.source),
        changes=dict(path=changes_path, entries=len(changes)),
        previous_run=D["previous_run"]["path"] if D["previous_run"] else None,
        reconciliation=recon,
        invariants=invariants,
        outputs={
            n: hashlib.sha256((out_dir / n).read_bytes()).hexdigest()
            for n in sorted(outputs)
        },
    )
    write_text(out_dir / "manifest.json", dumps(manifest))
    # Invariant 4: the reconciliation is in the written manifest, and hashes match.
    check = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    rc = check.get("reconciliation") or {}
    ok = all(k in rc for k in ("raw_sums", "dedup_sums", "removed_by_dedup")) and all(
        hashlib.sha256((out_dir / n).read_bytes()).hexdigest() == h
        for n, h in check.get("outputs", {}).items()
    )
    if not ok:
        (out_dir / "manifest.json").unlink()
        raise InvariantError(
            "reconciliation_in_manifest: manifest.json is missing the reconciliation or a hash "
            "does not match"
        )
    check["invariants"]["reconciliation_in_manifest"] = "pass"
    write_text(out_dir / "manifest.json", dumps(check))
    print(
        f"OK: {len(recs)} calls, {nfiles} files, {data_start}..{data_end} -> {out_dir}"
    )
    if unknown_models:
        print(f"Unpriced models (no cost weight): {', '.join(unknown_models)}")
    return EXIT_OK


def main(
    argv=None,
    *,
    home: Path | None = None,
    env: dict | None = None,
    today: dt.date | None = None,
    tz=None,
) -> int:
    try:
        return run(
            argv,
            home or Path.home(),
            dict(os.environ) if env is None else env,
            today or dt.date.today(),
            tz,
        )
    except InputError as exc:
        print(f"ERROR (exit {EXIT_INPUT}): {exc}", file=sys.stderr)
        return EXIT_INPUT
    except InvariantError as exc:
        print(f"INVARIANT FAILED (exit {EXIT_INVARIANT}): {exc}", file=sys.stderr)
        return EXIT_INVARIANT


if __name__ == "__main__":
    sys.exit(main())
