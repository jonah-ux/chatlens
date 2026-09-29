"""Deterministic work cards built only from local transcript events."""
from __future__ import annotations

from collections import Counter

from .facts import extract
from .model import ASSISTANT, USER, fmt_ts

SCHEMA = "chatlens-card/v1"


def continue_command(source: str, session_id: str) -> str:
    return {"codex": f"codex resume {session_id}",
            "claude": f"claude --resume {session_id}",
            "hermes": f"hermes --resume {session_id}"}.get(source, "")


def build(source: str, thread_id: str, title: str, events: list, node: str = "local") -> dict:
    """Summarize observed transcript content; every outcome remains an unverified claim."""
    facts = extract(events)
    users = [event for event in events if event.kind == USER]
    replies = [event for event in events if event.kind == ASSISTANT and event.text.strip()]
    return {
        "schema": SCHEMA,
        "source": source,
        "id": thread_id,
        "node": node,
        "title": title,
        "goal": users[0].text[:1200] if users else None,
        "goal_status": "first_human_message" if users else "no_stored_prompt",
        "activity": {"first": facts["first_ts"], "last": facts["last_ts"],
                     "active_hours": facts["active_hours"], "counts": facts["counts"]},
        "work": {"tools": facts["tools_top"], "files": facts["files_touched_top"][:15]},
        "claims": {"pull_requests_mentioned": facts["prs"],
                   "kanban_cards_mentioned": facts["kanban_ids"],
                   "commits_mentioned": facts["shas"],
                   "last_reported_outcome": replies[-1].text[:1500] if replies else None,
                   "verification": "not_verified: transcript statements are claims; check live evidence"},
        "continue": continue_command(source, thread_id),
    }


def render_markdown(card: dict) -> str:
    activity = card["activity"]
    claims = card["claims"]
    goal = card["goal"] or "(No stored human prompt.)"
    lines = [f"# Work card — {card['title'] or card['source'] + ':' + card['id']}",
             f"`{card['source']}:{card['id']}` · {card['node']} · "
             f"{fmt_ts(activity['first'])} → {fmt_ts(activity['last'])}", "",
             "## Goal", goal, "", "## Transcript claims (not verified)",
             claims["last_reported_outcome"] or "(No assistant reply stored.)",
             "", "Verification: " + claims["verification"]]
    if card.get("input", {}).get("status") == "partial":
        lines.extend(["", "Input is partial: " + "; ".join(error["error"] for error in card["input"]["errors"])])
    if card["continue"]:
        lines.extend(["", "## Continue", f"`{card['continue']}`"])
    return "\n".join(lines)
