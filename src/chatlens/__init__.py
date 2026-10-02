"""ChatLens: offline readers and search for local AI coding-agent transcripts."""

__version__ = "0.3.0"

# Portable recovery-to-trace handoff primitives are part of the public library
# surface as well as the CLI. Native adapters remain internal implementation
# details; callers can validate a received envelope without opening them.
from .trace import build_trace, import_report, read_trace, validate_trace, write_trace

__all__ = ["__version__", "build_trace", "import_report", "read_trace", "validate_trace", "write_trace"]
