"""Hermes adapter: <HERMES_HOME>/state.db (sqlite, WAL) for ~/.hermes, ~/.hermes-*, ~/.hermes/profiles/*.

Supported schema family (v30/v31):
- sessions(id, source, title, display_name, model, cwd, started_at, last_activity_at, message_count, ...)
- messages(id, session_id, role user|assistant|tool, content, timestamp, tool_calls JSON (OpenAI shape),
  tool_name, reasoning / reasoning_content (PLAIN TEXT), active, compacted, display_kind)
- timestamps: REAL unix seconds. Order by id (clocks can go backwards). Default visibility active=1.
- Read columns by NAME (v31 reorders columns). Open mode=ro, never immutable=1 (live WAL).
Thread ids are '<home-label>:<session_id>' so profiles never collide.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3

from .codex import Ambiguous
from .model import ASSISTANT, META, SUMMARY, THINKING, TOOL_CALL, TOOL_RESULT, USER, Event, Thread

SKIP_DISPLAY = {"hidden"}
# Harness text Hermes stores in role=user rows.
SYSTEM_DISPLAY = {"async_delegation_complete", "process_complete", "auto_continue", "failed_turn"}
RESTATED = "[STILL IN PROGRESS"
COMPACTION = "[CONTEXT COMPACTION"
OUT_OF_BAND = "[OUT-OF-BAND USER MESSAGE"
SYSTEM_PREFIXES = ("[System:", "[System note:", "[IMPORTANT:", "[Cronjob ")


def dbs() -> list[tuple[str, str]]:
    """[(label, db_path)] for every Hermes home on this machine that has sessions."""
    out = []
    candidates = [os.path.expanduser("~/.hermes/state.db")]
    candidates += sorted(glob.glob(os.path.expanduser("~/.hermes/profiles/*/state.db")))
    candidates += sorted(glob.glob(os.path.expanduser("~/.hermes-*/state.db")))
    for path in candidates:
        if not os.path.isfile(path):
            continue
        parent = os.path.dirname(path)
        if "/profiles/" in path:
            label = "profile-" + os.path.basename(parent)
        else:
            label = os.path.basename(parent).lstrip(".") or "hermes"
        out.append((label, path))
    return out


def _connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _origin(source: str | None) -> str:
    s = (source or "").lower()
    if s in ("desktop", "cli", "telegram", "discord", "slack", "whatsapp", "imessage", "api_server"):
        return "human" if s in ("desktop", "cli") else "channel"
    if s in ("subagent", "tool", "oneshot"):
        return "subagent"
    return s or "unknown"


def list_threads(node: str) -> list[Thread]:
    threads = []
    for label, path in dbs():
        con = None
        try:
            con = _connect(path)
            cols = {r[1] for r in con.execute("PRAGMA table_info(sessions)")}
            title_expr = "COALESCE(title, display_name, '')" if "display_name" in cols else "COALESCE(title,'')"
            last_cols = [name for name in ("last_activity_at", "ended_at", "started_at") if name in cols]
            last = (f"COALESCE({', '.join(last_cols)})" if len(last_cols) > 1 else
                    last_cols[0] if last_cols else "NULL")
            ended = "ended_at" if "ended_at" in cols else "NULL"
            end_reason = "end_reason" if "end_reason" in cols else "NULL"
            archived = "archived" if "archived" in cols else "NULL"
            rows = con.execute(
                f"SELECT id, source, {title_expr} AS title, model, cwd, started_at, {last} AS updated, "
                f"message_count, {ended} AS ended_at, {end_reason} AS end_reason, "
                f"{archived} AS archived FROM sessions").fetchall()
        except sqlite3.Error as exc:
            threads.append(Thread(source="hermes", id=f"{label}:ERROR", node=node, path=path,
                                  title=f"[unreadable: {exc}]", origin="error"))
            continue
        finally:
            if con is not None:
                con.close()
        for r in rows:
            threads.append(Thread(
                source="hermes", id=f"{label}:{r['id']}", node=node, path=f"{path}#{r['id']}",
                title=(r["title"] or "").strip(), started=r["started_at"], updated=r["updated"],
                cwd=r["cwd"] or "", model=r["model"] or "", origin=_origin(r["source"]),
                extra={"hermes_source": r["source"], "messages": r["message_count"], "home": label,
                       "ended_at": r["ended_at"], "end_reason": r["end_reason"],
                       "archive_metadata_supported": "archived" in cols,
                       "archived": None if r["archived"] is None else bool(r["archived"])}))
    return threads


def _first_user(con, sid: str) -> str:
    row = con.execute("SELECT content FROM messages WHERE session_id=? AND role='user' AND active=1 "
                      "ORDER BY id LIMIT 1", (sid,)).fetchone()
    return (row[0] or "")[:160].replace("\n", " ") if row else ""


def resolve(ref: str) -> str | None:
    """Accept '<label>:<sid>', a bare session id or prefix; returns '<db>#<sid>'."""
    if "#" in ref and os.path.exists(ref.split("#", 1)[0]):
        return ref
    label, _, sid = ref.rpartition(":")
    for lab, path in dbs():
        if label and lab != label:
            continue
        con = _connect(path)
        want = sid or ref
        rows = [r[0] for r in con.execute("SELECT id FROM sessions WHERE substr(id, 1, length(?)) = ? "
                                          "ORDER BY started_at DESC LIMIT 200", (want, want))]
        if want in rows or len(rows) == 1:
            return f"{path}#{want if want in rows else rows[0]}"
        if len(rows) > 1:
            raise Ambiguous(ref, [f"hermes:{lab}:{r}" for r in rows])
    return None


def events(ref: str, include_compacted: bool = True):
    path, sid = ref.split("#", 1)
    con = _connect(path)
    try:
        yield from _events(con, sid, include_compacted)
    finally:
        con.close()


def _events(con, sid: str, include_compacted: bool):
    cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
    want = [c for c in ("id", "timestamp", "role", "content", "tool_name", "tool_calls", "reasoning",
                        "reasoning_content", "display_kind", "active", "compacted") if c in cols]
    vis = "(active=1 OR compacted=1)" if include_compacted and "compacted" in cols else "active=1"
    q = f"SELECT {', '.join(want)} FROM messages WHERE session_id=? AND {vis} ORDER BY id"
    compacted_user: set[str] = set()
    for r in con.execute(q, (sid,)):
        r = dict(r)
        ts = r.get("timestamp")
        dk = r.get("display_kind")
        if dk in SKIP_DISPLAY:
            continue
        if dk == "model_switch":
            yield Event(ts, META, (r.get("content") or "model switch").strip())
            continue
        reasoning = r.get("reasoning") or r.get("reasoning_content")
        if reasoning and str(reasoning).strip():
            yield Event(ts, THINKING, str(reasoning).strip())
        role = r.get("role")
        content = (r.get("content") or "").strip()
        if role == "user" and content:
            if content.startswith(RESTATED):
                continue                      # post-compaction restatement
            # A live copy of an already-shown compacted message is a restatement; short replies
            # ("yes", "go") legitimately repeat, so only long texts are de-duplicated.
            if r.get("compacted") != 1 and len(content) >= 40 and content in compacted_user:
                continue
            if content.startswith(COMPACTION):
                yield Event(ts, SUMMARY, content)
                continue
            if dk in SYSTEM_DISPLAY or content.startswith(SYSTEM_PREFIXES):
                yield Event(ts, META, content[:600])
                continue
            if r.get("compacted") == 1:
                compacted_user.add(content)
            if content.startswith(OUT_OF_BAND):
                content = content.split("]", 1)[-1].strip() or content
            yield Event(ts, USER, content, extra={"display_kind": dk})
        elif role == "assistant":
            if content:
                yield Event(ts, ASSISTANT, content)
            if r.get("tool_calls"):
                try:
                    calls = json.loads(r["tool_calls"])
                except ValueError:
                    calls = []
                for tc in calls or []:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    yield Event(ts, TOOL_CALL, str(fn.get("arguments", "")), name=fn.get("name", ""))
        elif role == "tool":
            yield Event(ts, TOOL_RESULT, content, name=r.get("tool_name") or "")
