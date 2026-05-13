"""Heuristic triage for human ClickUp comments.

The router should not blindly convert every human comment into a fresh Kanban
execution task. This module keeps the first-pass decision deterministic and cheap:
simple questions can be answered directly, context/unblock notes update active
execution when one exists, and new scope becomes a new Kanban task.
"""
from __future__ import annotations

import re
from typing import Literal

CommentIntent = Literal["direct_reply", "append_to_active", "create_task"]

_CROSS_AGENT_HANDOFF_PATTERNS = (
    r"\bhandoff\b",
    r"\bunblock(?:ed|s|ing)?\b",
    r"\bblock(?:ed|er|ing)?\b",
    r"\bcomplete(?:d)?\b",
    r"\bdone\b",
    r"\bdecision\b",
    r"\bapproved?\b",
    r"\bcan proceed\b",
    r"\bproceed\b",
    r"\bnext owner\b",
    r"\bnext action\b",
    r"\bassigned to\b",
    r"\bdepends? on\b",
    r"\bno longer (?:blocked|needed|required)\b",
    r"\bnot (?:a )?blocker\b",
)

_NEW_SCOPE_PATTERNS = (
    r"\balso\b",
    r"\bbuild\b",
    r"\bcreate\b",
    r"\bimplement\b",
    r"\badd\b",
    r"\baudit\b",
    r"\brewrite\b",
    r"\bset up\b",
    r"\bspin up\b",
    r"\blaunch\b",
)

_CONTEXT_OR_UNBLOCK_PATTERNS = (
    r"\bapproved?\b",
    r"\bproceed\b",
    r"\bunblock(?:ed)?\b",
    r"\binstead\b",
    r"\bprefer\b",
    r"\bgo with\b",
    r"\buse\b",
    r"\btoken\b",
    r"\bstored\b",
    r"\bhere is\b",
    r"\bcontext\b",
)

_DIRECT_QUESTION_PATTERNS = (
    r"^\s*(what|why|where|when|who|is|are|does|do|did|should)\b",
    r"\?$",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def classify_comment_intent(comment_text: str, *, has_active_task: bool) -> CommentIntent:
    """Classify the operational side-effect for a human comment.

    Order matters:
    - New explicit scope creates a separate task even if something is active.
    - Context/unblock comments update active work when possible, otherwise create
      work because there is nothing active to update.
    - Simple questions/status asks are direct replies.
    - Ambiguous comments default to active-context update when active, otherwise a
      direct reply so the agent can clarify without creating phantom work.
    """
    text = " ".join((comment_text or "").strip().split())
    if not text:
        return "direct_reply"
    if _matches_any(text, _NEW_SCOPE_PATTERNS):
        return "create_task"
    if _matches_any(text, _CONTEXT_OR_UNBLOCK_PATTERNS):
        return "append_to_active" if has_active_task else "create_task"
    if _matches_any(text, _DIRECT_QUESTION_PATTERNS):
        return "direct_reply"
    return "append_to_active" if has_active_task else "direct_reply"


def is_meaningful_cross_agent_handoff(comment_text: str) -> bool:
    """Return True when an agent-authored comment should wake another agent.

    Agent-authored comments are normally loop-prevention noise. Cross-agent
    comments are allowed through only when they look like a handoff, unblock,
    blocker, completion, approval, or next-action signal. The receiving agent
    still decides whether to act after re-reading live ClickUp state.
    """
    text = " ".join((comment_text or "").strip().split())
    if not text:
        return False
    return _matches_any(text, _CROSS_AGENT_HANDOFF_PATTERNS)
