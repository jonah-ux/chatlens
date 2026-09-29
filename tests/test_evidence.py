"""Regressions for incomplete evidence, cache freshness, and source immutability."""
import hashlib
import json
import sqlite3
import unittest
from contextlib import redirect_stdout, redirect_stderr, closing
from io import StringIO
from unittest import mock

from test_cli import fixture_home
from chatlens import cli


def invoke(*args):
    output, diagnostic = StringIO(), StringIO()
    with redirect_stdout(output), redirect_stderr(diagnostic):
        status = cli.main(list(args))
    return status, json.loads(output.getvalue()), diagnostic.getvalue()


class EvidenceTest(unittest.TestCase):
    def test_corrupt_inventory_is_partial_and_never_a_fake_thread(self):
        with fixture_home() as (root, _, _):
            (root / ".hermes/state.db").write_bytes(b"not a database")
            status, result, _ = invoke("list", "--source", "hermes", "--json")
            self.assertEqual(status, 3)
            self.assertEqual(result["threads"], [])
            self.assertEqual(result["coverage"]["sources"]["hermes"]["status"], "unreadable")

    def test_partial_parse_reaches_cards_index_and_search(self):
        with fixture_home() as (root, tid, _):
            rollout = next((root / ".codex/sessions").rglob("*.jsonl"))
            with rollout.open("a") as out:
                out.write("{broken json\n")
            status, card, _ = invoke("card", "codex:" + tid, "--json")
            self.assertEqual(status, 3)
            self.assertEqual(card["input"]["status"], "partial")
            status, index, _ = invoke("index", "--source", "codex", "--json", "--fail-on-error")
            self.assertEqual(status, 3)
            self.assertTrue(index["partial"])
            self.assertEqual(index["failed"], 1)
            status, search, _ = invoke("search", "release", "--json")
            self.assertEqual(status, 3)
            self.assertTrue(search["matches"])
            self.assertEqual(search["index"]["sources"]["codex"]["coverage"]["status"], "partial")

    def test_refresh_removes_deleted_and_excluded_sessions(self):
        with fixture_home() as (root, tid, _):
            invoke("index", "--json")
            (root / "state/exclude.txt").write_text(tid + "\n")
            invoke("index", "--source", "codex", "--json")
            _, result, _ = invoke("search", "release", "--json")
            self.assertNotIn("codex", [row["source"] for row in result["matches"]])
            with closing(sqlite3.connect(root / ".hermes/state.db")) as con:
                con.execute("DELETE FROM sessions")
                con.commit()
            invoke("index", "--source", "hermes", "--json")
            _, result, _ = invoke("search", "release", "--json")
            self.assertEqual([row["source"] for row in result["matches"]], ["claude"])

    def test_single_source_refresh_has_only_requested_coverage(self):
        with fixture_home():
            status, report, _ = invoke("index", "--source", "codex", "--json", "--fail-on-error")
            self.assertEqual(status, 0)
            self.assertEqual(list(report["coverage"]["sources"]), ["codex"])

    def test_missing_index_is_an_error_without_creating_a_cache(self):
        with fixture_home() as (root, _, _):
            status, result, _ = invoke("search", "release", "--json")
            self.assertEqual(status, 3)
            self.assertEqual(result["schema"], "chatlens-error/v1")
            self.assertFalse((root / "state/index.db").exists())

    def test_unknown_inventory_does_not_claim_not_found(self):
        with fixture_home() as (root, _, _):
            (root / ".hermes/state.db").write_bytes(b"corrupt")
            status, result, _ = invoke("card", "missing-reference", "--json")
            self.assertEqual(status, 3)
            self.assertIn("inventory is incomplete", result["error"])

    def test_bounds_are_structured_and_never_mutate_source(self):
        with fixture_home() as (root, tid, _):
            rollout = next((root / ".codex/sessions").rglob("*.jsonl"))
            before = hashlib.sha256(rollout.read_bytes()).digest()
            with mock.patch.object(cli, "MAX_TEXT_CHARS", 10):
                status, card, _ = invoke("card", "codex:" + tid, "--json")
                self.assertEqual(status, 3)
                self.assertEqual(card["input"]["status"], "partial")
            with mock.patch.object(cli, "MAX_TRANSCRIPT_BYTES", 1):
                status, report, _ = invoke("index", "--source", "codex", "--json", "--fail-on-error")
                self.assertEqual(status, 3)
                self.assertEqual(report["indexed"], 0)
            self.assertEqual(before, hashlib.sha256(rollout.read_bytes()).digest())

    def test_reader_does_not_write_native_stores(self):
        with fixture_home() as (root, tid, _):
            paths = [path for dirname in (".codex", ".claude", ".hermes")
                     for path in (root / dirname).rglob("*") if path.is_file()]
            before = {path: hashlib.sha256(path.read_bytes()).digest() for path in paths}
            invoke("list", "--json")
            invoke("index", "--json")
            invoke("card", "codex:" + tid, "--json")
            self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).digest() for path in paths})
