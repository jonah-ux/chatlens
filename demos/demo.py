#!/usr/bin/env python3
"""Create deterministic synthetic stores and exercise the installed ChatLens CLI."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def create_fixtures(root: Path) -> dict[str, str]:
    codex_home = root / ".codex"
    codex_id = "01demo-codex-1111-7000-8000-000000000001"
    codex_rollout = codex_home / "sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{codex_id}.jsonl"
    write_jsonl(codex_rollout, [
        {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", "payload": {"id": codex_id, "source": "vscode"}},
        {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Find the parser and explain the release workflow."}], "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}}},
        {"timestamp": "2026-01-01T00:00:02Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "terminal", "call_id": "demo-call", "input": "python -m unittest"}},
        {"timestamp": "2026-01-01T00:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "I found the parser. Tests passed in this synthetic demo only."}]}},
    ])
    codex_home.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(codex_home / "state_5.sqlite") as con:
        con.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, title TEXT, created_at REAL, updated_at REAL, cwd TEXT, model TEXT, source TEXT, archived INT)")
        con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", (codex_id, str(codex_rollout), "Synthetic parser demo", 1767225600, 1767225603, str(root), "fixture-model", "vscode", 0))

    claude_root = root / ".claude/projects/demo-project"
    claude_id = "demo-claude-12345678"
    write_jsonl(claude_root / f"{claude_id}.jsonl", [
        {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "Find the parser and explain the release workflow."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "message": {"model": "fixture-model", "content": [{"type": "text", "text": "This synthetic Claude Code session demonstrates the event reader."}]}}
    ])

    hermes_db = root / ".hermes/state.db"
    hermes_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(hermes_db) as con:
        con.execute("CREATE TABLE sessions(id TEXT, source TEXT, title TEXT, model TEXT, cwd TEXT, started_at REAL, last_activity_at REAL, message_count INT)")
        con.execute("CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INT, compacted INT, display_kind TEXT, tool_name TEXT, tool_calls TEXT, reasoning TEXT)")
        con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", ("demo-hermes-1", "cli", "Synthetic Hermes demo", "fixture-model", str(root), 1767225600, 1767225602, 2))
        con.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(1, "demo-hermes-1", "user", "Find the parser and explain the release workflow.", 1767225600, 1, 0, "", "", "", ""), (2, "demo-hermes-1", "assistant", "This synthetic Hermes session demonstrates read-only SQLite access.", 1767225602, 1, 0, "", "", "", "")])
    return {"codex": codex_id, "claude": claude_id, "hermes": "demo-hermes-1"}


def run_cli(env: dict[str, str], *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-m", "chatlens", *args], env=env, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        raise RuntimeError(f"chatlens {' '.join(args)} failed ({proc.returncode}): {proc.stderr}")
    return proc


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="chatlens-demo-") as name:
        root = Path(name)
        ids = create_fixtures(root)
        env = dict(os.environ, HOME=str(root), CODEX_HOME=str(root / ".codex"),
                   CHATLENS_HOME=str(root / "index"), CHATLENS_NODE="synthetic-demo")
        env.pop("PYTHONPATH", None)
        print(run_cli(env, "list", "--json", "--limit", "10").stdout.strip())
        print(run_cli(env, "index", "--json").stdout.strip())
        print(run_cli(env, "search", "release workflow", "--json").stdout.strip())
        print(run_cli(env, "read", f"codex:{ids['codex']}", "--mode", "brief", "--no-ts").stdout.strip())
        print(run_cli(env, "card", f"codex:{ids['codex']}", "--json").stdout.strip())
        print("demo_scope=synthetic_fixture_only; transcript claims are not live verification")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
