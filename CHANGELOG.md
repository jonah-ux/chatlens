# Changelog

All notable changes to ChatLens are recorded here.

## [0.3.0] — portable trace handoff

- Add `trace-export` and `trace-import` with `chatlens-trace-envelope/v1` and
  `chatlens-trace-import/v1` for bounded, redacted JSONL handoffs to local trace
  readers. Canonical event rows and the header are bound with SHA-256 digests;
  tampered, malformed, or partial evidence fails closed.
- Expose the trace envelope helpers through the public `chatlens` package and
  add a synthetic recovery roundtrip demo that proves redaction and integrity
  without reading a native transcript store.

## [0.2.2] — release checksum portability

- Generate release checksums from artifact basenames so `sha256sum -c SHA256SUMS` works after GitHub asset download.
- Pin the public install and demo instructions to the current `v0.2.2` release.

## [0.2.1] — snapshot identity hardening

- Reject snapshot verification when the resolved source or session identity differs from the captured identity.
- Add an adversarial regression fixture for resolver identity drift.

## [0.2.0] — recovery snapshots

- Added `bundle` and `verify-bundle` with `chatlens-snapshot/v1` and `chatlens-snapshot-verify/v1` for compact, content-addressed session recovery.
- Snapshots preserve work-card claims and bounded event identity without copying transcript text; verification fails closed when the snapshot or source changes.

## Unreleased

## [0.1.0] — public prerelease

- Standalone offline CLI for Codex, Claude Code, and Hermes transcript stores.
- Common parser event model with bounded brief/conversation/full rendering.
- Local SQLite FTS5 index and JSON output for automation.
- Deterministic cards that distinguish transcript claims from verified evidence.
- Synthetic provider fixtures and demos; no network, Fleet, SSH, private credentials, OpenRouter, or Supabase dependencies.

[0.3.0]: https://github.com/jonah-ux/chatlens/releases/tag/v0.3.0
[0.2.0]: https://github.com/jonah-ux/chatlens/releases/tag/v0.2.0
[0.1.0]: https://github.com/jonah-ux/chatlens/releases/tag/v0.1.0
