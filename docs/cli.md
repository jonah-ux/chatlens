# CLI reference — 0.1

The executable is `chatlens`; `python -m chatlens` exposes the same interface. `--help` prints usage, and `--version` prints the package version. Commands are noninteractive and local.

| Command | Arguments | Result |
| --- | --- | --- |
| `list` | `--source codex\|claude\|hermes`, `--origin NAME`, `--since SECONDS`, `--limit N`, `--json` | Discovered threads; default limit 50; limit 0 lists all |
| `index` | `--source SOURCE`, `--json`, `--fail-on-error` | Replaces the selected source inventories in the local search cache |
| `search QUERY` | `--limit N`, `--json` | Cached full-text matches; default limit 20 |
| `read REF` | `--mode brief\|convo\|full`, `--budget TOKENS`, `--no-ts` | Plain-text transcript rendering; default mode `convo` |
| `card REF` | `--json` | Deterministic work card or Markdown |
| `bundle REF` | `--out PATH`, `--json` | Content-addressed recovery snapshot; output files are new owner-only files |
| `verify-bundle PATH` | `--json` | Verify snapshot integrity and compare it with the current source |
| `trace-export REF` | `--out PATH`, `--json` | Bounded redacted JSONL trace envelope for an external local reader |
| `trace-import PATH` | `--json` | Validate a trace envelope without opening native source stores |

`REF` accepts a source-qualified ID such as `codex:SESSION_ID`, a unique native ID/prefix, or an inventory path. Hermes IDs include a home label; for example, `hermes:hermes:SESSION_ID` is the source-qualified form for the default home. Unknown custom Hermes home mappings have no generated resume command.

## JSON protocol

JSON modes return one document; diagnostics use stderr. The top-level schema IDs are `chatlens-list/v1`, `chatlens-index/v1`, `chatlens-search/v1`, `chatlens-card/v1`, `chatlens-snapshot/v1`, `chatlens-snapshot-verify/v1`, `chatlens-trace-export/v1`, `chatlens-trace-import/v1`, and `chatlens-error/v1`. Argument-parser failures use stderr without a JSON document.

- List: `threads` is an array of source/ID/path/title/activity rows. `coverage.sources` records the selected inventories; `errors` describes incomplete discovery.
- Index: `indexed`, `failed`, `failures`, `partial`, timing fields, and `coverage`. Refreshing one source does not classify unselected sources as missing.
- Search: `matches` is an array of source/ID/title/snippet rows. `index.sources` records each cached source's refresh time and coverage. `index.freshness` explicitly labels the result as cached.
- Card: `goal`, `activity`, `work`, `claims`, `continue`, and `input`. `claims.verification` labels transcript outcomes unverified; `input.status` is complete or partial. Resume strings quote identifiers and preserve native Hermes profile/session identity.
- Snapshot: `source`, `id`, `title`, `origin`, `identity`, `card`, and `input`. `identity.events_sha256` covers the ordered bounded event stream while keeping event text out of the artifact. `snapshot_sha256` covers the snapshot contents.
- Snapshot verification: `ok` is true only for a valid complete snapshot whose current source event identity matches. `source_state` is `matched`, `mismatch`, or `unknown`; a mismatch is exit code 1 and partial/invalid evidence is exit code 3.
- Trace export: with `--out`, the command writes canonical JSONL and emits a `chatlens-trace-export/v1` summary. Without `--out`, it prints the header and event rows; adding `--json` requests the summary instead. `input_status` is `complete` or `partial`.
- Trace import: `chatlens-trace-import/v1` validates the header, contiguous event sequence, bounds, redacted event digest, and envelope digest. `trace_state` is `matched` or `mismatch`; complete matching input is exit code 0, digest mismatch is exit code 1, and malformed or partial evidence is exit code 3.
- Error: `status: error`, `error`, and, for reference/usage errors, `exit_code`.

Coverage statuses are readable, empty, absent, partial, unreadable, or unknown. A missing source installation is different from an unreadable installed source. Exit codes are 0 completed, 1 reference absent in a readable inventory, 2 usage/ambiguity, and 3 partial or unavailable evidence. Indexing returns 0 for a partial report unless `--fail-on-error` is present; the report still identifies the partial state.

## Trace envelope interoperability

`trace-export` writes a first-line `chatlens-trace-envelope/v1` header followed by
event lines shaped for local JSONL trace readers. Text and metadata are recursively
redacted before hashing and output. The header never includes the raw session ID;
`session_id_sha256` is used to bind the handoff to its source without exporting that
identifier. `events_sha256` covers the canonical redacted rows, while
`envelope_sha256` covers the unsigned header plus those rows. A receiving tool can
consume the event lines directly, or `trace-import` can be used as the integrity
gate before rendering. The source store is not needed for import and is never written.

## Storage

`CODEX_HOME` chooses the Codex root. Claude and Hermes discovery uses their native paths beneath the current user's home. `CHATLENS_HOME` selects the generated cache; its default is `~/.local/share/chatlens`. `CHATLENS_NODE` labels results. New cache directories use mode 700 and the database uses mode 600; unsafe existing permissions are refused.

Native SQLite inputs are opened read-only. The cache contains local transcript excerpts; content is not redacted. There is no upload or model request in the package. These output schemas describe recorded data and cache coverage, not current task ownership or runtime liveness.
