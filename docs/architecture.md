# Architecture

Chatlens keeps source-specific parsing behind one event model:

```text
Codex JSONL  ─┐
Claude JSONL ─┼─> source adapter -> Event/Thread model -> index -> search/read/card
Hermes SQLite ─┘                                      \-> bounded JSON contracts
```

## Ownership boundaries

- **Adapters** (`codex.py`, `claude.py`, `hermes.py`) discover native stores, read them read-only, and translate source records into `Thread` and `Event` values. A malformed or incomplete source is reported as partial evidence rather than converted into a successful-looking conversation.
- **Shared model** (`model.py`) normalizes event kinds, timestamps, text, UTF-8 safety, and bounded token estimates. It is the compatibility boundary between changing native formats and the rest of the tool.
- **Facts and cards** (`facts.py`, `card.py`) derive compact, source-neutral work facts from normalized events. They do not reparse native stores or invent missing reasoning.
- **Index and search** (`index.py`, `search.py`) own the private SQLite cache. Refresh replaces only the selected source coverage; native stores remain untouched. Cache paths must be private and symlink-safe.
- **CLI and rendering** (`cli.py`, `render.py`) expose stable JSON/terminal output and exit codes. They report coverage, partial state, and unknowns instead of collapsing them into “not found”.

The largest adapter functions are intentionally organized around the native reader contract: inventory, archive discovery, and bounded event parsing. Any future extraction should preserve source-specific refusal behavior and the shared event interface; splitting by line count alone would make format compatibility harder to audit.

## Invariants

1. Native transcript files and Hermes databases are read-only inputs.
2. Every source is reported separately as complete, partial, absent, unreadable, or unknown.
3. A partial parse remains partial through indexing, search, cards, and rendered output.
4. Missing native reasoning stays missing; encrypted Codex reasoning is never decrypted.
5. Index refresh cannot leave deleted or excluded sessions in selected source coverage.
6. IDs are source-qualified; ambiguous references refuse to guess.
7. Reads are bounded by file size, event count, retained text, and title-scan limits.
8. User-visible strings are UTF-8 safe even when a JSON parser accepts lone UTF-16 surrogates or replacement bytes.

## Failure model

Malformed JSONL, invalid UTF-8, truncated writer records, corrupt inventories, missing indexes, unsafe cache permissions, symlinked cache paths, ambiguous IDs, unavailable source roots, and exceeded read bounds produce structured partial/error results. A readable empty inventory is different from an unavailable inventory. The tool never treats missing evidence as proof that a conversation did not exist.

## Privacy boundary

Chatlens is local-first. It does not make network or model calls. Raw transcript content stays in the local source and private cache paths; cards and search results can still contain sensitive text, so users should treat generated output as private. The tool does not read credentials or decrypt provider-owned reasoning.

## Non-goals

Chatlens is not a hosted conversation archive, a model client, a transcript repairer, or a guarantee that a session is currently active. It provides bounded local readback with explicit coverage and uncertainty.
