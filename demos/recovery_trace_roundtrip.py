#!/usr/bin/env python3
"""Portable recovery handoff for a local trace consumer."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from chatlens import import_report, read_trace, validate_trace, write_trace
from chatlens.model import ASSISTANT, TOOL_CALL, USER, Event, Thread
from chatlens.trace import build_trace


def main() -> None:
    thread = Thread(
        source="codex",
        id="session-release-20261001",
        node="mac-studio",
        path="[HOME]/.codex/sessions/session.jsonl",
        title="Recover release verification context",
        origin="human",
    )
    events = [
        Event(1727812800, USER, "Find the release verification result for the parser.", extra={"email": "jonah@example.com"}),
        Event(1727812801, TOOL_CALL, "cat /Users/jonah/project/RELEASE.md?token=secret-value", name="shell"),
        Event(1727812802, ASSISTANT, "The release check completed and the artifact is ready for review."),
    ]
    envelope = build_trace("codex", thread, events, [], "mac-studio")
    with tempfile.TemporaryDirectory(prefix="chatlens-recovery-") as tmp:
        path = Path(tmp) / "release.trace.jsonl"
        write_trace(path, envelope)
        loaded = read_trace(path)
        valid, errors, _ = validate_trace(loaded)
        report = import_report(loaded)
        messages = [row["message"] for row in loaded["events"]]
        redaction_proof = all("secret-value" not in message and "jonah@example.com" not in message for message in messages)
        result = {
            "schema": "chatlens/integration-example/v1",
            "trace_schema": loaded["header"]["schema"],
            "event_count": loaded["header"]["event_count"],
            "valid": valid,
            "validation_errors": errors,
            "import_status": report["envelope_state"],
            "trace_state": report["trace_state"],
            "redaction_proof": redaction_proof,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        if not valid or errors or report["envelope_state"] != "valid" or report["trace_state"] != "matched" or not redaction_proof:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
