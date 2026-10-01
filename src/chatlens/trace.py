"""Bounded, redacted trace envelopes for recovery-tool interoperability.

The native adapters expose a common :class:`~chatlens.model.Event` stream.  This
module turns that stream into a portable JSONL envelope that another local trace
reader can consume without needing ChatLens' private source adapters.  The
envelope contains redacted event text, never the native transcript bytes, and is
integrity-bound by digests over the canonical redacted rows and header.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from pathlib import Path

from .model import clean

SCHEMA = "chatlens-trace-envelope/v1"
IMPORT_SCHEMA = "chatlens-trace-import/v1"
EXPORT_SCHEMA = "chatlens-trace-export/v1"

# These bounds are deliberately smaller than the native reader limits.  A trace
# envelope is a portable handoff artifact, so it must be cheap to inspect and
# safe to attach to an issue without copying an entire transcript.
MAX_TRACE_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 4_000
MAX_EVENT_TEXT = 8_000
MAX_TOTAL_TEXT = 250_000
MAX_NAME_CHARS = 256

SENSITIVE_KEY_PARTS = (
    "token", "secret", "password", "api_key", "apikey", "authorization",
    "cookie", "credential", "private_key",
)

INLINE_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"\b(?:sk|gh[pousr]|xox[baprs])-[_a-z0-9-]{8,}\b", re.I), "[REDACTED]"),
)

# Email addresses and home-directory prefixes are useful in local transcripts
# but are not needed by a portable trace consumer.  Keep the rest of the text
# intact so incident queries remain useful.
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
HOME_PATH_PATTERN = re.compile(
    r"(?:/(?:Users|home)/)[^\s\"'`]+|(?:[A-Za-z]:\\Users\\)[^\\\s\"'`]+",
    re.I,
)
QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:token|key|secret|password|auth|code)=)[^&#\s]+"
)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sensitive_key(key: str) -> bool:
    lowered = key.casefold().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_text(value: object) -> tuple[str, int]:
    """Return bounded text with recognizable credentials and personal locators removed."""

    text = clean(str(value) if value is not None else "")
    count = 0
    for pattern, replacement in INLINE_SECRET_PATTERNS:
        text, replacements = pattern.subn(replacement, text)
        count += replacements
    text, replacements = QUERY_SECRET_PATTERN.subn(r"\1[REDACTED]", text)
    count += replacements
    text, replacements = EMAIL_PATTERN.subn("[EMAIL]", text)
    count += replacements
    text, replacements = HOME_PATH_PATTERN.subn("[HOME]", text)
    count += replacements
    if len(text) > MAX_EVENT_TEXT:
        text = text[:MAX_EVENT_TEXT] + "… [TRUNCATED]"
        count += 1
    return text, count


def redact_value(value: object, *, key: str | None = None) -> tuple[object, int]:
    """Recursively redact values without mutating native event metadata."""

    if key is not None and _sensitive_key(key):
        return "[REDACTED]", 1
    if isinstance(value, dict):
        output: dict[str, object] = {}
        count = 0
        for child_key, child_value in value.items():
            safe_value, child_count = redact_value(child_value, key=str(child_key))
            output[str(child_key)] = safe_value
            count += child_count
        return output, count
    if isinstance(value, (list, tuple)):
        output = []
        count = 0
        for child in value:
            safe_value, child_count = redact_value(child)
            output.append(safe_value)
            count += child_count
        return output, count
    if isinstance(value, str):
        return redact_text(value)
    return value, 0


def _event_rows(events: list) -> tuple[list[dict], int, int]:
    rows: list[dict] = []
    redactions = 0
    text_chars = 0
    for index, event in enumerate(events[:MAX_EVENTS]):
        message, message_redactions = redact_text(event.text or "")
        safe_name, name_redactions = redact_text(event.name or "")
        safe_extra, extra_redactions = redact_value(event.extra or {})
        try:
            extra_bytes = _canonical(safe_extra)
        except (TypeError, ValueError):
            safe_extra = {"status": "unserializable"}
            extra_bytes = _canonical(safe_extra)
            extra_redactions += 1
        if len(extra_bytes) > 16_384:
            safe_extra = {"status": "truncated", "sha256": _sha256(extra_bytes)}
            extra_redactions += 1
        safe_name = safe_name[:MAX_NAME_CHARS]
        rows.append({
            "schema": SCHEMA,
            "kind": "event",
            "seq": index,
            "type": str(event.kind),
            "name": safe_name,
            "timestamp": event.ts if (event.ts is None or (
                isinstance(event.ts, (int, float)) and not isinstance(event.ts, bool)
                and math.isfinite(event.ts))) else None,
            "message": message,
            "extra": safe_extra,
        })
        redactions += message_redactions + name_redactions + extra_redactions
        text_chars += len(message)
    return rows, redactions, text_chars


def _rows_digest(rows: list[dict]) -> str:
    hasher = hashlib.sha256()
    for row in rows:
        hasher.update(_canonical(row))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _envelope_digest(header: dict, rows: list[dict]) -> str:
    unsigned = {key: value for key, value in header.items() if key != "envelope_sha256"}
    hasher = hashlib.sha256()
    hasher.update(_canonical(unsigned))
    hasher.update(b"\n")
    for row in rows:
        hasher.update(_canonical(row))
        hasher.update(b"\n")
    return hasher.hexdigest()


def _safe_error_status(errors: list[dict]) -> dict:
    """Keep source diagnostics useful without exporting paths or native IDs."""

    return {
        "status": "partial" if errors else "complete",
        "error_count": len(errors),
    }


def build_trace(source: str, thread, events: list, errors: list[dict], node: str) -> dict:
    """Build an in-memory trace envelope from one bounded native event stream."""

    from .snapshot import event_identity  # Avoid a module cycle at import time.

    rows, redactions, text_chars = _event_rows(events)
    source_identity = event_identity(events)
    if len(events) > MAX_EVENTS:
        # _read_events normally reports this as a partial error.  Keep the
        # envelope fail-closed if this function is used directly by a caller.
        errors = list(errors) + [{"status": "partial", "error": "trace event limit reached"}]
    if text_chars > MAX_TOTAL_TEXT:
        # Trim from the end while preserving sequence numbers and an explicit
        # partial marker in the header.  Normal CLI reads are already bounded,
        # but this protects direct library callers too.
        kept: list[dict] = []
        used = 0
        for row in rows:
            if used + len(row["message"]) > MAX_TOTAL_TEXT:
                break
            kept.append(row)
            used += len(row["message"])
        rows = kept
        text_chars = used
        errors = list(errors) + [{"status": "partial", "error": "trace text limit reached"}]
    title, title_redactions = redact_text(getattr(thread, "title", ""))
    node_text, node_redactions = redact_text(node)
    origin, origin_redactions = redact_text(getattr(thread, "origin", ""))
    redactions += title_redactions + node_redactions + origin_redactions
    header = {
        "schema": SCHEMA,
        "kind": "header",
        "version": 1,
        "type": "trace_header",
        "name": "chatlens-trace-envelope",
        "message": "",
        "source": source,
        "session_id_sha256": _sha256(str(thread.id).encode("utf-8")),
        "node": node_text,
        "title": title,
        "origin": origin,
        "input": _safe_error_status(errors),
        "limits": {
            "max_events": MAX_EVENTS,
            "max_event_text_chars": MAX_EVENT_TEXT,
            "max_total_text_chars": MAX_TOTAL_TEXT,
        },
        "source_identity": source_identity,
        "event_count": len(rows),
        "text_chars": text_chars,
        "redactions": redactions,
        "events_sha256": _rows_digest(rows),
    }
    header["envelope_sha256"] = _envelope_digest(header, rows)
    return {"header": header, "events": rows}


def _validate_rows(header: dict, rows: list[dict]) -> list[str]:
    errors: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"event {index} is not an object")
            continue
        if row.get("schema") != SCHEMA or row.get("kind") != "event":
            errors.append(f"event {index} has an unsupported schema")
        if row.get("seq") != index:
            errors.append(f"event {index} sequence is not contiguous")
        if not isinstance(row.get("type"), str) or not row.get("type"):
            errors.append(f"event {index} type is missing")
        if not isinstance(row.get("name"), str):
            errors.append(f"event {index} name is invalid")
        if not isinstance(row.get("message"), str):
            errors.append(f"event {index} message is invalid")
        if not isinstance(row.get("extra"), (dict, list)):
            errors.append(f"event {index} extra is invalid")
        timestamp = row.get("timestamp")
        if timestamp is not None and (not isinstance(timestamp, (int, float))
                                      or isinstance(timestamp, bool)
                                      or not math.isfinite(timestamp)):
            errors.append(f"event {index} timestamp is invalid")
    if len(rows) > MAX_EVENTS:
        errors.append("event count exceeds the envelope bound")
    if header.get("event_count") != len(rows):
        errors.append("header event_count does not match event rows")
    if header.get("text_chars") != sum(len(row.get("message", "")) for row in rows if isinstance(row, dict)):
        errors.append("header text_chars does not match event rows")
    return errors


def validate_trace(envelope: object) -> tuple[bool, list[str], dict]:
    """Validate a parsed envelope and return ``(valid, errors, report_data)``."""

    if not isinstance(envelope, dict):
        return False, ["trace envelope must be a JSON object"], {}
    header = envelope.get("header")
    rows = envelope.get("events")
    errors: list[str] = []
    if not isinstance(header, dict):
        errors.append("trace header is missing")
        header = {}
    if not isinstance(rows, list):
        errors.append("trace events are missing")
        rows = []
    if header.get("schema") != SCHEMA or header.get("kind") != "header":
        errors.append("unsupported trace envelope schema")
    if header.get("version") != 1:
        errors.append("unsupported trace envelope version")
    if not isinstance(header.get("source"), str) or not header.get("source"):
        errors.append("trace source is missing")
    if not isinstance(header.get("session_id_sha256"), str):
        errors.append("trace session identity is missing")
    input_state = header.get("input")
    if not isinstance(input_state, dict) or input_state.get("status") not in ("complete", "partial"):
        errors.append("trace input status is invalid")
    errors.extend(_validate_rows(header, rows))
    expected_events = header.get("events_sha256")
    current_events = _rows_digest(rows)
    if not isinstance(expected_events, str) or expected_events != current_events:
        errors.append("events_sha256 does not match event rows")
    expected_envelope = header.get("envelope_sha256")
    current_envelope = _envelope_digest(header, rows)
    if not isinstance(expected_envelope, str) or expected_envelope != current_envelope:
        errors.append("envelope_sha256 does not match the envelope contents")
    report = {
        "event_count": len(rows),
        "events_sha256": current_events,
        "envelope_sha256": current_envelope,
        "source": header.get("source"),
        "session_id_sha256": header.get("session_id_sha256"),
    }
    return not errors, errors, report


def read_trace(path: str | os.PathLike[str]) -> dict:
    """Read a bounded JSONL envelope without following symlink paths."""

    target = Path(path).expanduser()
    if target.is_symlink() or not target.is_file():
        raise OSError("trace path must be a regular file, not a symlink")
    if target.stat().st_size > MAX_TRACE_BYTES:
        raise OSError(f"trace exceeds the {MAX_TRACE_BYTES:,}-byte safety limit")
    header = None
    rows = []
    with target.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                raise ValueError(f"line {line_number}: blank lines are not allowed")
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"line {line_number}: invalid JSON ({exc.msg})") from exc
            if not isinstance(value, dict):
                raise ValueError(f"line {line_number}: envelope row must be an object")
            if line_number == 1:
                header = value
            else:
                rows.append(value)
    return {"header": header, "events": rows}


def write_trace(path: str | os.PathLike[str], envelope: dict) -> None:
    """Atomically write a new owner-only JSONL envelope and never overwrite it."""

    target = Path(path).expanduser()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise OSError("trace output must be a new regular file, not a symlink")
        raise OSError("refusing to overwrite an existing trace; choose a new path")
    if target.parent.is_symlink():
        raise OSError("trace output directory must not be a symlink")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(target.parent.stat().st_mode) & 0o077:
        raise OSError("trace output directory must be private (mode 700)")
    valid, errors, _ = validate_trace(envelope)
    if not valid:
        raise ValueError("cannot write invalid trace envelope: " + "; ".join(errors))
    data = b"".join(_canonical(row) + b"\n" for row in [envelope["header"], *envelope["events"]])
    if len(data) > MAX_TRACE_BYTES:
        raise OSError(f"trace exceeds the {MAX_TRACE_BYTES:,}-byte safety limit")
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent, text=False)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def envelope_lines(envelope: dict) -> list[str]:
    """Return canonical JSONL lines for stdout or an external trace reader."""

    return [json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for row in [envelope["header"], *envelope["events"]]]


def import_report(envelope: object) -> dict:
    valid, errors, data = validate_trace(envelope)
    header = envelope.get("header") if isinstance(envelope, dict) else {}
    input_state = header.get("input") if isinstance(header, dict) else None
    input_status = input_state.get("status") if isinstance(input_state, dict) else None
    ok = valid and input_status == "complete"
    digest_errors = {
        "header event_count does not match event rows",
        "header text_chars does not match event rows",
        "events_sha256 does not match event rows",
        "envelope_sha256 does not match the envelope contents",
    }
    envelope_state = "valid" if valid else (
        "mismatch" if errors and set(errors).issubset(digest_errors) else "invalid")
    return {
        "schema": IMPORT_SCHEMA,
        "ok": ok,
        "envelope_state": envelope_state,
        "trace_state": "matched" if valid else "mismatch",
        "input_status": input_status,
        **data,
        "errors": errors + (["trace was captured from partial input"] if valid and not ok else []),
    }


def export_summary(envelope: dict, path: str | None = None) -> dict:
    header = envelope["header"]
    return {
        "schema": EXPORT_SCHEMA,
        "trace_schema": SCHEMA,
        "path": path,
        "event_count": header["event_count"],
        "redactions": header["redactions"],
        "input_status": header["input"]["status"],
        "envelope_sha256": header["envelope_sha256"],
    }
