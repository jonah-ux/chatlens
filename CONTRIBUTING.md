# Contributing

Thanks for helping improve ChatLens. Keep this project local-first and provider-neutral.

- Python 3.11+; production dependencies are standard-library only.
- Do not add network, SSH, model-provider, Fleet, credential, or telemetry dependencies to the portable core.
- Use synthetic data in tests. Never add private transcripts, user paths, live IDs, or credential files.
- Readers must open source SQLite stores read-only and never mutate native transcripts.
- Preserve ambiguity refusal and claim-versus-verification semantics.
- Run `python -m unittest discover -s tests -v` and `python -m compileall -q src tests` before proposing changes.
- Keep the version in `pyproject.toml` and `src/chatlens/__init__.py` consistent. Maintainers own release tags and publication.

See [Security](SECURITY.md), [agent usage](README.md#agent-interface), and [releasing](docs/releasing.md).
