"""Public, offline-only ChatLens command line interface.

The CLI intentionally has no network, SSH, credential, or model paths. It reads native
transcript stores and keeps its SQLite index under CHATLENS_HOME.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sqlite3
import stat
import sys
import time
from dataclasses import replace
from pathlib import Path

from . import __version__
from . import claude, codex, hermes
from .card import build as build_card, render_markdown
from .model import ASSISTANT, TOOL_RESULT, USER, Thread, fmt_ts
from .render import render
from .snapshot import build_snapshot, read_snapshot, validate_snapshot, verify_snapshot, write_snapshot

ADAPTERS = {"codex": codex, "claude": claude, "hermes": hermes}
MAX_EVENTS = 200_000
MAX_TEXT_CHARS = 1_000_000
MAX_TRANSCRIPT_BYTES = 67_108_864
INDEX_SCHEMA = "chatlens-index/v1"


class CLIError(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def _home() -> Path:
    return Path(os.environ.get("CHATLENS_HOME", "~/.local/share/chatlens")).expanduser()


def _node() -> str:
    return os.environ.get("CHATLENS_NODE", "local")


def _json(value) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def _error(message: str, code: int = 2):
    print(f"chatlens: {message}", file=sys.stderr)
    raise CLIError(message, code)


def _threads(source: str | None = None) -> tuple[list[Thread], list[dict]]:
    names = [source] if source else list(ADAPTERS)
    rows: list[Thread] = []
    errors = []
    for name in names:
        try:
            adapter = ADAPTERS[name]
            if name == "codex":
                rows.extend(adapter.list_threads(_node(), errors=errors))
            elif name == "claude":
                rows.extend(adapter.list_threads(_node(), with_titles=True, errors=errors))
            else:
                for row in adapter.list_threads(_node()):
                    if row.origin == "error":
                        errors.append({"source": name, "status": "unreadable", "error": row.title})
                    else:
                        rows.append(row)
        except Exception as exc:  # one damaged source must not hide other local sources
            errors.append({"source": name, "status": "unreadable", "error": f"{type(exc).__name__}: {exc}"})
    for error in errors:
        print(f"chatlens: {error['source']} inventory {error['status']}: {error['error']}", file=sys.stderr)
    return rows, errors


def _store_presence(source: str) -> bool | None:
    try:
        if source == "codex":
            home = Path(codex.home())
            return (home / "state_5.sqlite").is_file() or (home / "sessions").is_dir()
        if source == "claude":
            return bool(claude.roots())
        if source == "hermes":
            return bool(hermes.dbs())
    except OSError:
        return None
    return None


def _visible_threads(source: str | None = None) -> tuple[list[Thread], list[dict]]:
    rows, errors = _threads(source)
    excludes = _excluded()
    return [row for row in rows if not any(row.id.startswith(prefix) for prefix in excludes)], errors


def _excluded() -> tuple[str, ...]:
    path = _home() / "exclude.txt"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ()
    return tuple(value for value in (line.split("#", 1)[0].strip() for line in lines)
                 if value and (value.endswith(":") or len(value) >= 8))


def _resolve(ref: str) -> tuple[str, str, Thread]:
    rows, errors = _visible_threads()
    direct = [row for row in rows if ref == row.id or ref == f"{row.source}:{row.id}" or row.path == ref]
    if not direct:
        direct = [row for row in rows if row.id.startswith(ref)]
    if len(direct) != 1:
        if not direct:
            if errors:
                _error(f"cannot establish whether {ref!r} exists: inventory is incomplete", 3)
            _error(f"no chat found for {ref!r}", 1)
        choices = [f"{row.source}:{row.id}" for row in direct[:20]]
        _error(f"{ref!r} matches several chats; use a longer id:\n  " + "\n  ".join(choices), 2)
    row = direct[0]
    return row.source, row.path, row


def _read_events(thread: Thread, include_results: bool = True) -> tuple[list, list[dict]]:
    source, path = thread.source, thread.path
    errors = []
    if source != "hermes" and path:
        try:
            if os.path.getsize(path) > MAX_TRANSCRIPT_BYTES:
                raise OSError(f"transcript exceeds the {MAX_TRANSCRIPT_BYTES:,}-byte safety limit")
        except OSError:
            raise
    stream = (ADAPTERS[source].events(path, errors=errors) if source != "hermes"
              else ADAPTERS[source].events(path))
    events = []
    used = 0
    try:
        for number, event in enumerate(itertools.islice(stream, MAX_EVENTS + 1)):
            remaining = MAX_TEXT_CHARS - used
            if number == MAX_EVENTS or remaining <= 0:
                errors.append({"source": source, "status": "partial", "error": "transcript limit reached"})
                break
            if not include_results and event.kind == TOOL_RESULT:
                continue
            if len(event.text) > remaining:
                events.append(replace(event, text=event.text[:remaining]))
                errors.append({"source": source, "status": "partial", "error": "transcript text truncated"})
                break
            used += len(event.text)
            events.append(event)
    finally:
        # Close SQLite connections / files even when a bounded read ends early.
        if hasattr(stream, "close"):
            stream.close()
    return events, errors


def _load_events(ref: str, include_results: bool = True) -> tuple[str, Thread, list, list[dict]]:
    source, _, thread = _resolve(ref)
    events, errors = _read_events(thread, include_results)
    for error in errors:
        print(f"chatlens: {error['error']}", file=sys.stderr)
    return source, thread, events, errors


def _coverage(rows: list[Thread], errors: list[dict], names: list[str]) -> dict:
    sources = {}
    for name in names:
        present = _store_presence(name)
        count = sum(row.source == name for row in rows)
        failed = sum(error["source"] == name for error in errors)
        status = ("partial" if count else "unreadable") if failed else (
            "readable" if count else "empty" if present else "absent" if present is False else "unknown")
        sources[name] = {"status": status, "store_present": present,
                         "inventory_count": count, "failed_count": failed}
    partial = any(value["status"] in ("partial", "unreadable", "unknown") for value in sources.values())
    return {"status": "partial" if partial else "complete", "sources": sources}


def cmd_list(args) -> int:
    rows, errors = _visible_threads(args.source)
    coverage = _coverage(rows, errors, [args.source] if args.source else list(ADAPTERS))
    if args.origin:
        rows = [row for row in rows if row.origin == args.origin]
    if args.since:
        cutoff = time.time() - args.since
        rows = [row for row in rows if (row.updated or 0) >= cutoff]
    rows.sort(key=lambda row: row.updated or 0, reverse=True)
    rows = rows[:args.limit] if args.limit else rows
    payload = [row.as_row() for row in rows]
    if args.json:
        _json({"schema": "chatlens-list/v1", "threads": payload, "coverage": coverage, "errors": errors})
    else:
        for row in rows:
            print(f"{row.source:7} {row.id:40} {fmt_ts(row.updated)} {row.origin:9} {row.title[:80]}")
    return 3 if coverage["status"] == "partial" else 0


def cmd_read(args) -> int:
    _, _, events, errors = _load_events(args.ref, include_results=args.mode == "full")
    text = render(events, mode=args.mode, budget=args.budget, show_ts=not args.no_ts)
    print(text)
    return 3 if errors else 0


def _db() -> sqlite3.Connection:
    path = _home().resolve() / "index.db"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
        raise OSError("CHATLENS_HOME must be private (mode 700); choose a private directory or fix its permissions")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            raise OSError("index.db must be private (mode 600); fix its permissions before indexing")
    finally:
        os.close(fd)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS threads (key TEXT PRIMARY KEY, source TEXT, id TEXT, node TEXT, path TEXT, title TEXT, origin TEXT, updated REAL, body TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(key UNINDEXED, title, body)")
    con.execute("CREATE TABLE IF NOT EXISTS metadata (source TEXT PRIMARY KEY, report TEXT)")
    return con


def _digest(events: list) -> str:
    parts = [event.text[:5000] for event in events if event.kind in (USER, ASSISTANT)]
    return "\n".join(parts)[-MAX_TEXT_CHARS:]


def cmd_index(args) -> int:
    started = time.time()
    con = _db()
    rows, listing_errors = _threads(args.source)
    excludes = _excluded()
    rows = [row for row in rows if not any(row.id.startswith(prefix) for prefix in excludes)]
    indexed = 0
    failures = list(listing_errors)
    names = [args.source] if args.source else list(ADAPTERS)
    # Replace the selected inventories atomically; deleted/excluded sessions must disappear.
    for name in names:
        con.execute("DELETE FROM search WHERE key IN (SELECT key FROM threads WHERE source=?)", (name,))
        con.execute("DELETE FROM threads WHERE source=?", (name,))
    for thread in rows:
        try:
            events, errors = _read_events(thread, include_results=False)
            failures.extend(dict(error, id=thread.id) for error in errors)
            key = f"{thread.source}:{thread.id}"
            body = _digest(events)
            con.execute("DELETE FROM search WHERE key=?", (key,))
            con.execute("INSERT INTO search(key,title,body) VALUES (?,?,?)", (key, thread.title, body))
            con.execute("INSERT OR REPLACE INTO threads VALUES (?,?,?,?,?,?,?,?,?)",
                        (key, thread.source, thread.id, thread.node, thread.path, thread.title,
                         thread.origin, thread.updated, body))
            indexed += 1
        except Exception as exc:
            failure = {"id": thread.id, "source": thread.source,
                       "error": f"{type(exc).__name__}: {exc}"[:300]}
            failures.append(failure)
    coverage = _coverage(rows, failures, names)
    partial = coverage["status"] == "partial"
    finished = time.time()
    result = {"schema": INDEX_SCHEMA, "node": _node(), "indexed": indexed,
              "failed": len(failures), "failures": failures,
              "started_at": started, "finished_at": finished,
              "status": "partial" if partial else "complete", "partial": partial,
              "coverage": coverage}
    for name in names:
        con.execute("INSERT OR REPLACE INTO metadata VALUES (?,?)", (name, json.dumps({
            "finished_at": finished, "coverage": coverage["sources"][name]})))
    con.commit()
    con.close()
    if args.json:
        _json(result)
    else:
        print(f"indexed {indexed} chat(s); failed {len(failures)}")
    return 3 if partial and args.fail_on_error else 0


def cmd_search(args) -> int:
    path = (_home() / "index.db").resolve()
    if not path.is_file():
        _error("no index exists; run chatlens index first", 3)
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    metadata = {source: json.loads(report) for source, report in con.execute("SELECT source,report FROM metadata")}
    if not metadata:
        con.close()
        _error("index has no completed refresh; run chatlens index first", 3)
    terms = [term.replace('"', '""') for term in args.query.split()]
    if not terms:
        _error("search query is empty", 2)
    query = " AND ".join(f'"{term}"' for term in terms)
    rows = con.execute(
        "SELECT t.source,t.id,t.node,t.updated,t.origin,t.title,snippet(search,2,'[',']','…',16) "
        "FROM search JOIN threads t ON t.key=search.key WHERE search MATCH ? "
        "ORDER BY bm25(search) LIMIT ?", (query, args.limit)).fetchall()
    output = [dict(zip(("source", "id", "node", "updated", "origin", "title", "snippet"), row)) for row in rows]
    if args.json:
        _json({"schema": "chatlens-search/v1", "matches": output, "index": {
            "sources": metadata, "queried_at": time.time(), "freshness": "cached; refresh with chatlens index"}})
    else:
        for row in output:
            print(f"{row['source']:7} {row['id']} {fmt_ts(row['updated'])} {row['title']}")
            print(f"  {row['snippet']}")
    con.close()
    return 3 if any(item["coverage"]["status"] in ("partial", "unreadable", "unknown")
                    for item in metadata.values()) else 0


def cmd_card(args) -> int:
    source, thread, events, errors = _load_events(args.ref)
    card = build_card(source, thread.id, thread.title, events, _node())
    card["input"] = {"status": "partial" if errors else "complete", "errors": errors}
    print(json.dumps(card, ensure_ascii=False, sort_keys=True) if args.json else render_markdown(card))
    return 3 if errors else 0


def cmd_bundle(args) -> int:
    """Capture a compact, content-addressed recovery snapshot."""
    source, thread, events, errors = _load_events(args.ref)
    snapshot = build_snapshot(source, thread, events, errors, _node())
    if args.out:
        write_snapshot(args.out, snapshot)
    if args.json:
        _json(snapshot)
    elif args.out:
        print(f"wrote {args.out} ({snapshot['snapshot_sha256']})")
    else:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    return 3 if errors else 0


def cmd_verify_bundle(args) -> int:
    """Compare a snapshot with the currently readable native transcript."""
    try:
        snapshot = read_snapshot(args.path)
    except (OSError, ValueError, TypeError) as exc:
        report = {"schema": "chatlens-snapshot-verify/v1", "ok": False,
                  "snapshot_state": "invalid", "source_state": "unknown",
                  "source": None, "id": None, "snapshot_sha256": None,
                  "expected_events_sha256": None, "current_events_sha256": None,
                  "errors": [str(exc)]}
        if args.json:
            _json(report)
        else:
            print("unknown: snapshot could not be read")
            print(f"  {exc}")
        return 3
    if not isinstance(snapshot, dict):
        report = {"schema": "chatlens-snapshot-verify/v1", "ok": False,
                  "snapshot_state": "invalid", "source_state": "unknown",
                  "source": None, "id": None, "snapshot_sha256": None,
                  "expected_events_sha256": None, "current_events_sha256": None,
                  "errors": ["snapshot must be a JSON object"]}
        if args.json:
            _json(report)
        else:
            print("unknown: snapshot is not a JSON object")
        return 3
    source = snapshot.get("source")
    thread_id = snapshot.get("id")
    if not isinstance(source, str) or not isinstance(thread_id, str):
        report = {"schema": "chatlens-snapshot-verify/v1", "ok": False,
                  "snapshot_state": "invalid", "source_state": "unknown",
                  "source": source, "id": thread_id, "snapshot_sha256": snapshot.get("snapshot_sha256"),
                  "expected_events_sha256": None, "current_events_sha256": None,
                  "errors": ["snapshot is missing source or id"]}
        if args.json:
            _json(report)
        else:
            print("unknown: snapshot is missing source or id")
        return 3
    try:
        source_name, thread, events, errors = _load_events(f"{source}:{thread_id}")
    except (CLIError, OSError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError, RuntimeError) as exc:
        snapshot_valid, snapshot_errors = validate_snapshot(snapshot)
        report = {"schema": "chatlens-snapshot-verify/v1", "ok": False,
                  "snapshot_state": "valid" if snapshot_valid else "invalid", "source_state": "unknown",
                  "source": source, "id": thread_id, "snapshot_sha256": snapshot.get("snapshot_sha256"),
                  "expected_events_sha256": (snapshot.get("identity") or {}).get("events_sha256"),
                  "current_events_sha256": None, "errors": snapshot_errors + [str(exc)]}
        if args.json:
            _json(report)
        else:
            print(f"unknown: {source}:{thread_id}")
            print(f"  {exc}")
        return 3
    report = verify_snapshot(snapshot, source_name, thread, events, errors)
    if args.json:
        _json(report)
    else:
        status = "matched" if report["ok"] else report["source_state"]
        print(f"{status}: {source_name}:{thread_id}")
        for error in report["errors"]:
            print(f"  {error}")
    return 0 if report["ok"] else (3 if errors or report["snapshot_state"] == "invalid" else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chatlens", description="Read local Codex, Claude Code, and Hermes chats offline.")
    parser.add_argument("--version", action="version", version=f"chatlens {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="list discovered local chats")
    listing.add_argument("--source", choices=sorted(ADAPTERS))
    listing.add_argument("--origin", help="filter by origin, for example human or subagent")
    listing.add_argument("--since", type=float, metavar="SECONDS", help="only chats updated within this many seconds")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--json", action="store_true", help="emit versioned JSON with source coverage")
    listing.set_defaults(func=cmd_list)

    reading = sub.add_parser("read", help="render one chat by id, unique prefix, or path")
    reading.add_argument("ref")
    reading.add_argument("--mode", choices=("brief", "convo", "full"), default="convo")
    reading.add_argument("--budget", type=int, metavar="TOKENS")
    reading.add_argument("--no-ts", action="store_true")
    reading.set_defaults(func=cmd_read)

    indexing = sub.add_parser("index", help="build the local SQLite FTS5 index")
    indexing.add_argument("--source", choices=sorted(ADAPTERS))
    indexing.add_argument("--json", action="store_true")
    indexing.add_argument("--fail-on-error", action="store_true")
    indexing.set_defaults(func=cmd_index)

    searching = sub.add_parser("search", help="search the local SQLite index")
    searching.add_argument("query")
    searching.add_argument("--limit", type=int, default=20)
    searching.add_argument("--json", action="store_true")
    searching.set_defaults(func=cmd_search)

    cards = sub.add_parser("card", help="emit a deterministic, unverified work card")
    cards.add_argument("ref")
    cards.add_argument("--json", action="store_true")
    cards.set_defaults(func=cmd_card)

    bundles = sub.add_parser("bundle", help="capture a content-addressed recovery snapshot")
    bundles.add_argument("ref")
    bundles.add_argument("--out", metavar="PATH", help="write a new owner-only JSON snapshot")
    bundles.add_argument("--json", action="store_true", help="emit the snapshot JSON")
    bundles.set_defaults(func=cmd_bundle)

    verify = sub.add_parser("verify-bundle", help="verify a recovery snapshot against its source")
    verify.add_argument("path", metavar="SNAPSHOT")
    verify.add_argument("--json", action="store_true", help="emit versioned JSON")
    verify.set_defaults(func=cmd_verify_bundle)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except BrokenPipeError:
        return 0
    except CLIError as exc:
        if getattr(args, "json", False):
            _json({"schema": "chatlens-error/v1", "status": "error", "error": str(exc), "exit_code": exc.code})
        return exc.code
    except (OSError, sqlite3.Error, ValueError, TypeError, AttributeError, KeyError) as exc:
        if getattr(args, "json", False):
            _json({"schema": "chatlens-error/v1", "status": "error", "error": str(exc)})
        print(f"chatlens: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        codex.close_state()


if __name__ == "__main__":
    raise SystemExit(main())
