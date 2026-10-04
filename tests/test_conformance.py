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
    def test_owner_manifest_matches_producer_schema_boundaries(self):
        path = Path(__file__).parents[1] / "conformance" / "agent-systems-lab.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        trace = make_trace()
        evidence = build_work_evidence(
            trace, evidence_id="fixture:owner-manifest", subject="Synthetic", summary="Producer boundary",
            created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab",
        )
        self.assertEqual(manifest["owner"], "chatlens")
        self.assertCountEqual(manifest["native_schemas"], [trace["header"]["schema"], evidence["schema"]])
        for test_path in manifest["tests"]:
            self.assertTrue((Path(__file__).parents[1] / test_path).is_file())
        self.assertTrue((Path(__file__).parents[1] / manifest["consumer_manifest"]).is_file())

    @staticmethod
    def manifest():
        path = Path(__file__).parent / "fixtures" / "agent-systems-lab" / "conformance.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_corpus_manifest_pins_the_forgeyard_owner(self):
        manifest = self.manifest()
        owner = manifest["owner_corpus"]
        self.assertEqual(manifest["schema"], "agent-systems-lab-consumer-conformance/v2")
        self.assertEqual(manifest["contract"], "ai-work-evidence/v1")
        self.assertEqual(owner["commit"], "d6feb5b0ec0f7ccb4fb7f56939e8513972b0855d")
        self.assertEqual(
            owner["manifest_url"],
            "https://github.com/jonah-ux/forgeyard/blob/d6feb5b0ec0f7ccb4fb7f56939e8513972b0855d/conformance/manifest.json",
        )
        self.assertEqual(
            owner["manifest_sha256"],
            "6fe5fc6c5993f161111110971927b07e7db4b7d9f01eac352afd173ce31e7924",
        )

    def test_manifest_carries_all_owner_cases(self):
        manifest = self.manifest()
        self.assertEqual(
            manifest["cases"],
            [
                {"name": "valid-observed", "valid": True, "status": "observed", "fixture": "complete-trace"},
                {"name": "status-unknown", "valid": True, "status": "unknown", "fixture": "partial-trace"},
                {"name": "status-failed", "valid": True, "status": "failed", "fixture": "validator-only"},
                {"name": "unknown-version", "valid": False, "mutation": "schema"},
                {"name": "unsafe-artifact", "valid": False, "mutation": "artifact-name"},
                {"name": "bad-hash", "valid": False, "mutation": "artifact-sha256"},
                {"name": "malformed", "valid": False, "mutation": "root"},
            ],
        )

    def test_complete_and_partial_projection_statuses_match_corpus(self):
        cases = {item["name"]: item for item in self.manifest()["cases"]}
        complete = build_work_evidence(make_trace(), evidence_id="fixture:complete", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        partial = build_work_evidence(make_trace(partial=True), evidence_id="fixture:partial", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        self.assertEqual(complete["status"], cases["valid-observed"]["status"])
        self.assertEqual(partial["status"], cases["status-unknown"]["status"])
        self.assertEqual(validate_work_evidence(complete), complete)
        self.assertEqual(validate_work_evidence(partial), partial)

    def test_status_failed_is_valid_at_the_shared_validator_boundary(self):
        document = build_work_evidence(make_trace(), evidence_id="fixture:failed", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        failed_case = next(item for item in self.manifest()["cases"] if item["name"] == "status-failed")
        document["status"] = failed_case["status"]
        self.assertEqual(validate_work_evidence(document), document)

    def test_owner_refusal_mutations_fail_closed(self):
        tampered = make_trace()
        tampered["header"]["envelope_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            build_work_evidence(tampered, evidence_id="fixture:tampered", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        valid = build_work_evidence(make_trace(), evidence_id="fixture:valid", subject="Synthetic", summary="Fixture", created_at="2026-01-01T00:00:00Z", fixture_id="agent-systems-lab")
        mutations = {
            "unknown-version": lambda document: document.update(schema="ai-work-evidence/v2"),
            "unsafe-artifact": lambda document: document["artifacts"][0].update(name="../secret.txt"),
            "bad-hash": lambda document: document["artifacts"][0].update(sha256="not-a-digest"),
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                document = json.loads(json.dumps(valid))
                mutate(document)
                with self.assertRaises(ValueError):
                    validate_work_evidence(document)
        with self.assertRaises(ValueError):
            validate_work_evidence([])
