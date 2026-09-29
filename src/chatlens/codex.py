"""Codex adapter: ~/.codex/sessions/**/rollout-*.jsonl (+ state_5.sqlite for titles/origin).

Facts verified on mac-studio 2026-09-24 (codex-cli 0.155.0-alpha.16.3):
- Typed human text = response_item/message role=user whose
  internal_chat_message_metadata_passthrough.content_item_kinds contains 'user.text'.
  Everything else with role user/developer is injected context (AGENTS.md, hooks, skills...).
- Reasoning = response_item/reasoning: 'summary' holds readable headlines; the full
  reasoning is 'encrypted_content' (OpenAI-encrypted, unreadable locally).
- Tool calls: function_call(name, arguments) / custom_tool_call(name, input);
  results: *_output(output).
- Turn boundaries: event_msg task_started / task_complete(last_agent_message, duration_ms).
- compacted records carry a model-written summary in payload.message.
- event_msg/item_completed and token_count duplicate or are bookkeeping -> skipped.
"""
from __future__ import annotations

import glob
import json
import re
import os
import sqlite3
import sys
import tarfile

from .model import (ASSISTANT, META, SUMMARY, THINKING, TOOL_CALL, TOOL_RESULT, TURN, USER,
                    Event, Thread, content_text, iso_to_ts, warn_bad_lines)

class Ambiguous(SystemExit):
    def __init__(self, ref, options):
        self.ref, self.options = ref, list(options)
        super().__init__(f"chatlens: {ref!r} matches several chats - use a longer id:\n  " + "\n  ".join(options))


# Verified in Jonah's rollouts 2026-09-24: the app wraps ambient UI state and automation heartbeats as user.text.
AMBIENT_BLOCK = re.compile(r"<in-app-browser-context\b.*?</in-app-browser-context>\s*", re.S)
AUTOMATION_PREFIXES = ("<heartbeat>",)
INJECTED_PREFIXES = ("<", "# AGENTS.md", "# Files mentioned", "# Files pasted")


def home() -> str:
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def _origin(source: str | None) -> str:
    if not source:
        return "unknown"
    if source == "vscode":
        return "human"            # Codex Desktop / IDE chats Jonah drives
    if source.startswith("{"):
        return "subagent"
    return source                 # exec, cli, ...


def _session_meta(first: bytes, tid: str) -> tuple[str, float]:
    """Classify one rollout from its first line without reading chat content."""
    if len(first) > 65536:
        raise ValueError("oversized session metadata")
    try:
        meta = json.loads(first)
        payload = meta["payload"]
        if meta["type"] != "session_meta" or payload["id"] != tid:
            raise ValueError("session identity mismatch")
        source = payload.get("source")
        if isinstance(source, dict):
            source = json.dumps(source)
        if not isinstance(source, str):
            raise ValueError("unknown origin")
        origin = _origin(source)
        if origin == "unknown":
            raise ValueError("unknown origin")
        started = iso_to_ts(meta.get("timestamp"))
        if started is None:
            raise ValueError("unknown timestamp")
        return origin, started
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(str(exc)) from exc


