# Security policy

ChatLens reads personal conversation history. Treat its input stores and generated index as sensitive local data.

## Safe handling

- Keep `CHATLENS_HOME` out of source control; it may contain transcript excerpts.
- Use fixture data in bug reports and tests. Redact names, prompts, paths, tokens, URLs, and IDs.
- ChatLens does not authenticate, contact a network, use SSH, call a model provider, or read credential files.
- Native Codex/Hermes SQLite databases are opened read-only. Do not run ChatLens against a copied store with write permissions as a substitute for source safety.
- Report a security issue privately to the project owner before public disclosure. Do not include a real transcript or secret in the report.

This policy covers the standalone public project. Fleet runtime integrations are deliberately outside its trust boundary.
