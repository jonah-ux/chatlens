# Releases

Use semantic versions. Until 1.0, minor versions may change interfaces; patch versions preserve documented commands and JSON schemas. Each published version has an annotated Git tag, changelog entry, source distribution, wheel, and SHA-256 checksum file attached to its GitHub release.

1. Update `pyproject.toml`, `src/chatlens/__init__.py`, and `CHANGELOG.md` together. Describe changed commands or schemas explicitly.
2. Run fixture tests on supported Python versions and operating systems. Review privacy and source immutability regressions.
3. Build with `python3 -m build --sdist --wheel`. Install each artifact into a fresh environment and invoke `chatlens --version`, `--help`, and `python3 demos/demo.py` without a source-path override.
4. Review the distributed file list and scan the current tree and Git history for secrets. Never distribute native histories, indexes, local credentials, or developer environments.
5. Tag the reviewed commit with `git tag -a vX.Y.Z -m 'Chatlens X.Y.Z'`. Push the commit and tag through the repository's approved release route.
6. Publish the release with the source distribution, wheel, and `SHA256SUMS`. Include changes, compatibility notes, and tested platforms. Mark experimental versions as prereleases.
7. Download an artifact from the published release, verify its checksum, install it in a fresh environment, and invoke the installed CLI. A local build is not proof that the downloadable release works.

No PyPI publication is implied by a GitHub release. The README uses the versioned Git tag installation route.
