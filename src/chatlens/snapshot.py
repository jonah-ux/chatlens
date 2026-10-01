"""Deterministic, local-only recovery snapshots for one transcript.

Snapshots intentionally keep a compact work card and a content fingerprint instead of
copying the transcript.  The fingerprint lets a later verifier distinguish a matching
source, a changed source, and a source that cannot currently be read.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import time
from pathlib import Path

from .card import build as build_card

SCHEMA = "chatlens-snapshot/v1"
VERIFY_SCHEMA = "chatlens-snapshot-verify/v1"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def event_identity(events: list) -> dict:
    """Return a stable identity for the bounded event stream.

    Text itself is never written to the snapshot.  Kind, timestamp, tool name,
    length, and a digest of every event are enough to prove that a later read
    observed the same bounded stream without making the artifact a transcript copy.
    """
    rows = []
    text_chars = 0
    timestamps = []
    for index, event in enumerate(events):
        text = event.text or ""
        text_bytes = text.encode("utf-8")
        row = {
            "index": index,
            "kind": event.kind,
            "name": event.name or "",
            "ts": event.ts,
            "text_chars": len(text),
            "text_sha256": _sha256(text_bytes),
            "extra_sha256": _sha256(_canonical(event.extra or {})),
        }
        rows.append(row)
        text_chars += len(text)
        if event.ts is not None:
            timestamps.append(event.ts)
    identity = {
        "event_count": len(rows),
        "text_chars": text_chars,
        "first_ts": min(timestamps) if timestamps else None,
        "last_ts": max(timestamps) if timestamps else None,
        "events_sha256": _sha256(_canonical(rows)),
    }
    return identity


def _payload(snapshot: dict) -> dict:
    return {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}


def build_snapshot(source: str, thread, events: list, errors: list[dict], node: str) -> dict:
    card = build_card(source, thread.id, thread.title, events, node)
    card["input"] = {"status": "partial" if errors else "complete", "errors": errors}
    payload = {
        "schema": SCHEMA,
        "captured_at": time.time(),
        "source": source,
        "id": thread.id,
        "node": node,
        "title": thread.title,
        "origin": thread.origin,
        "identity": event_identity(events),
        "input": {"status": "partial" if errors else "complete", "errors": errors},
        "card": card,
    }
    payload["snapshot_sha256"] = _sha256(_canonical(payload))
    return payload


def validate_snapshot(snapshot: object) -> tuple[bool, list[str]]:
    if not isinstance(snapshot, dict):
        return False, ["snapshot must be a JSON object"]
    errors = []
    if snapshot.get("schema") != SCHEMA:
        errors.append(f"unsupported snapshot schema: {snapshot.get('schema')!r}")
    if not isinstance(snapshot.get("source"), str) or not snapshot.get("source"):
        errors.append("snapshot source is missing")
    if not isinstance(snapshot.get("id"), str) or not snapshot.get("id"):
        errors.append("snapshot id is missing")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("events_sha256"), str):
        errors.append("snapshot event identity is missing")
    expected = snapshot.get("snapshot_sha256")
    if not isinstance(expected, str) or expected != _sha256(_canonical(_payload(snapshot))):
        errors.append("snapshot_sha256 does not match the snapshot contents")
    input_state = snapshot.get("input")
    if not isinstance(input_state, dict) or input_state.get("status") not in ("complete", "partial"):
        errors.append("snapshot input status is invalid")
    return not errors, errors


def verify_snapshot(snapshot: dict, source: str, thread, events: list,
                    errors: list[dict]) -> dict:
    valid, validation_errors = validate_snapshot(snapshot)
    expected = snapshot.get("identity") if isinstance(snapshot, dict) else {}
    current = event_identity(events)
    source_errors = [item.get("error", "source read was partial") for item in errors]
    source_state = "unknown"
    if not errors:
        source_state = "matched" if current == expected else "mismatch"
    report_errors = list(validation_errors)
    if errors:
        report_errors.extend(source_errors)
    elif source_state == "mismatch":
        report_errors.append("current transcript identity differs from the snapshot")
    complete = snapshot.get("input", {}).get("status") == "complete" if isinstance(snapshot, dict) else False
    ok = valid and complete and source_state == "matched"
    return {
        "schema": VERIFY_SCHEMA,
        "ok": ok,
        "snapshot_state": "valid" if valid else "invalid",
        "source_state": source_state,
        "source": source,
        "id": snapshot.get("id") if isinstance(snapshot, dict) else None,
        "snapshot_sha256": snapshot.get("snapshot_sha256") if isinstance(snapshot, dict) else None,
        "expected_events_sha256": expected.get("events_sha256") if isinstance(expected, dict) else None,
        "current_events_sha256": current.get("events_sha256"),
        "errors": report_errors,
    }


def read_snapshot(path: str | os.PathLike[str]) -> dict:
    target = Path(path).expanduser()
    if target.is_symlink() or not target.is_file():
        raise OSError("snapshot path must be a regular file, not a symlink")
    if target.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise OSError(f"snapshot exceeds the {MAX_SNAPSHOT_BYTES:,}-byte safety limit")
    with target.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    return value


def write_snapshot(path: str | os.PathLike[str], snapshot: dict) -> None:
    """Write one owner-only snapshot without following an existing symlink."""
    target = Path(path).expanduser()
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not target.is_file():
            raise OSError("snapshot output must be a new regular file, not a symlink")
        raise OSError("refusing to overwrite an existing snapshot; choose a new path")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if stat.S_IMODE(target.parent.stat().st_mode) & 0o077:
        raise OSError("snapshot output directory must be private (mode 700)")
    data = (_canonical(snapshot) + b"\n")
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
