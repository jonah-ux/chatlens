# Source provenance

Chatlens adapts the reusable Chatlens parsers, event model, and renderer from Jonah's private Fleet repository, pinned to `8d6040d8b9710b8da2090bbab94ad088137bed4d` during extraction. The five original model/adapter/renderer paths have 17 historical source changes at that pin.

This public repository starts with a reviewed standalone snapshot. Private Fleet history has not been transplanted: commit messages and older source versions have a broader disclosure surface than the current portable files. The public history records real extraction, correctness fixes, tests, documentation, and subsequent releases; it contains no padded activity or backdated commits.

The portable source is released by its owner under the MIT license. Fleet orchestration, remote access, account/provider routing, credentials, business telemetry, and private transcripts are excluded. All distributed fixtures and demos are synthetic.

## License provenance

Provenance: ChatLens parser, event model, and renderer include adapted code
from Jonah Helland's MIT-licensed ChatLens kit in jonah-ux/fleet, initially
introduced 2026-09-24. Public source-history review for this bundle remains
pending; see docs/releasing.md before publishing. Other project material was
written for this standalone distribution.
