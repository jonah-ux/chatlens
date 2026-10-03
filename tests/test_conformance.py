import json
from pathlib import Path
import unittest

from chatlens.evidence import build_work_evidence, validate_work_evidence
from chatlens.model import ASSISTANT, USER, Event, Thread
from chatlens.trace import build_trace


def make_trace(partial=False):
    thread = Thread(source="codex", id="fixture-session", node="fixture", path="[HOME]/fixture.jsonl", title="Synthetic", origin="fixture")
    errors = [{"status": "partial", "error": "fixture truncated"}] if partial else []
    return build_trace("codex", thread, [Event(1, USER, "review"), Event(2, ASSISTANT, "ready")], errors, "fixture")


class ConsumerConformanceTest(unittest.TestCase):
    def test_corpus_manifest_names_the_forgeyard_owner(self):
        path = Path(__file__).parent / "fixtures" / "agent-systems-lab" / "conformance.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["owner_corpus"], "forgeyard/conformance")
        self.assertEqual(manifest["schema"], "agent-systems-lab-consumer-conformance/v1")

    def test_complete_and_partial_projection_statuses_match_corpus(self):
        complete = build_work_evidence(make_trace(), evidence_id="fixture:complete", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        partial = build_work_evidence(make_trace(partial=True), evidence_id="fixture:partial", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        self.assertEqual(complete["status"], "observed")
        self.assertEqual(partial["status"], "unknown")
        self.assertEqual(validate_work_evidence(complete), complete)
        self.assertEqual(validate_work_evidence(partial), partial)

    def test_tampered_trace_and_unknown_schema_are_rejected(self):
        tampered = make_trace()
        tampered["header"]["envelope_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            build_work_evidence(tampered, evidence_id="fixture:tampered", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        unknown = {"schema": "ai-work-evidence/v2", "source": "chatlens", "evidence_id": "fixture:unknown", "source_version": "0.4.0", "created_at": "2026-01-01T00:00:00Z", "subject": "Synthetic", "summary": "Fixture", "artifacts": [], "provenance": {"fixture": "agent-systems-lab", "trace_events_sha256": "0" * 64, "trace_envelope_sha256": "0" * 64}, "status": "observed"}
        with self.assertRaises(ValueError):
            validate_work_evidence(unknown)
