# ChatLens demo

`demo.py` creates temporary synthetic Codex, Claude Code, and Hermes stores, invokes the real module CLI, builds the real SQLite FTS5 index, searches it, renders a bounded transcript, and emits a deterministic card. It deletes the temporary directory on exit.

```console
python demos/demo.py
```

No real home directories, network services, credentials, or model calls are used.

## Portable recovery handoff

`recovery_trace_roundtrip.py` builds a bounded, redacted trace envelope, writes JSONL, and validates it through the public import surface. It uses synthetic events only.