def list_threads(node: str, include_missing: bool = False, strict: bool = False,
                 errors: list | None = None) -> list[Thread]:
    """Prefer state_5.sqlite (titles, origin); fall back to globbing rollouts."""
    db = os.path.join(home(), "state_5.sqlite")
    threads: list[Thread] = []
    rows = None
    fallback_error = None
    fallback_status = None
    if os.path.exists(db):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            try:
                rows = con.execute(
                    "SELECT id, rollout_path, title, created_at, updated_at, cwd, model, source, archived "
                    "FROM threads").fetchall()
            finally:
                con.close()
        except sqlite3.Error as exc:
            if strict:
                raise RuntimeError(f"Codex state_5.sqlite unreadable: {exc}") from exc
            # A corrupt or locked app database must not hide every rollout on disk: list them from the files.
            fallback_error = f"state_5.sqlite unreadable; fell back to rollout files: {exc}"
            print(f"chatlens: codex state_5.sqlite unreadable ({exc}); listing rollout files instead "
                  "(no titles/origin)", file=sys.stderr)
            rows = None
    if rows is not None:
        for tid, path, title, created, updated, cwd, model, source, archived in rows:
            exists = bool(path) and os.path.exists(path)
            if not exists and not include_missing:
                continue
            origin = _origin(source)
            if strict and exists and origin == "unknown":
                try:
                    if os.stat(path).st_size == 0:
                        print(f"chatlens: ignoring zero-byte indexed live rollout stub {tid}",
                              file=sys.stderr)
                        continue
                    with open(path, "rb") as stream:
                        origin, _ = _session_meta(stream.readline(65537), tid)
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"indexed live rollout {tid} origin unreadable: {exc}") from exc
            threads.append(Thread(
                source="codex", id=tid, node=node, path=path or "", title=(title or "").strip(),
                started=float(created) if created else None, updated=float(updated) if updated else None,
                cwd=cwd or "", model=model or "", origin=origin,
                size_bytes=_size(path) if exists else 0,
                extra={"archived": bool(archived), "on_disk": exists}))
        if strict:
            seen = {thread.id for thread in threads}
            for path in glob.glob(os.path.join(home(), "sessions", "*", "*", "*", "rollout-*.jsonl")):
                tid = _rollout_id(path)
                if tid in seen:
                    continue
                try:
                    st = os.stat(path)
                    if st.st_size == 0:
                        print(f"chatlens: ignoring zero-byte live rollout stub {tid}", file=sys.stderr)
                        continue
                    with open(path, "rb") as stream:
                        origin, started = _session_meta(stream.readline(65537), tid)
                except (OSError, ValueError) as exc:
                    raise RuntimeError(f"unindexed live rollout {tid} unreadable: {exc}") from exc
                threads.append(Thread(source="codex", id=tid, node=node, path=path,
                                      started=started, updated=st.st_mtime, size_bytes=st.st_size,
                                      origin=origin, extra={"archived": False, "on_disk": True}))
                seen.add(tid)
        return threads
    live_paths = glob.glob(os.path.join(home(), "sessions", "*", "*", "*", "rollout-*.jsonl"))
    if not os.path.exists(db):
        if live_paths:
            fallback_error = "state_5.sqlite missing; fell back to rollout files (origin is unclassified)"
            fallback_status = "partial"
        elif os.path.isdir(os.path.join(home(), "sessions")):
            fallback_error = "state_5.sqlite missing and no rollout files were readable"
            fallback_status = "unknown"
    if errors is not None and fallback_error:
        errors.append({"source": "codex", "status": fallback_status or ("partial" if live_paths else "unreadable"),
                       "error": fallback_error[:300]})
    if strict and live_paths:
        raise RuntimeError("Codex state_5.sqlite is missing while live rollouts exist")
    for path in live_paths:
        tid = _rollout_id(path)          # was [-41:-6], which dropped the first id character
        try:
            st = os.stat(path)
        except OSError:
            continue                      # vanished between glob and stat
        threads.append(Thread(source="codex", id=tid, node=node, path=path, started=st.st_ctime,
                              updated=st.st_mtime, size_bytes=st.st_size, origin="unknown"))
    return threads


