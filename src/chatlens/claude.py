"""Claude Code adapter: ~/.claude/projects/<encoded-cwd>/<sessionId>.jsonl (+ ~/.claude-*/projects).

Facts verified on mac-studio 2026-09-24 (CLI 2.1.24x-2.1.26x):
- Subagent transcripts live at <proj>/<sessionId>/subagents/agent-<id>.jsonl (+ .meta.json);
  the path is the reliable main-vs-subagent signal.
- One API message is split over several assistant rows sharing message.id; each row holds one block.
- Human text = type=user rows whose content is a string / text block, not meta / compact summary /
  harness tags; tool_result blocks also arrive as type=user.
- Injected context (hooks, CLAUDE.md, skill listings) arrives as type=attachment -> skipped.
- thinking blocks carry only a signature (text empty) -> recorded as a redaction marker, once per message.
- compaction: user row with isCompactSummary=true holds the model-written summary.
"""
from __future__ import annotations

import glob
import json
import os
import sys

from .codex import Ambiguous
from .model import (ASSISTANT, SUMMARY, THINKING, TOOL_CALL, TOOL_RESULT, USER, Event, Thread,
                    content_text, iso_to_ts, warn_bad_lines)

HARNESS_PREFIXES = ("<task-notification>", "<local-command", "<command-", "<system-reminder>",
                    "Stop hook feedback", "Caveat:", "This session is being continued",
                    "[Request interrupted")


def roots() -> list[str]:
    base = [os.path.expanduser("~/.claude/projects")]
    base += sorted(glob.glob(os.path.expanduser("~/.claude-*/projects")))
    return [r for r in base if os.path.isdir(r)]


def _first_prompt(path: str, limit_lines: int = 400) -> tuple[str, str, str]:
    """(title, cwd, entrypoint) from the head of a transcript, cheaply."""
    cwd = entry = ""
    try:
        fh = open(path, errors="replace")
    except OSError as exc:              # one unreadable transcript must not break list/index/report
        return f"[unreadable: {exc.strerror or exc}]", "", ""
    with fh:
        for i, line in enumerate(fh):
            if i > limit_lines:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            cwd = cwd or rec.get("cwd") or ""
            entry = entry or rec.get("entrypoint") or ""
            if rec.get("type") == "user" and not _injected(rec):
                text = _user_text(rec)
                if text:
                    return text[:160].replace("\n", " "), cwd, entry
    return "", cwd, entry


def _user_text(rec: dict) -> str:
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text").strip()
    return ""


def _injected(rec: dict) -> bool:
    if rec.get("isMeta") or rec.get("isCompactSummary"):
        return True
    origin = (rec.get("origin") or {}).get("kind")
    if origin not in (None, "human"):
        return True
    text = _user_text(rec)
    return (not text) or text.lstrip().startswith(HARNESS_PREFIXES)


def list_threads(node: str, with_titles: bool = True, include_subagents: bool = False,
                 errors: list | None = None) -> list[Thread]:
    threads = []
    for root in roots():
        for proj in os.scandir(root):
            if not proj.is_dir():
                continue
            try:
                entries = list(os.scandir(proj.path))
            except OSError as exc:      # one unreadable project must not hide every other project
                if errors is not None:
                    errors.append({"source": "claude", "status": "partial",
                                   "error": f"project unreadable: {proj.path}: {exc.strerror or exc}"[:300]})
                print(f"chatlens: claude project unreadable, skipped: {proj.path}: {exc.strerror or exc}",
                      file=sys.stderr)
                continue
            for entry in entries:
                if entry.is_file() and entry.name.endswith(".jsonl"):
                    try:
                        st = entry.stat()
                    except OSError as exc:
                        if errors is not None:
                            errors.append({"source": "claude", "status": "partial",
                                           "error": f"transcript vanished before stat: {entry.path}: {exc}"[:300]})
                        continue          # vanished between scandir and stat
                    threads.append(_thread(node, entry.path, "human", with_titles, st))
                elif include_subagents and entry.is_dir():
                    sub = os.path.join(entry.path, "subagents")
                    if os.path.isdir(sub):
                        for f in os.scandir(sub):
                            if f.name.endswith(".jsonl"):
                                threads.append(_thread(node, f.path, "subagent", with_titles))
    return threads


