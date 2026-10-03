# ChatLens projection: `ai-work-evidence/v1`

ChatLens remains the owner of `chatlens-trace-envelope/v1`. The additive `evidence-export`
command projects a validated, redacted trace into the portfolio suite's shared evidence shape.
It carries one artifact name, size, and SHA-256 digest plus scalar trace provenance. It does not
copy event text, native session IDs, source paths, or provider metadata.

Complete trace input becomes `observed`; a valid partial trace becomes `unknown`. The projection
does not claim ownership, liveness, deployment, or a provider outcome. Forgeyard can consume the
result with its `compose` command without installing ChatLens or opening a native transcript store.

ChatLens's consumer conformance fixture is under
[`tests/fixtures/agent-systems-lab/conformance.json`](../../tests/fixtures/agent-systems-lab/conformance.json).
It mirrors the Forgeyard-owned corpus classifications for complete, partial, tampered, and
unknown-version inputs without importing Forgeyard at runtime.

Example:

```bash
chatlens evidence-export ./artifacts/synthetic.trace.jsonl \
  --id fixture-session:001 \
  --subject "Synthetic release investigation" \
  --summary "A bounded local fixture" \
  --created-at 2026-01-01T00:00:00Z \
  --fixture-id portfolio-suite-v2 \
  --out ./artifacts/chatlens.evidence.json \
  --json
```
