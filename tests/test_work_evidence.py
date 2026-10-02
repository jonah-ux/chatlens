import json
import unittest
from pathlib import Path

from chatlens.evidence import build_work_evidence, validate_work_evidence, write_work_evidence
from chatlens.model import ASSISTANT, USER, Event, Thread
from chatlens.trace import build_trace


def envelope(partial=False):
    thread = Thread(source="codex", id="fixture-session-001", node="fixture", path="[HOME]/fixture.jsonl", title="Synthetic review", origin="fixture")
    events = [
        Event(1727812800, USER, "Find the release result for jonah@example.com"),
        Event(1727812801, ASSISTANT, "The local fixture is ready."),
    ]
    errors = [{"status": "partial", "error": "fixture truncated"}] if partial else []
    return build_trace("codex", thread, events, errors, "fixture")


class WorkEvidenceTest(unittest.TestCase):
    def test_complete_trace_projects_to_shared_contract_without_text(self):
        document = build_work_evidence(envelope(), evidence_id="fixture-session:001", subject="Synthetic review", summary="A bounded local fixture", created_at="2026-01-01T00:00:00Z", fixture_id="portfolio-suite-v2")
        self.assertEqual(document["schema"], "ai-work-evidence/v1")
        self.assertEqual(document["source"], "chatlens")
        self.assertEqual(document["status"], "observed")
        self.assertEqual(document["artifacts"][0]["name"], "chatlens-trace-envelope.jsonl")
        self.assertNotIn("jonah@example.com", json.dumps(document))

    def test_partial_trace_is_unknown(self):
        document = build_work_evidence(envelope(partial=True), evidence_id="fixture-session:002", subject="Synthetic review", summary="A partial local fixture", created_at="2026-01-01T00:00:00Z", fixture_id="portfolio-suite-v2")
        self.assertEqual(document["status"], "unknown")

    def test_invalid_trace_and_unsafe_metadata_fail_closed(self):
        broken = envelope()
        broken["header"]["envelope_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            build_work_evidence(broken, evidence_id="fixture-session:003", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="portfolio-suite-v2")
        with self.assertRaises(ValueError):
            validate_work_evidence({"schema": "ai-work-evidence/v1", "source": "chatlens", "evidence_id": "../private"})
        with self.assertRaises(ValueError):
            validate_work_evidence({"schema": "ai-work-evidence/v1", "source": "chatlens", "evidence_id": "fixture:001", "source_version": "0.3.0", "created_at": "2026-01-01T00:00:00Z", "subject": "Synthetic", "summary": "Fixture", "artifacts": [], "provenance": {"fixture": "../private"}, "status": "unknown"})
        with self.assertRaises(ValueError):
            validate_work_evidence({"schema": "ai-work-evidence/v1", "source": "chatlens", "evidence_id": "fixture:001", "source_version": "0.3.0", "created_at": "2026-01-01T00:00:00Z", "subject": "Synthetic", "summary": "Fixture", "artifacts": [], "provenance": {"fixture": "portfolio-suite-v2", "trace_events_sha256": "bad", "trace_envelope_sha256": "0" * 64}, "status": "unknown"})

    def test_write_is_canonical_and_does_not_overwrite(self):
        document = build_work_evidence(envelope(), evidence_id="fixture-session:004", subject="Synthetic review", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="portfolio-suite-v2")
        path = Path(self._testMethodName + ".json")
        try:
            digest = write_work_evidence(path, document)
            self.assertEqual(json.loads(path.read_text()), document)
            self.assertEqual(len(digest), 64)
            with self.assertRaises(OSError):
                write_work_evidence(path, document)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
