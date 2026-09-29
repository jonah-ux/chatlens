"""Small deterministic fact extractor shared by cards and index digests."""
from __future__ import annotations

import json
import re
from collections import Counter

from .model import ASSISTANT, SUMMARY, TOOL_CALL, TOOL_RESULT, USER

PR_URL = re.compile(r"github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
PR_REF = re.compile(r"\bPR\s*#(\d{2,7})\b", re.I)
KANBAN = re.compile(r"(?i)\bkanban\s*#?(\d{4,8})\b|#(2\d{5})\b")
SHA = re.compile(r"\b(?:commit|merge(?:d)?|head|sha)[\s:=`]*([0-9a-f]{7,40})\b", re.I)
PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)
SHELL_SPLIT = re.compile(r"(?:&&|\|\||;|\||\n|\$\(|`)")
SKIP_WORDS = {"cd", "echo", "cat", "ls", "grep", "sed", "awk", "head", "tail", "python", "python3",
              "bash", "sh", "rg", "find", "wc", "sort", "uniq", "true", "false", "test", "mkdir"}


def _command_text(event) -> str:
    text = event.text or ""
    if text.lstrip().startswith("{"):
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            value = {}
        for key in ("command", "cmd", "script", "code"):
            if isinstance(value.get(key), str):
                return value[key]
    return text


def _file_paths(event) -> list[str]:
    text = event.text or ""
    found = PATCH_FILE.findall(text)
    if text.lstrip().startswith("{"):
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            value = {}
        for key in ("file_path", "path", "notebook_path"):
            if isinstance(value.get(key), str):
                found.append(value[key])
    return found


def extract(events: list, thread=None) -> dict:
    kinds = Counter(event.kind for event in events)
    tools, files = Counter(), Counter()
    prs, kanban, shas = set(), set(), set()
    text_parts = []
    for event in events:
        if event.kind == TOOL_CALL:
            tools[event.name or "?"] += 1
            for path in _file_paths(event):
                files[path] += 1
        if event.kind in (USER, ASSISTANT, SUMMARY):
            text_parts.append(event.text[:20000])
    blob = "\n".join(text_parts)
    prs.update(f"{repo}#{number}" for repo, number in PR_URL.findall(blob))
    prs.update(f"#{number}" for number in PR_REF.findall(blob)
               if not any(item.endswith(f"#{number}") for item in prs))
    kanban.update(a or b for a, b in KANBAN.findall(blob))
    shas.update(item[:12] for item in SHA.findall(blob))
    timestamps = [event.ts for event in events if event.ts is not None]
    return {
        "counts": {kind: kinds.get(kind, 0) for kind in (USER, ASSISTANT, TOOL_CALL, TOOL_RESULT, SUMMARY)},
        "first_ts": min(timestamps) if timestamps else None,
        "last_ts": max(timestamps) if timestamps else None,
        "active_hours": round((max(timestamps) - min(timestamps)) / 3600, 2) if len(timestamps) > 1 else 0,
        "tools_top": tools.most_common(25), "files_touched_top": files.most_common(60),
        "prs": sorted(prs), "kanban_ids": sorted(kanban), "shas": sorted(shas),
        "tool_errors": sum(1 for event in events if event.kind == TOOL_RESULT and event.extra.get("error")),
    }
