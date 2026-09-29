"""Turn an event stream into a compact, token-budgeted transcript.

Modes (cheapest first):
  brief  - your messages + each turn's final reply + runtime summaries. The storyline.
  convo  - + all reply text, thinking headlines, one line per tool call. Default.
  full   - + tool results (head/tail trimmed). Closest to the raw chat.
A --budget (est. tokens) degrades gracefully: tool results -> thinking -> tool calls -> middle of
long messages, always keeping every human message and the last reply.
"""
from __future__ import annotations

from collections import Counter

from .model import (ASSISTANT, META, SUMMARY, THINKING, TOOL_CALL, TOOL_RESULT, TURN, USER, Event,
                    est_tokens, fmt_ts)

LABEL = {USER: "YOU", ASSISTANT: "AI", THINKING: "think", TOOL_CALL: "run", TOOL_RESULT: "out",
         SUMMARY: "SUMMARY", TURN: "--", META: "meta"}


def _trim(text: str, max_chars: int) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    head = int(max_chars * 0.7)
    tail = max_chars - head
    return f"{text[:head]}\n  …[{len(text) - max_chars:,} chars cut]…\n{text[-tail:]}"


def _one_line(text: str, n: int) -> str:
    s = " ".join(text.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def stats(evts: list[Event]) -> dict:
    by = Counter()
    tok = Counter()
    for e in evts:
        by[e.kind] += 1
        tok[e.kind] += e.tokens
    return {"events": dict(by), "est_tokens": dict(tok), "est_tokens_total": sum(tok.values())}


def _mark_finals(evts: list[Event]) -> set[int]:
    """Index of the last assistant message before each human message / turn end / end of chat."""
    finals, last_ai = set(), None
    for i, e in enumerate(evts):
        if e.kind == ASSISTANT:
            last_ai = i
        elif (e.kind == USER or (e.kind == TURN and "started" not in e.text)) and last_ai is not None:
            finals.add(last_ai)
            last_ai = None
    if last_ai is not None:
        finals.add(last_ai)
    return finals


def render(evts: list[Event], mode: str = "convo", budget: int | None = None,
           result_chars: int = 600, msg_chars: int = 4000, show_ts: bool = True) -> str:
    evts = list(evts)
    finals = _mark_finals(evts)
    plan = {
        "brief": dict(results=False, thinking=False, calls=False, commentary=False),
        "convo": dict(results=False, thinking=True, calls=True, commentary=True),
        "full": dict(results=True, thinking=True, calls=True, commentary=True),
    }[mode].copy()

    def build(p: dict, mchars: int, rchars: int) -> str:
        out, last_call, repeat = [], None, 0
        for i, e in enumerate(evts):
            k = e.kind
            if k == TOOL_RESULT and not p["results"]:
                continue
            if k == THINKING and not p["thinking"]:
                continue
            if k == TOOL_CALL and not p["calls"]:
                continue
            if k == ASSISTANT and not p["commentary"] and i not in finals:
                continue
            if k == META and mode == "brief":
                continue
            if k == TURN and "started" in e.text:
                continue
            ts = f"[{fmt_ts(e.ts)}] " if show_ts else ""
            if k != TOOL_CALL and k != TOOL_RESULT:
                # A human/AI/turn event between two identical calls means they are separate runs.
                if repeat:
                    out.append(f"      (same call repeated {repeat}x)")
                last_call, repeat = None, 0
            if k == TOOL_CALL:
                sig = (e.name, e.text[:200])
                if sig == last_call:
                    repeat += 1
                    continue
                if repeat:
                    out.append(f"      (same call repeated {repeat}x)")
                last_call, repeat = sig, 0
                out.append(f"{ts}run {e.name}: {_one_line(e.text, 220)}")
            elif k == TOOL_RESULT:
                body = _trim(e.text, rchars).replace("\n", "\n      ")
                out.append(f"      out: {body}")
            elif k == THINKING:
                out.append(f"{ts}think: {_one_line(e.text, 300)}")
            elif k == TURN:
                out.append(f"{ts}-- {e.text}")
            else:
                limit = mchars if k != USER else max(mchars, 8000)
                out.append(f"{ts}{LABEL[k]}: {_trim(e.text, limit)}")
        if repeat:
            out.append(f"      (same call repeated {repeat}x)")
        return "\n".join(out)

    text = build(plan, msg_chars, result_chars)
    if not budget or est_tokens(text) <= budget:
        return text
    # Degrade in order of least value per token.
    ladder = [
        ("results", False, msg_chars, result_chars),
        ("thinking", False, msg_chars, result_chars),
        ("calls", False, msg_chars, result_chars),
        ("commentary", False, msg_chars, result_chars),
        (None, None, 1500, 300),
        (None, None, 600, 200),
        (None, None, 250, 100),
    ]
    for key, val, mc, rc in ladder:
        if key:
            plan[key] = val
        text = build(plan, mc, rc)
        if est_tokens(text) <= budget:
            return text + f"\n\n[chatlens: compressed to fit budget {budget:,} est. tokens]"
    # Still too big: every human message survives (shortened evenly), plus the last reply.
    users = [e for e in evts if e.kind == USER]
    last_ai = next((e for e in reversed(evts) if e.kind == ASSISTANT), None)
    per = max(80, (budget * 4) // max(1, len(users) + 1))
    out = [f"[{fmt_ts(e.ts)}] YOU: {_one_line(e.text, per)}" if show_ts else f"YOU: {_one_line(e.text, per)}"
           for e in users]
    if last_ai:
        out.append(f"AI (last): {_one_line(last_ai.text, per)}")
    text = "\n".join(out)
    note = f"budget {budget:,}" if est_tokens(text) <= budget else f"budget {budget:,} EXCEEDED: human messages are never dropped"
    return text + f"\n\n[chatlens: reduced to human messages + last reply ({note})]"
