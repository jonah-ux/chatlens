#!/usr/bin/env python3
"""Build a synthetic trace and project it into the shared evidence contract."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from chatlens.evidence import build_work_evidence, write_work_evidence
from chatlens.model import ASSISTANT, USER, Event, Thread
from chatlens.trace import build_trace, read_trace, validate_trace, write_trace


def main() -> None:
    thread = Thread(source="codex", id="fixture-session-001", node="fixture", path="[HOME]/fixture.jsonl", title="Synthetic review", origin="fixture")
    envelope = build_trace("codex", thread, [Event(1, USER, "Review the synthetic release."), Event(2, ASSISTANT, "The bounded fixture is ready.")], [], "fixture")
    with tempfile.TemporaryDirectory(prefix="chatlens-work-evidence-") as directory:
        root = Path(directory)
        trace_path = root / "trace.jsonl"
        evidence_path = root / "evidence.json"
        write_trace(trace_path, envelope)
        loaded = read_trace(trace_path)
        valid, errors, _ = validate_trace(loaded)
        document = build_work_evidence(loaded, evidence_id="fixture-session:001", subject="Synthetic review", summary="A bounded local fixture", created_at="2026-01-01T00:00:00Z", fixture_id="portfolio-suite-v2")
        write_work_evidence(evidence_path, document)
        print(json.dumps({"schema": "chatlens/work-evidence-demo/v1", "trace_valid": valid, "errors": errors, "evidence": document, "path": str(evidence_path)}, sort_keys=True))
        if not valid or errors:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