def archived_threads(node: str, known_ids: set[str], since: float | None = None,
                     force_ids: set[str] | None = None,
                     require_root: bool = False) -> tuple[list[Thread], list[str]]:
    """List archive-only rollouts from their first session_meta line, without reading chat bodies.

    Archives are opt-in because scanning compressed monthly tarballs can take seconds.
    Any unreadable archive or unclassifiable member makes the inventory incomplete.
    """
    root = os.path.expanduser("~/archive/codex-sessions")
    native_root = os.path.join(home(), "archived_sessions")
    found: list[Thread] = []
    issues: list[str] = []
    native_paths: list[str] = []
    try:
        with os.scandir(native_root) as entries:
            native_paths = sorted(entry.path for entry in entries
                                  if entry.name.startswith("rollout-") and entry.name.endswith(".jsonl"))
        native_present = True
    except FileNotFoundError:
        native_present = False
        if os.path.lexists(native_root):
            issues.append("native archive root unavailable")
    except OSError as exc:
        native_present = False
        issues.append(f"native archive root unreadable: {exc.strerror or type(exc).__name__}")
    try:
        with os.scandir(root) as entries:
            archives = sorted(entry.path for entry in entries
                              if entry.name.endswith((".tgz", ".tar.gz")))
    except FileNotFoundError:
        archives = []
        if (require_root and not native_present) or os.path.lexists(root):
            issues.append("archive root unavailable")
    except OSError as exc:
        archives = []
        issues.append(f"archive root unreadable: {exc.strerror or type(exc).__name__}")
    seen = set(known_ids)
    force_ids = force_ids or set()
    for archive_path in archives:
        try:
            with tarfile.open(archive_path, "r|gz") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    basename = os.path.basename(member.name)
                    if (since is not None and member.mtime and member.mtime < since
                            and _rollout_id(basename) not in force_ids):
                        continue
                    if not basename.startswith("rollout-") or not basename.endswith(".jsonl"):
                        continue
                    if not re.fullmatch(
                            r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9a-f-]{36}\.jsonl",
                            basename):
                        issues.append(f"{os.path.basename(archive_path)}:{basename}: invalid rollout name")
                        continue
                    tid = _rollout_id(member.name)
                    if tid in seen:
                        continue
                    if member.size == 0:
                        print(f"chatlens: ignoring zero-byte archived rollout stub {tid}", file=sys.stderr)
                        continue
                    seen.add(tid)
                    stream = archive.extractfile(member)
                    if stream is None:
                        issues.append(f"{os.path.basename(archive_path)}:{tid}: unreadable member")
                        continue
                    first = stream.readline(65537)
                    try:
                        origin, started = _session_meta(first, tid)
                    except ValueError as exc:
                        issues.append(f"{os.path.basename(archive_path)}:{tid}: {exc}")
                        continue
                    updated = float(member.mtime) if member.mtime else started
                    found.append(Thread(source="codex", id=tid, node=node, path="",
                                        started=started, updated=updated, origin=origin,
                                        size_bytes=member.size,
                                        extra={"archived": True, "on_disk": False,
                                               "archive": archive_path, "member": member.name}))
        except (OSError, tarfile.TarError) as exc:
            issues.append(f"{os.path.basename(archive_path)}: {exc}")
    for path in native_paths:
        basename = os.path.basename(path)
        tid = _rollout_id(path)
        try:
            st = os.stat(path)
        except OSError as exc:
            issues.append(f"native:{basename}: {exc}")
            continue
        if since is not None and st.st_mtime < since and tid not in force_ids:
            continue
        if not re.fullmatch(
                r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-[0-9a-f-]{36}\.jsonl",
                basename):
            issues.append(f"native:{basename}: invalid rollout name")
            continue
        if tid in seen:
            continue
        try:
            if st.st_size == 0:
                print(f"chatlens: ignoring zero-byte native archived rollout stub {tid}",
                      file=sys.stderr)
                continue
            with open(path, "rb") as stream:
                origin, started = _session_meta(stream.readline(65537), tid)
        except (OSError, ValueError) as exc:
            issues.append(f"native:{tid}: {exc}")
            continue
        seen.add(tid)
        found.append(Thread(source="codex", id=tid, node=node, path=path,
                            started=started, updated=st.st_mtime, origin=origin,
                            size_bytes=st.st_size,
                            extra={"archived": True, "on_disk": True}))
    return found, issues


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _rollout_id(path: str) -> str:
    """rollout-YYYY-MM-DDTHH-MM-SS-<36-char id>.jsonl -> id"""
    base = os.path.basename(path)
    return base[-42:-6] if base.endswith(".jsonl") else ""


def resolve(ref: str) -> str | None:
    """Accept a rollout path, a full thread id, or an id PREFIX (never a timestamp/substring)."""
    if os.path.exists(ref):
        return ref
    matches: dict[str, tuple[str, str]] = {}           # id -> (path, title)
    db = os.path.join(home(), "state_5.sqlite")
    if os.path.exists(db):
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
            try:
                for tid, path, title in con.execute(
                        "SELECT id, rollout_path, title FROM threads WHERE substr(id, 1, length(?)) = ? "
                        "ORDER BY updated_at DESC LIMIT 200", (ref, ref)):
                    if path and os.path.exists(path):
                        matches[tid] = (path, title or "")
            finally:
                con.close()
        except sqlite3.Error as exc:           # fall through to the rollout files
            print(f"chatlens: codex state_5.sqlite unreadable ({exc}); matching rollout files", file=sys.stderr)
    if not matches:
        for path in glob.glob(os.path.join(home(), "sessions", "*", "*", "*", "rollout-*.jsonl")):
            tid = _rollout_id(path)
            if tid.startswith(ref):
                matches[tid] = (path, "")
    for path in glob.glob(os.path.join(home(), "archived_sessions", "rollout-*.jsonl")):
        tid = _rollout_id(path)
        if tid.startswith(ref) and tid not in matches:
            matches[tid] = (path, "")
    if ref in matches:
        return matches[ref][0]
    if len(matches) == 1:
        return next(iter(matches.values()))[0]
    if len(matches) > 1:
        raise Ambiguous(ref, [f"codex:{t}  {ti[:60]}" for t, (_, ti) in list(matches.items())[:8]])
    return None


def _is_typed(payload: dict, text: str) -> bool:
    meta = payload.get("internal_chat_message_metadata_passthrough") or {}
    kinds = meta.get("content_item_kinds") if isinstance(meta, dict) else None
    if kinds:
        return "user.text" in kinds
    return not text.lstrip().startswith(INJECTED_PREFIXES)   # older rollouts have no metadata


_STATE_CON = {}


