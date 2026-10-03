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
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [ROOT / value for value in result.stdout.decode("utf-8", errors="ignore").split("\0") if value]


def _secret_scan() -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    scanned = 0
    for path in _tracked_files():
        if not path.is_file() or path.name in {"SHA256SUMS"}:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue
        scanned += 1
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:
            relative = path.name
        for name, pattern in _SECRET_PATTERNS:
            if pattern.search(raw):
                findings.append({"path": relative, "class": name})
    return {"state": "pass" if not findings else "blocked", "files_scanned": scanned, "findings": findings}


def _project() -> dict[str, Any] | None:
    try:
        return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8")).get("project", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None


def _dependency_inventory() -> dict[str, Any]:
    try:
        payload = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return {"state": "blocked", "error": str(exc)}
    project = payload.get("project", {})
    build = payload.get("build-system", {})
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
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return {"state": "blocked", "error": str(exc)}
    license_field = project.get("license") if project else None
    license_name = license_field if isinstance(license_field, str) else license_field.get("file") if isinstance(license_field, dict) else None
    return {
        "state": "pass" if license_text.strip() and license_name else "blocked",
        "license_file": "LICENSE" if license_text.strip() else None,
        "license_declared": license_name,
    }


def _release_provenance() -> dict[str, Any]:
    workflow = ROOT / ".github" / "workflows" / "release.yml"
    markers = {
        "checksum_manifest": "SHA256SUMS",
        "tag_gate": "refs/tags",
        "artifact_build": "build",
        "release_publish": "gh release",
    }
    try:
        workflow_text = workflow.read_text(encoding="utf-8")
    except OSError as exc:
        return {"state": "blocked", "error": str(exc)}
    present = {name: marker in workflow_text for name, marker in markers.items()}
    docs = {name: (ROOT / name).is_file() for name in ("PROVENANCE.md", "SECURITY.md")}
    return {"state": "pass" if all(present.values()) and all(docs.values()) else "blocked", "workflow_markers": present, "docs": docs}


def _checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 2 and _SHA256.fullmatch(parts[0]):
            result[Path(parts[-1]).name] = parts[0]
    return result


def _artifact_audit(dist_dir: Path | None) -> dict[str, Any]:
    if dist_dir is None:
        return {"state": "unavailable", "reason": "dist_dir_not_provided"}
    if not dist_dir.is_dir():
        return {"state": "unavailable", "reason": "dist_dir_missing"}
    sums = dist_dir / "SHA256SUMS"
    assets = sorted(path for path in dist_dir.iterdir() if path.is_file() and path.suffix in {".whl", ".gz"})
    if not assets or not sums.is_file():
        return {"state": "unavailable", "reason": "artifacts_or_checksums_missing"}
    expected = _checksums(sums)
    observations = []
    mismatches = []
    for path in assets:
        digest = _digest(path.read_bytes())
        observations.append({"name": path.name, "bytes": path.stat().st_size, "sha256": digest})
        if expected.get(path.name) != digest:
            mismatches.append(path.name)
    return {
        "state": "pass" if not mismatches and len(assets) >= 2 else "blocked",
        "assets": observations,
        "mismatches": mismatches,
        "checksum_manifest_sha256": _digest(sums.read_bytes()),
    }


def audit(dist_dir: Path | None = None) -> dict[str, Any]:
    dependency = _dependency_inventory()
    license_info = _license_inventory()
    provenance = _release_provenance()
    privacy = _secret_scan()
    artifacts = _artifact_audit(dist_dir)
    static_pass = all(item.get("state") == "pass" for item in (dependency, license_info, provenance, privacy))
    artifact_pass = artifacts.get("state") in {"pass", "unavailable"}
    return {
        "schema": SCHEMA,
        "source": {"revision": _revision(), "python": platform.python_version()},
        "dependency_inventory": dependency,
        "license_inventory": license_info,
        "release_provenance": provenance,
        "privacy_scan": privacy,
        "artifact_audit": artifacts,
        "result": "pass" if static_pass and artifact_pass else "blocked",
        "limits": [
            "secret scanning uses high-signal patterns and is not a complete semantic DLP system",
            "artifact checks are unavailable without an explicit dist directory",
            "a passing audit does not claim security, deployment, adoption, or production readiness",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = audit(args.dist_dir)
    print(json.dumps(report, indent=2 if args.json else None, sort_keys=True))
    return 0 if report["result"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
