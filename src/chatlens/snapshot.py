"""Deterministic, local-only recovery snapshots for one transcript.

Snapshots intentionally keep a compact work card and a content fingerprint instead of
copying the transcript.  The fingerprint lets a later verifier distinguish a matching
source, a changed source, and a source that cannot currently be read.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path

from .card import build as build_card

SCHEMA = "chatlens-snapshot/v1"
VERIFY_SCHEMA = "chatlens-snapshot-verify/v1"
MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024
_SNAPSHOT_FIELDS = frozenset({
    "schema", "captured_at", "source", "id", "node", "title", "origin",
    "identity", "input", "card", "snapshot_sha256",
})
_IDENTITY_FIELDS = frozenset({
    "event_count", "text_chars", "first_ts", "last_ts", "events_sha256",
})
_CARD_FIELDS = frozenset({
    "schema", "source", "id", "node", "title", "goal", "goal_status",
    "activity", "work", "claims", "continue", "input",
})
_ERROR_FIELDS = frozenset({"source", "status", "error"})
_HEX64 = set("0123456789abcdef")


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


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX64


def _valid_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate_error_list(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != _ERROR_FIELDS:
            errors.append(f"{label}[{index}] must contain source, status, and error")
            continue
        if any(not isinstance(item[field], str) or not item[field] for field in _ERROR_FIELDS):
            errors.append(f"{label}[{index}] fields must be non-empty strings")


def _validate_input(value: object, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return
    unknown = sorted(set(value) - {"status", "errors"})
    if unknown:
        errors.append(f"{label} has unsupported fields: {', '.join(unknown)}")
    if value.get("status") not in ("complete", "partial"):
        errors.append(f"{label} status is invalid")
    _validate_error_list(value.get("errors"), f"{label}.errors", errors)


def _validate_card(card: object, errors: list[str]) -> None:
    if not isinstance(card, dict):
        errors.append("snapshot card must be an object")
        return
    unknown = sorted(set(card) - _CARD_FIELDS)
    if unknown:
        errors.append(f"snapshot card has unsupported fields: {', '.join(unknown)}")
    for field in ("schema", "source", "id", "node", "title", "goal", "goal_status", "continue"):
        if not isinstance(card.get(field), (str, type(None))):
            errors.append(f"snapshot card {field} is invalid")
    if card.get("schema") != "chatlens-card/v1":
        errors.append("snapshot card schema is invalid")
    activity = card.get("activity")
    if not isinstance(activity, dict) or set(activity) != {"first", "last", "active_hours", "counts"}:
        errors.append("snapshot card activity is invalid")
    elif (
        (activity["first"] is not None and not _valid_number(activity["first"]))
        or (activity["last"] is not None and not _valid_number(activity["last"]))
        or not _valid_number(activity["active_hours"])
        or not isinstance(activity["counts"], dict)
        or any(not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool) or value < 0
               for key, value in activity["counts"].items())
    ):
        errors.append("snapshot card activity values are invalid")
    work = card.get("work")
    if not isinstance(work, dict) or set(work) != {"tools", "files"}:
        errors.append("snapshot card work is invalid")
    else:
        for field in ("tools", "files"):
            values = work[field]
            if not isinstance(values, list):
                errors.append(f"snapshot card work.{field} must be a list")
                continue
            for index, item in enumerate(values):
                if not isinstance(item, list) or len(item) != 2 or not isinstance(item[0], str) or not isinstance(item[1], int) or isinstance(item[1], bool) or item[1] < 0:
                    errors.append(f"snapshot card work.{field}[{index}] is malformed")
    claims = card.get("claims")
    if not isinstance(claims, dict) or set(claims) != {"pull_requests_mentioned", "kanban_cards_mentioned", "commits_mentioned", "last_reported_outcome", "verification"}:
        errors.append("snapshot card claims are invalid")
    else:
        for field in ("pull_requests_mentioned", "kanban_cards_mentioned", "commits_mentioned"):
            values = claims[field]
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                errors.append(f"snapshot card claims.{field} is malformed")
        if not isinstance(claims["last_reported_outcome"], (str, type(None))) or not isinstance(claims["verification"], str):
            errors.append("snapshot card claims text is invalid")
    _validate_input(card.get("input"), "snapshot card input", errors)


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
    unknown = sorted(set(snapshot) - _SNAPSHOT_FIELDS)
    if unknown:
        errors.append(f"snapshot has unsupported fields: {', '.join(unknown)}")
    if snapshot.get("schema") != SCHEMA:
        errors.append(f"unsupported snapshot schema: {snapshot.get('schema')!r}")
    if not isinstance(snapshot.get("source"), str) or not snapshot.get("source"):
        errors.append("snapshot source is missing")
    if not isinstance(snapshot.get("id"), str) or not snapshot.get("id"):
        errors.append("snapshot id is missing")
    identity = snapshot.get("identity")
    if not isinstance(identity, dict):
        errors.append("snapshot event identity is missing")
    else:
        identity_unknown = sorted(set(identity) - _IDENTITY_FIELDS)
        if identity_unknown:
            errors.append(f"snapshot identity has unsupported fields: {', '.join(identity_unknown)}")
        for field in ("event_count", "text_chars"):
            if not isinstance(identity.get(field), int) or isinstance(identity.get(field), bool) or identity[field] < 0:
                errors.append(f"snapshot identity {field} is invalid")
        for field in ("first_ts", "last_ts"):
            if identity.get(field) is not None and not _valid_number(identity[field]):
                errors.append(f"snapshot identity {field} is invalid")
        if not _valid_digest(identity.get("events_sha256")):
            errors.append("snapshot event identity is missing")
    expected = snapshot.get("snapshot_sha256")
    if not _valid_digest(expected) or expected != _sha256(_canonical(_payload(snapshot))):
        errors.append("snapshot_sha256 does not match the snapshot contents")
    _validate_input(snapshot.get("input"), "snapshot input", errors)
    _validate_card(snapshot.get("card"), errors)
    return not errors, errors


def verify_snapshot(snapshot: dict, source: str, thread, events: list,
                    errors: list[dict]) -> dict:
    valid, validation_errors = validate_snapshot(snapshot)
    expected = snapshot.get("identity") if isinstance(snapshot, dict) else {}
    current = event_identity(events)
    source_errors = [item.get("error", "source read was partial") for item in errors]
    source_state = "unknown"
    identity_errors = []
    if getattr(thread, "id", None) != snapshot.get("id"):
        identity_errors.append("resolved thread id differs from the snapshot")
    if source != snapshot.get("source"):
        identity_errors.append("resolved source differs from the snapshot")
    if not errors:
        source_state = "matched" if current == expected and not identity_errors else "mismatch"
    report_errors = list(validation_errors)
    report_errors.extend(identity_errors)
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
