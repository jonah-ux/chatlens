import json
import os
import sqlite3
import tempfile
import unittest
import sys
from contextlib import contextmanager, closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from chatlens import claude, cli, codex, hermes
from chatlens.model import ASSISTANT, USER


@contextmanager
def fixture_home():
    with tempfile.TemporaryDirectory(prefix="chatlens-fixture-") as tmp:
        root = Path(tmp)
        codex_home = root / ".codex"
        claude_home = root / ".claude" / "projects" / "demo"
        hermes_home = root / ".hermes"
        state_home = root / "state"
        codex_home.joinpath("sessions/2026/01/01").mkdir(parents=True)
        claude_home.mkdir(parents=True)
        hermes_home.mkdir(parents=True)
        state_home.mkdir()
        tid = "01fixture-1111-7000-8000-000000000001"
        rollout = codex_home / "sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{tid}.jsonl"
        rows = [
            {"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta", "payload": {"id": tid, "source": "vscode"}},
            {"timestamp": "2026-01-01T00:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "find the release parser"}], "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["user.text"]}}},
            {"timestamp": "2026-01-01T00:00:02Z", "type": "response_item", "payload": {"type": "custom_tool_call", "name": "terminal", "call_id": "call-1", "input": "python -m unittest"}},
            {"timestamp": "2026-01-01T00:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "The parser is ready. Tests pass."}]}},
        ]
        rollout.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        db = codex_home / "state_5.sqlite"
        with closing(sqlite3.connect(db)) as con:
            con.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, title TEXT, created_at REAL, updated_at REAL, cwd TEXT, model TEXT, source TEXT, archived INT)")
            con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", (tid, str(rollout), "Fixture release", 1767225600, 1767225603, str(root), "fixture", "vscode", 0))
            con.commit()
        claude_id = "claude-fixture-1234"
        (claude_home / f"{claude_id}.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"content": "find the release parser"}}),
            json.dumps({"type": "assistant", "timestamp": "2026-01-01T00:00:01Z", "message": {"model": "fixture", "content": [{"type": "text", "text": "Claude fixture reply."}]}}),
        ]) + "\n")
        hermes_db = hermes_home / "state.db"
        with closing(sqlite3.connect(hermes_db)) as con:
            con.execute("CREATE TABLE sessions(id TEXT, source TEXT, title TEXT, model TEXT, cwd TEXT, started_at REAL, last_activity_at REAL, message_count INT)")
            con.execute("CREATE TABLE messages(id INTEGER, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INT, compacted INT, display_kind TEXT, tool_name TEXT, tool_calls TEXT, reasoning TEXT)")
            con.execute("INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)", ("hermes-fixture", "cli", "Hermes fixture", "fixture", str(root), 1767225600, 1767225603, 2))
            con.executemany("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?)", [(1, "hermes-fixture", "user", "find the release parser", 1767225600, 1, 0, "", "", "", ""), (2, "hermes-fixture", "assistant", "Hermes fixture reply.", 1767225601, 1, 0, "", "", "", "")])
            con.commit()
        env = {"CODEX_HOME": str(codex_home), "HOME": str(root), "CHATLENS_HOME": str(state_home), "CHATLENS_NODE": "fixture"}
        with mock.patch.dict(os.environ, env, clear=False):
            yield root, tid, claude_id


class FixtureTest(unittest.TestCase):
    def test_all_three_adapters_parse_real_fixture_shapes(self):
        with fixture_home() as (root, tid, claude_id):
            self.assertEqual([event.text for event in codex.events(str(root / ".codex/sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{tid}.jsonl")) if event.kind == USER], ["find the release parser"])
            claude_events = list(claude.events(str(root / ".claude/projects/demo" / f"{claude_id}.jsonl")))
            self.assertIn("Claude fixture reply.", [event.text for event in claude_events])
            hermes_path = str(root / ".hermes/state.db") + "#hermes-fixture"
            self.assertIn("Hermes fixture reply.", [event.text for event in hermes.events(hermes_path)])

    def test_cli_index_search_read_card_and_json_stdout(self):
        with fixture_home() as (root, tid, claude_id):
            for command in (["index", "--json"], ["search", "release parser", "--json"],
                            ["list", "--source", "codex", "--json"],
                            ["card", f"codex:{tid}", "--json"]):
                stdout = StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(cli.main(command), 0)
                json.loads(stdout.getvalue())
            stdout = StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(cli.main(["read", f"codex:{tid}", "--mode", "brief", "--budget", "300"]), 0)
            self.assertIn("find the release parser", stdout.getvalue())

    def test_ambiguous_reference_refuses_to_guess(self):
        with fixture_home() as (root, tid, claude_id):
            second_id = "01fixture-2222-7000-8000-000000000002"
            second = root / ".codex/sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{second_id}.jsonl"
            second.write_text((root / ".codex/sessions/2026/01/01" / f"rollout-2026-01-01T00-00-00-{tid}.jsonl").read_text().replace(tid, second_id))
            with closing(sqlite3.connect(root / ".codex/state_5.sqlite")) as con:
                con.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)", (second_id, str(second), "Second fixture", 1767225600, 1767225603, str(root), "fixture", "vscode", 0))
                con.commit()
            self.assertEqual(cli.main(["read", "01fixture", "--mode", "brief"]), 2)


if __name__ == "__main__":
    unittest.main()
