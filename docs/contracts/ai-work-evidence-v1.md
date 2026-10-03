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
It pins the Forgeyard-owned manifest at commit
`d6feb5b0ec0f7ccb4fb7f56939e8513972b0855d` and records its SHA-256
`6fe5fc6c5993f161111110971927b07e7db4b7d9f01eac352afd173ce31e7924`.
The local tests carry all seven owner case names and expected validity/status classifications,
then exercise ChatLens's own projection and refusal mutations. They do not import Forgeyard or
add it as an install-time or runtime dependency.

ChatLens emits `observed` for a complete validated trace and `unknown` for a valid partial trace.
The `status-failed` case is checked at the shared-shape validator boundary because a trace that
fails validation is rejected before projection; ChatLens does not invent a failed outcome for an
invalid trace. The remaining four owner cases are exercised as schema, artifact-name, digest,
and root-shape refusal mutations. Forgeyard remains the reference validator for the shared
contract; this fixture records compatibility expectations rather than taking ownership of it.

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
