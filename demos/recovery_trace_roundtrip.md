# Recovery trace round trip

`recovery_trace_roundtrip.py` shows the portable handoff boundary: a caller turns a bounded local event stream into a redacted Chatlens trace envelope, writes it as JSONL, and a separate reader validates the envelope digest and completeness before consuming it. The fixture includes an email address, home path, and query token so the output proves those fields are redacted before handoff.

```console
python3 demos/recovery_trace_roundtrip.py
```

The example uses synthetic events only and does not inspect a real local transcript store.
