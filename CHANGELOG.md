# Changelog

All notable changes to ChatLens are recorded here.

## [0.2.1] — snapshot identity hardening

- Reject snapshot verification when the resolved source or session identity differs from the captured identity.
- Add an adversarial regression fixture for resolver identity drift.

## [0.2.0] — recovery snapshots

- Added `bundle` and `verify-bundle` with `chatlens-snapshot/v1` and `chatlens-snapshot-verify/v1` for compact, content-addressed session recovery.
- Snapshots preserve work-card claims and bounded event identity without copying transcript text; verification fails closed when the snapshot or source changes.

## Unreleased

- Added `bundle` and `verify-bundle` with `chatlens-snapshot/v1` and
  `chatlens-snapshot-verify/v1` for compact, content-addressed session recovery.
  Snapshots preserve work-card claims and bounded event identity without copying
  transcript text; verification fails closed when the snapshot or source changes.

## [0.1.0] — public prerelease

- Standalone offline CLI for Codex, Claude Code, and Hermes transcript stores.
- Common parser event model with bounded brief/conversation/full rendering.
- Local SQLite FTS5 index and JSON output for automation.
- Deterministic cards that distinguish transcript claims from verified evidence.
- Synthetic provider fixtures and demos; no network, Fleet, SSH, private credentials, OpenRouter, or Supabase dependencies.

[0.2.0]: https://github.com/jonah-ux/chatlens/releases/tag/v0.2.0
[0.1.0]: https://github.com/jonah-ux/chatlens/releases/tag/v0.1.0
