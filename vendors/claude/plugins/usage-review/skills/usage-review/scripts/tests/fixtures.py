"""Small synthetic Claude Code profiles for the tests. No real transcript data.

Every text field that a real transcript could hold (prompts, answers, thinking,
tool input, tool output, agent descriptions, config text) contains MARKER, so the
privacy test can check that no output file contains it.
"""

from __future__ import annotations

import json
from pathlib import Path

MARKER = "ZQX-PRIVACY-MARKER-4471"
SESSION_1 = "11111111-aaaa-4aaa-8aaa-000000000001"
SESSION_2 = "22222222-bbbb-4bbb-8bbb-000000000002"
PROJECT = "-work-demo"


def usage(inp=0, w5=0, w1=0, read=0, out=0, think=0, split=True):
    u = {
        "input_tokens": inp,
        "cache_creation_input_tokens": w5 + w1,
        "cache_read_input_tokens": read,
        "output_tokens": out,
        "output_tokens_details": {"thinking_tokens": think},
    }
    if split:
        u["cache_creation"] = {
            "ephemeral_5m_input_tokens": w5,
            "ephemeral_1h_input_tokens": w1,
        }
    return u


def assistant(
    ts,
    mid,
    model,
    u,
    content=None,
    effort="high",
    per_turn=None,
    session=SESSION_1,
    **extra,
):
    d = {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": session,
        "effort": effort,
        "perTurnEffort": per_turn,
        "cwd": "/work/demo",
        "message": {
            "id": mid,
            "model": model,
            "role": "assistant",
            "usage": u,
            "content": content
            if content is not None
            else [{"type": "text", "text": f"answer {MARKER}"}],
        },
    }
    if mid is None:
        del d["message"]["id"]
    if effort is None:
        del d["effort"]
    d.update(extra)
    return d


def tool_use(tid, name, **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(ts, tid, text, session=SESSION_1):
    return {
        "type": "user",
        "timestamp": ts,
        "sessionId": session,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}],
        },
    }


def write_jsonl(path: Path, items) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for it in items:
            fh.write(it if isinstance(it, str) else json.dumps(it))
            fh.write("\n")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1), encoding="utf-8")