def _state_con():
    """One read-only state_5 connection per process (was reopened per chat: 473 opens = 6.7s in an index run)."""
    db = os.path.join(home(), "state_5.sqlite")
    if db not in _STATE_CON:
        _STATE_CON[db] = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10) if os.path.exists(db) else None
    return _STATE_CON[db]


def _stored_first_prompt(path: str) -> str:
    """Agent-launched threads can lack any user.text record; state_5 still keeps the launch prompt."""
    con = _state_con()
    if con is None:
        return ""
    row = con.execute("SELECT first_user_message FROM threads WHERE rollout_path=?", (path,)).fetchone()
    return (row[0] or "").strip() if row else ""


def events(path: str, errors: list | None = None):
    for evt in _events(path, errors=errors):
        yield evt


def _events(path: str, errors: list | None = None):
    calls: dict[str, str] = {}
    have_user = False

    def fallback(ts):
        text = _stored_first_prompt(path)
        if text:
            return Event(ts, USER, text, extra={"from": "state_5.first_user_message"})
        # Verified 2026-09-24: thread_source=agent_created_thread chats can have no stored prompt anywhere.
        return Event(ts, META, "[no human prompt stored: this chat was launched by another agent and Codex "
                               "did not persist the launch instructions]")

    bad = 0
    with open(path, errors="replace") as fh:
        for line in fh:
            head = line[:200]
            # Measured 2026-09-24: these records are ~67% of rollout bytes and never rendered; skipping them
            # before json.loads halves parse time (500MB rollout 1.41s -> 0.66s). The literal type check
            # keeps any record we render (message, reasoning, calls, task_*, compacted, turn_context).
            if ('"type":"event_msg"' in head and ('"type":"item_completed"' in head or '"type":"token_count"' in head)) \
                    or '"type":"token_usage_record"' in head or '"type":"world_state"' in head:
                continue
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
            p = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
            ts = iso_to_ts(rec.get("timestamp"))
            if rtype == "response_item":
                ptype = p.get("type")
                if ptype == "message":
                    text = content_text(p.get("content")).strip()
                    if not text:
                        continue
                    role = p.get("role")
                    if role == "user" and _is_typed(p, text):
                        text = AMBIENT_BLOCK.sub("", text).strip()
                        if not text:
                            continue
                        if text.startswith(AUTOMATION_PREFIXES):   # scheduled heartbeat, not a human
                            yield Event(ts, META, text[:600])
                            continue
                        have_user = True
                        yield Event(ts, USER, text)
                    elif role == "assistant":
                        if not have_user:
                            have_user = True
                            fb = fallback(ts)
                            if fb:
                                yield fb
                        yield Event(ts, ASSISTANT, text, extra={"phase": p.get("phase")})
                elif ptype == "reasoning":
                    heads = [s.get("text", "") for s in (p.get("summary") or []) if isinstance(s, dict)]
                    if heads:
                        yield Event(ts, THINKING, "\n".join(h for h in heads if h))
                elif ptype in ("function_call", "custom_tool_call"):
                    name = p.get("name") or ""
                    calls[p.get("call_id", "")] = name
                    args = p.get("arguments") if ptype == "function_call" else p.get("input")
                    yield Event(ts, TOOL_CALL, content_text(args), name=name)
                elif ptype in ("function_call_output", "custom_tool_call_output"):
                    out = p.get("output")
                    if isinstance(out, dict) and "output" in out:
                        out = out["output"]
                    yield Event(ts, TOOL_RESULT, content_text(out), name=calls.get(p.get("call_id", ""), ""))
            elif rtype == "event_msg":
                ptype = p.get("type")
                if ptype == "task_started":
                    yield Event(ts, TURN, "turn started", extra={"turn_id": p.get("turn_id")})
                elif ptype == "task_complete":
                    dur = p.get("duration_ms")
                    yield Event(ts, TURN, "turn completed", extra={"turn_id": p.get("turn_id"), "duration_ms": dur})
                elif ptype == "turn_aborted":
                    yield Event(ts, TURN, "turn " + str(p.get("reason") or "aborted"), extra={"turn_id": p.get("turn_id")})
            elif rtype == "compacted":
                msg = p.get("message")
                if isinstance(msg, str) and msg.strip():
                    yield Event(ts, SUMMARY, msg.strip())
            elif rtype == "turn_context":
                model = p.get("model")
                if model:
                    yield Event(ts, META, f"model={model} effort={p.get('effort') or p.get('reasoning_effort') or ''}".strip())
    warn_bad_lines(path, bad)
    if errors is not None and bad:
        errors.append({"source": "codex", "status": "partial",
                       "error": f"skipped {bad} unparseable rollout line(s)"})