def _thread(node: str, path: str, origin: str, with_titles: bool, st=None) -> Thread:
    st = st or os.stat(path)
    title = cwd = entry = ""
    if with_titles and st.st_size:
        title, cwd, entry = _first_prompt(path)
    sid = os.path.basename(path)[:-6]
    if origin == "human" and entry in ("sdk-cli", "sdk-py", "sdk-ts"):
        origin = "sdk"
    return Thread(source="claude", id=sid, node=node, path=path, title=title,
                  started=st.st_birthtime if hasattr(st, "st_birthtime") else st.st_ctime,
                  updated=st.st_mtime, cwd=cwd, origin=origin, size_bytes=st.st_size,
                  extra={"entrypoint": entry})


def resolve(ref: str) -> str | None:
    if os.path.exists(ref):
        return ref
    for root in roots():
        hits = glob.glob(os.path.join(root, "*", f"{glob.escape(ref)}*.jsonl"))
        exact = [h for h in hits if os.path.basename(h) == ref + ".jsonl"]
        if exact or len(hits) == 1:
            return (exact or hits)[0]
        if len(hits) > 1:
            raise Ambiguous(ref, ["claude:" + os.path.basename(h)[:-6] for h in hits[:6]])
    return None


def events(path: str, errors: list | None = None):
    tool_names: dict[str, str] = {}
    seen_thinking: set[str] = set()
    bad = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                # A final line with no newline is a chat still being written, not damage: skip it quietly.
                bad += line.strip() != "" and line.endswith("\n")
                continue
            if not isinstance(rec, dict):
                bad += 1
                continue
            rtype = rec.get("type")
            ts = iso_to_ts(rec.get("timestamp"))
            msg = rec.get("message") or {}
            if rtype == "user":
                content = msg.get("content")
                if rec.get("isCompactSummary"):
                    yield Event(ts, SUMMARY, _user_text(rec))
                    continue
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            yield Event(ts, TOOL_RESULT, content_text(b.get("content")),
                                        name=tool_names.get(b.get("tool_use_id", ""), ""),
                                        extra={"error": bool(b.get("is_error"))})
                if not _injected(rec):
                    text = _user_text(rec)
                    if text:
                        yield Event(ts, USER, text)
            elif rtype == "assistant" and msg.get("model") != "<synthetic>":
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    k = b.get("type")
                    if k == "text" and b.get("text", "").strip():
                        yield Event(ts, ASSISTANT, b["text"].strip())
                    elif k == "tool_use":
                        tool_names[b.get("id", "")] = b.get("name", "")
                        inp = b.get("input")
                        yield Event(ts, TOOL_CALL, inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False),
                                    name=b.get("name", ""))
                    elif k == "thinking":
                        text = (b.get("thinking") or "").strip()
                        mid = msg.get("id", "")
                        if text:
                            yield Event(ts, THINKING, text)
                        elif mid not in seen_thinking:
                            seen_thinking.add(mid)
                            yield Event(ts, THINKING, "[thinking redacted: signature only]")
    warn_bad_lines(path, bad)
    if errors is not None and bad:
        errors.append({"source": "claude", "status": "partial",
                       "error": f"skipped {bad} unparseable transcript line(s)"})


def fill_titles(threads) -> None:
    """Read titles/cwd only for the chats that will actually be shown (measured: reading all ~4k heads = 15-20s)."""
    for t in threads:
        if t.source == "claude" and not t.title and t.size_bytes:
            t.title, cwd, entry = _first_prompt(t.path)
            t.cwd = t.cwd or cwd
            t.extra["entrypoint"] = entry
            if t.origin == "human" and entry in ("sdk-cli", "sdk-py", "sdk-ts"):
                t.origin = "sdk"