def build_profile_a(root: Path) -> Path:
    """Main profile: one main session with 3 subagents, all in ISO week 2026-W38."""
    proj = root / "projects" / PROJECT
    big = MARKER * 1000  # 23000 chars, a large tool result
    main = [
        {
            "type": "user",
            "timestamp": "2026-09-14T12:00:00Z",
            "sessionId": SESSION_1,
            "message": {"role": "user", "content": f"please help {MARKER}"},
        },
        assistant(
            "2026-09-14T12:00:05Z",
            "msg_m1",
            "claude-opus-5",
            usage(inp=10, w1=30000, out=100, think=20),
            content=[
                {"type": "thinking", "thinking": MARKER},
                tool_use("tu1", "Read", file_path="/work/demo/a.py"),
            ],
        ),
        # the same API response written twice (streaming): counted once
        assistant(
            "2026-09-14T12:00:05Z",
            "msg_m1",
            "claude-opus-5",
            usage(inp=10, w1=30000, out=100, think=20),
            content=[tool_use("tu1", "Read", file_path="/work/demo/a.py")],
        ),
        tool_result("2026-09-14T12:00:06Z", "tu1", big),
        assistant(
            "2026-09-14T12:01:00Z",
            "msg_m2",
            "claude-opus-5",
            usage(inp=5, read=30000, out=50),
            per_turn="medium",
            content=[
                tool_use("tu2", "Read", file_path="/work/demo/a.py"),
                tool_use("tu9", "Agent", subagent_type="builder", prompt=MARKER),
            ],
        ),
        tool_result("2026-09-14T12:01:01Z", "tu2", "x" * 100),
        assistant(
            "2026-09-14T12:02:00Z",
            "msg_m3",
            "claude-opus-5",
            usage(inp=5, read=30100, out=10),
            content=[
                tool_use("tu3", "Read", file_path="/work/demo/a.py"),
                tool_use("tu8", "Bash", command=f"echo {MARKER}"),
                # Read calls with no file_path: their other input must never be kept
                tool_use("tu5", "Read", content=MARKER),
                tool_use("tu6", "Read", content=MARKER),
                tool_use("tu7", "Read", content=MARKER),
            ],
        ),
        tool_result("2026-09-14T12:02:01Z", "tu3", "y" * 100),
        tool_result("2026-09-14T12:02:02Z", "tu8", MARKER),
        # 2h idle gap, then the whole context is written to the cache again
        assistant(
            "2026-09-14T14:10:00Z",
            "msg_m4",
            "claude-opus-5",
            usage(inp=5, w1=31000, out=40),
        ),
        {
            "type": "assistant",
            "timestamp": "2026-09-14T14:11:00Z",
            "sessionId": SESSION_1,
            "uuid": "lim-1",
            "error": "rate_limit",
            "isApiErrorMessage": True,
            "message": {
                "model": "<synthetic>",
                "role": "assistant",
                "content": [{"type": "text", "text": MARKER}],
            },
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "timestamp": "2026-09-14T14:12:00Z",
            "sessionId": SESSION_1,
            "uuid": "cmp-1",
            "content": MARKER,
            "compactMetadata": {
                "trigger": "auto",
                "preTokens": 31000,
                "postTokens": 3000,
            },
        },
        # requestId fallback (no message.id) and a model with no price
        assistant(
            "2026-09-15T12:00:00Z",
            None,
            "claude-unknown-9",
            usage(inp=1, w5=100, out=1),
            requestId="req_5",
        ),
        "{not json " + MARKER,
    ]
    write_jsonl(proj / f"{SESSION_1}.jsonl", main)
    sub = proj / SESSION_1 / "subagents"
    write_jsonl(
        sub / "agent-b1.jsonl",
        [
            assistant(
                "2026-09-14T12:03:00Z",
                "msg_b1",
                "claude-sonnet-5",
                usage(inp=3, w5=20000, out=30),
                effort="medium",
                isSidechain=True,
                content=[tool_use("tu4", "Read", file_path="/work/demo/a.py")],
            ),
            tool_result("2026-09-14T12:03:01Z", "tu4", "z" * 200),
        ],
    )
    write_json(
        sub / "agent-b1.meta.json", {"agentType": "builder", "description": MARKER}
    )
    for name, ts, mid, text in (
        (
            "agent-r1",
            "2026-09-14T12:05:00Z",
            "msg_r1",
            f"Looks fine {MARKER}\nVERDICT: APPROVE",
        ),
        (
            "agent-r2",
            "2026-09-14T12:06:00Z",
            "msg_r2",
            f"I approve this, it passes. {MARKER}",
        ),
        (
            "agent-r3",
            "2026-09-14T12:07:00Z",
            "msg_r3",
            f"Two problems {MARKER}\n  VERDICT: CHANGES  ",
        ),
    ):
        write_jsonl(
            sub / f"{name}.jsonl",
            [
                assistant(
                    ts,
                    mid,
                    "claude-sonnet-5",
                    usage(inp=2, w5=21000, out=20),
                    effort="low",
                    isSidechain=True,
                    content=[{"type": "text", "text": text}],
                ),
            ],
        )
        write_json(
            sub / f"{name}.meta.json", {"agentType": "reviewer", "description": MARKER}
        )
    # config inventory
    write_json(
        root / "settings.json",
        {
            "model": "opus",
            "autoCompactWindow": 500000,
            "subagentPromptCacheTtl": "1h",
            "statusLine": {"command": MARKER},
            "env": {"MAX_THINKING_TOKENS": "8000", "MY_SECRET": MARKER},
            "hooks": {
                "PostToolUse": [
                    {
                        "matcher": "Edit",
                        "hooks": [{"type": "command", "command": MARKER}],
                    }
                ]
            },
        },
    )
    (root / "CLAUDE.md").write_text(f"# Rules\n{MARKER}\n", encoding="utf-8")
    (proj / "memory").mkdir(parents=True, exist_ok=True)
    (proj / "memory" / "note.md").write_text(MARKER, encoding="utf-8")
    (root / "agents").mkdir(parents=True, exist_ok=True)
    (root / "agents" / "reviewer.md").write_text(
        f"---\nname: reviewer\ndescription: {MARKER}\ntools: Read, Grep\nmodel: opus\neffort: medium\n---\n{MARKER}\n",
        encoding="utf-8",
    )
    (root / "skills" / "demo").mkdir(parents=True, exist_ok=True)
    (root / "skills" / "demo" / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: {MARKER}\n---\n{MARKER}\n", encoding="utf-8"
    )
    return root


def build_profile_b(root: Path) -> Path:
    """Second profile: one call with no effort and no 5m/1h split."""
    write_jsonl(
        root / "projects" / PROJECT / f"{SESSION_2}.jsonl",
        [
            assistant(
                "2026-09-16T12:00:00Z",
                "msg_b_1",
                "claude-haiku-4-5-20251001",
                usage(inp=7, w5=500, out=3, split=False),
                effort=None,
                session=SESSION_2,
            ),
        ],
    )
    return root


def build_sessions(root: Path, start_day: int, n: int, w5: int, month: int = 9) -> None:
    """n one-call main sessions, one per day from 2026-<month>-<start_day>, noon UTC."""
    for i in range(n):
        sid = f"33333333-cccc-4ccc-8ccc-{month:02d}{start_day:02d}{i:08d}"
        day = start_day + i
        write_jsonl(
            root / "projects" / PROJECT / f"{sid}.jsonl",
            [
                assistant(
                    f"2026-{month:02d}-{day:02d}T12:00:00Z",
                    f"msg_{sid}",
                    "claude-opus-5",
                    usage(inp=1, w5=w5, read=1000, out=10),
                    session=sid,
                ),
            ],
        )
