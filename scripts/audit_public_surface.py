#!/usr/bin/env python3
"""Audit ChatLens's public supply-chain and privacy surface."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "chatlens-public-audit/v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(rb"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{20,}\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_root_text(relative: str) -> str:
    path = ROOT / relative
    if path.is_symlink():
        raise ValueError(f"{relative} is a symlink")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT.resolve())
        return resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{relative} is unreadable: {exc}") from exc


def _revision() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False)
    except OSError:
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def _tracked_files() -> list[Path]:
    try:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=False)
    except OSError as exc:
        raise RuntimeError(f"git ls-files failed: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files exited with {result.returncode}")
    return [ROOT / value for value in result.stdout.decode("utf-8", errors="ignore").split("\0") if value]


def _secret_scan() -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    errors: list[str] = []
    scanned = 0
    skipped = 0
    try:
        tracked_files = _tracked_files()
    except RuntimeError as exc:
        return {"state": "blocked", "files_scanned": 0, "skipped": 0, "findings": [], "errors": [str(exc)]}
    if not tracked_files:
        return {"state": "blocked", "files_scanned": 0, "skipped": 0, "findings": [], "errors": ["tracked_file_list_empty"]}
    for path in tracked_files:
        if path.is_symlink():
            findings.append({"path": path.name, "class": "symlink"})
            continue
        if not path.is_file() or path.name in {"SHA256SUMS"}:
            skipped += 1
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            errors.append(f"{path.name}: {exc}")
            skipped += 1
            continue
        if b"\0" in raw[:4096]:
            skipped += 1
            continue
        scanned += 1
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = path.name
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(raw):
                findings.append({"path": relative, "class": name})
    if scanned == 0 and not errors:
        errors.append("no_readable_text_files")
    state = "pass" if scanned > 0 and not findings and not errors else "blocked"
    return {"state": state, "files_scanned": scanned, "skipped": skipped, "findings": findings, "errors": errors}


def _project() -> dict[str, Any] | None:
    try:
        project = tomllib.loads(_safe_root_text("pyproject.toml")).get("project", {})
        return project if isinstance(project, dict) else None
    except (ValueError, tomllib.TOMLDecodeError):
        return None


def _dependency_inventory() -> dict[str, Any]:
    try:
        payload = tomllib.loads(_safe_root_text("pyproject.toml"))
    except (ValueError, tomllib.TOMLDecodeError) as exc:
        return {"state": "blocked", "error": str(exc)}
    project = payload.get("project", {})
    build = payload.get("build-system", {})
    if not isinstance(project, dict) or not isinstance(build, dict):
        return {"state": "blocked", "error": "project and build-system must be tables"}
    dependencies = project.get("dependencies", [])
    build_requires = build.get("requires", [])
    optional = project.get("optional-dependencies", {})
    valid = isinstance(dependencies, list) and all(isinstance(item, str) for item in dependencies)
    valid = valid and isinstance(build_requires, list) and all(isinstance(item, str) for item in build_requires)
    return {
        "state": "pass" if valid else "blocked",
        "runtime_dependencies": sorted(dependencies) if valid else [],
        "build_dependencies": sorted(build_requires) if valid else [],
        "optional_groups": sorted(optional) if isinstance(optional, dict) else [],
    }


def _license_inventory() -> dict[str, Any]:
    project = _project()
    try:
        license_text = _safe_root_text("LICENSE")
    except ValueError as exc:
        return {"state": "blocked", "error": str(exc)}
    license_field = project.get("license") if project else None
    license_name = license_field if isinstance(license_field, str) else license_field.get("file") if isinstance(license_field, dict) else None
    return {
        "state": "pass" if license_text.strip() and license_name else "blocked",
        "license_file": "LICENSE" if license_text.strip() else None,
        "license_declared": license_name,
    }


def _release_provenance() -> dict[str, Any]:
    try:
        workflow_text = _safe_root_text(".github/workflows/release.yml")
    except ValueError as exc:
        return {"state": "blocked", "error": str(exc)}
    workflow_text = "\n".join(line for line in workflow_text.splitlines() if not line.lstrip().startswith("#"))
    present = {
        "checksum_manifest": "SHA256SUMS" in workflow_text and bool(re.search(r"\bsha256sum\b", workflow_text)),
        "tag_gate": "refs/tags" in workflow_text or bool(re.search(r"(?m)^\s+tags:\s*$", workflow_text)),
        "artifact_build": bool(re.search(r"\bpython(?:3)?\s+-m\s+build\b", workflow_text)),
        "release_publish": "gh release create" in workflow_text,
    }
    docs = {}
    for name in ("PROVENANCE.md", "SECURITY.md"):
        try:
            docs[name] = bool(_safe_root_text(name).strip())
        except ValueError:
            docs[name] = False
    return {"state": "pass" if all(present.values()) and all(docs.values()) else "blocked", "workflow_markers": present, "docs": docs}


def _checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"checksum manifest unreadable: {exc}") from exc
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or not _SHA256.fullmatch(parts[0]):
            raise ValueError(f"checksum manifest line {index} is malformed")
        filename = parts[1].strip().replace("\\", "/")
        if filename.startswith("*"):
            filename = filename[1:]
        if filename.startswith("dist/"):
            filename = filename[5:]
        path_parts = Path(filename).parts
        if not filename or filename.startswith("/") or ".." in path_parts or Path(filename).name != filename or filename in result:
            raise ValueError(f"checksum manifest filename on line {index} is invalid or duplicated")
        result[filename] = parts[0]
    return result


def _artifact_audit(dist_dir: Path | None) -> dict[str, Any]:
    if dist_dir is None:
        return {"state": "unavailable", "reason": "dist_dir_not_provided"}
    if dist_dir.is_symlink() or not dist_dir.is_dir():
        return {"state": "blocked", "reason": "dist_dir_missing_or_symlink"}
    sums = dist_dir / "SHA256SUMS"
    candidates = sorted(path for path in dist_dir.iterdir() if path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    if any(path.is_symlink() or not path.is_file() for path in candidates):
        return {"state": "blocked", "reason": "artifact_symlink_or_unreadable"}
    assets = candidates
    wheels = [path for path in assets if path.suffix == ".whl"]
    sdists = [path for path in assets if path.name.endswith(".tar.gz")]
    if len(wheels) != 1 or len(sdists) != 1 or not sums.is_file() or sums.is_symlink():
        return {"state": "blocked", "reason": "exactly_one_wheel_sdist_and_checksum_manifest_required"}
    try:
        expected = _checksums(sums)
    except ValueError as exc:
        return {"state": "blocked", "reason": "checksum_manifest_invalid", "error": str(exc)}
    if set(expected) != {path.name for path in assets}:
        return {"state": "blocked", "reason": "checksum_manifest_asset_set_mismatch"}
    observations = []
    mismatches = []
    for path in assets:
        try:
            digest = _digest(path.read_bytes())
        except OSError as exc:
            return {"state": "blocked", "reason": "artifact_unreadable", "error": str(exc)}
        observations.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
        if expected.get(path.name) != digest:
            mismatches.append(path.name)
    return {
        "state": "pass" if not mismatches and len(assets) >= 2 else "blocked",
        "assets": observations,
        "mismatches": mismatches,
        "checksum_manifest_sha256": _digest(sums.read_bytes()),
    }


def audit(dist_dir: Path | None = None, require_dist: bool = False) -> dict[str, Any]:
    revision = _revision()
    dependency = _dependency_inventory()
    license_info = _license_inventory()
    provenance = _release_provenance()
    privacy = _secret_scan()
    artifacts = _artifact_audit(dist_dir)
    static_pass = bool(revision) and all(item.get("state") == "pass" for item in (dependency, license_info, provenance, privacy))
    artifact_pass = artifacts.get("state") == "pass" or (artifacts.get("state") == "unavailable" and not require_dist)
    return {
        "schema": SCHEMA,
        "source": {"revision": revision, "python": platform.python_version()},
        "dependency_inventory": dependency,
        "license_inventory": license_info,
        "release_provenance": provenance,
        "privacy_scan": privacy,
        "artifact_audit": artifacts,
        "result": "pass" if static_pass and artifact_pass else "blocked",
        "limits": [
            "secret scanning uses high-signal patterns and is not a complete semantic DLP system",
            "artifact checks are unavailable without an explicit dist directory",
            "pass --require-dist when artifact evidence is required for a release review",
            "a passing audit does not claim security, deployment, adoption, or production readiness",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path)
    parser.add_argument("--require-dist", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit(args.dist_dir, require_dist=args.require_dist)
    print(json.dumps(report, indent=2 if args.json else None, sort_keys=True))
    return 0 if report["result"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
