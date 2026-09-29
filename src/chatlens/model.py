"""Shared event model for every chat source.

Every adapter turns its native store into a stream of Event rows so the
renderer, indexer and summarizer never care which runtime wrote the chat.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field

# Event kinds, in the order a reader usually cares about them.
USER = "user"            # text the human actually typed
ASSISTANT = "assistant"  # visible reply text
THINKING = "thinking"    # reasoning text or headline (Codex: summary only)
TOOL_CALL = "tool_call"  # command / tool invocation
TOOL_RESULT = "tool_result"
SUMMARY = "summary"      # runtime compaction / summary record
TURN = "turn"            # turn boundary with status (completed/interrupted/...)
META = "meta"            # model switch, cwd, etc.

KINDS = (USER, ASSISTANT, THINKING, TOOL_CALL, TOOL_RESULT, SUMMARY, TURN, META)


def est_tokens(text: str) -> int:
    """chars/4 estimate. Good to ~±20% for English + code; labelled as an estimate everywhere."""
    return (len(text) + 3) // 4


def clean(text):
    """Replace lone UTF-16 surrogates (JSON "\\ud83d" with no pair) with U+FFFD. Python accepts them from
    json.loads, but printing, sqlite and hashing all raise UnicodeEncodeError on them, which used to abort a
    whole `index` run over one bad message."""
    if not isinstance(text, str):
        return text
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


@dataclass
class Event:
    ts: float | None          # unix seconds, may be None when the store has no timestamp
    kind: str
    text: str
    name: str = ""            # tool name for tool_call / tool_result
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.text = clean(self.text)

    @property
    def tokens(self) -> int:
        return est_tokens(self.text)


@dataclass
class Thread:
    source: str               # codex | claude | hermes
    id: str
    node: str
    path: str                 # file path or db path (+ '#session_id' for hermes)
    title: str = ""
    started: float | None = None
    updated: float | None = None
    cwd: str = ""
    model: str = ""
    origin: str = ""          # human | subagent | exec | cron | channel...
    size_bytes: int = 0
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.title, self.cwd = clean(self.title), clean(self.cwd)

    def as_row(self) -> dict:
        return {
            "source": self.source, "id": self.id, "node": self.node, "path": self.path,
            "title": self.title, "started": self.started, "updated": self.updated, "cwd": self.cwd,
            "model": self.model, "origin": self.origin, "size_bytes": self.size_bytes,
        }


# Timestamps outside 1970..2100 are corrupt or skewed beyond use; they become "unknown" instead of crashing
# datetime (1e20 -> OSError/EOVERFLOW, year 1 -> ValueError).
TS_MIN, TS_MAX = 0.0, 4_102_444_800.0


def iso_to_ts(value) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)):
            ts = float(value) / 1000.0 if value > 1e11 else float(value)
        else:
            ts = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return None
    return ts if TS_MIN < ts < TS_MAX else None


def fmt_ts(ts: float | None, fmt: str = "%m-%d %H:%M") -> str:
    if not ts:
        return "--"
    try:
        return _dt.datetime.fromtimestamp(ts).strftime(fmt)
    except (ValueError, OverflowError, OSError):
        return "--"


def warn_bad_lines(path: str, bad: int) -> None:
    """Unparseable JSONL lines are skipped, but never silently. A chat that is still being written can end in
    one partial line; more than that means the file is damaged."""
    if bad:
        import sys
        print(f"chatlens: {path}: skipped {bad} unparseable line{'s' if bad != 1 else ''}", file=sys.stderr)


def content_text(content) -> str:
    """Flatten the many content shapes (str, list of blocks, dict) into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "content", "output", "input"):
            if key in content:
                return content_text(content[key])
        return ""
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type", "")
                if btype in ("image", "input_image", "image_url"):
                    parts.append("[image]")
                else:
                    parts.append(content_text(block))
        return "\n".join(p for p in parts if p)
    return str(content)
