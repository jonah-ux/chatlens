"""Project a redacted ChatLens trace into the portfolio evidence contract."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .trace import envelope_lines, redact_text, validate_trace


SCHEMA = "ai-work-evidence/v1"
_STATUSES = frozenset({"observed", "verified", "failed", "unknown"})
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEYS = frozenset({"schema", "evidence_id", "source", "source_version", "created_at", "subject", "summary", "artifacts", "provenance", "status"})


def _canonical(document: Mapping[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def build_work_evidence(
    envelope: Mapping[str, Any],
    *,
    evidence_id: str,
    subject: str,
    summary: str,
    created_at: str,
    fixture_id: str,
    source_version: str | None = None,
) -> dict[str, Any]:
    """Project a validated redacted trace without copying its event text."""

    valid, errors, report = validate_trace(dict(envelope))
    if not valid:
        raise ValueError("cannot project invalid trace envelope: " + "; ".join(errors))
    header = envelope["header"]
    trace_bytes = b"\n".join(line.encode("utf-8") for line in envelope_lines(dict(envelope))) + b"\n"
    input_status = header["input"]["status"]
    safe_subject, _ = redact_text(subject)
    safe_summary, _ = redact_text(summary)
    document = {
        "schema": SCHEMA,
        "evidence_id": evidence_id,
        "source": "chatlens",
        "source_version": source_version or _package_version(),
        "created_at": created_at,
        "subject": safe_subject,
        "summary": safe_summary,
        "artifacts": [{"name": "chatlens-trace-envelope.jsonl", "size": len(trace_bytes), "sha256": _digest(trace_bytes)}],
        "provenance": {
            "fixture": fixture_id,
            "trace_schema": header["schema"],
            "trace_events_sha256": report["events_sha256"],
            "trace_envelope_sha256": report["envelope_sha256"],
        },
        "status": "observed" if input_status == "complete" else "unknown",
    }
    return validate_work_evidence(document)


def validate_work_evidence(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the Forgeyard-owned shape locally without a package dependency."""

    if not isinstance(document, Mapping):
        raise ValueError("work evidence must be a JSON object")
    unknown = set(document) - _KEYS
    if unknown:
        raise ValueError(f"work evidence contains unknown fields: {sorted(unknown)}")
    missing = sorted(_KEYS - {"artifacts", "provenance"} - set(document))
    if missing:
        raise ValueError("work evidence is missing required fields: " + ", ".join(missing))
    for key, pattern in (("evidence_id", _ID_RE), ("source_version", re.compile(r"^\S{1,64}$"))):
        value = document[key]
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f"work evidence {key} has an invalid format")
    if document["schema"] != SCHEMA or document["source"] != "chatlens":
        raise ValueError("work evidence schema or source is invalid")
    for key in ("subject", "summary"):
        value = document[key]
        if not isinstance(value, str) or not value.strip() or len(value) > 2048 or any(ord(char) < 32 for char in value):
            raise ValueError(f"work evidence {key} must be bounded and printable")
    created_at = document["created_at"]
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise ValueError("work evidence created_at must be an RFC 3339 UTC timestamp")
    try:
        datetime.fromisoformat(created_at[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("work evidence created_at must be an RFC 3339 UTC timestamp") from exc
    if document["status"] not in _STATUSES:
        raise ValueError("work evidence status is invalid")
    artifacts = document.get("artifacts", [])
    if not isinstance(artifacts, list):
        raise ValueError("work evidence artifacts must be a list")
    names: set[str] = set()
    normalized_artifacts = []
    for artifact in artifacts:
        if not isinstance(artifact, Mapping) or set(artifact) != {"name", "size", "sha256"}:
            raise ValueError("work evidence artifacts must contain name, size, and sha256")
        name = artifact["name"]
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name) or ".." in name or "\\" in name or name.startswith("/"):
            raise ValueError("work evidence artifact name is unsafe")
        if name in names:
            raise ValueError("work evidence artifact names must be unique")
        names.add(name)
        size = artifact["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("work evidence artifact size is invalid")
        digest = artifact["sha256"]
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError("work evidence artifact hash is invalid")
        normalized_artifacts.append({"name": name, "size": size, "sha256": digest})
    provenance = document.get("provenance", {})
    if not isinstance(provenance, Mapping) or any(not isinstance(key, str) or not isinstance(value, (str, int, bool, type(None))) for key, value in provenance.items()):
        raise ValueError("work evidence provenance must contain scalar values")
    fixture = provenance.get("fixture")
    if not isinstance(fixture, str) or not _ID_RE.fullmatch(fixture):
        raise ValueError("work evidence fixture provenance is invalid")
    for key in ("trace_events_sha256", "trace_envelope_sha256"):
        value = provenance.get(key)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError(f"work evidence provenance hash is invalid: {key}")
    normalized = dict(document)
    normalized["artifacts"] = normalized_artifacts
    normalized["provenance"] = dict(provenance)
    return normalized


def write_work_evidence(path: str | Path, document: Mapping[str, Any]) -> str:
    target = Path(path).expanduser()
    if target.exists() or target.is_symlink():
        raise OSError("work evidence output already exists")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.is_symlink():
        raise OSError("work evidence output directory must not be a symlink")
    normalized = validate_work_evidence(document)
    target.write_bytes(_canonical(normalized))
    return _digest(_canonical(normalized))


def _package_version() -> str:
    from . import __version__

    return __version__
