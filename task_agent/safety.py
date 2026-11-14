"""Deterministic safety gate.

The LLM proposes; plain code decides. These rules run on every task after
classification and cannot be talked out of by the email or the model:

* destructive statements (DROP, TRUNCATE, DELETE without WHERE) are refused;
* anything with side effects, long runtimes, low confidence, or a side effect
  requested by an outside sender waits for a human to approve it.
"""

from __future__ import annotations

import re

from .models import Classification, EmailTask, GateDecision

RISKY_ACTIONS = {
    "update_records",
    "delete_records",
    "send_external_email",
    "send_calendar_invite",
    "make_payment",
}

_PROHIBITED = [
    (re.compile(r"\bdrop\s+(table|schema|database)\b", re.I), "DROP statement"),
    (re.compile(r"\btruncate\b", re.I), "TRUNCATE statement"),
    (re.compile(r"\bdelete\s+from\s+\w+\s*(;|$)", re.I | re.M), "DELETE without WHERE"),
    (re.compile(r"\bdelete\s+(all|every)\b", re.I), "bulk delete request"),
]

MIN_CONFIDENCE = 0.6


def find_prohibited(text: str) -> list[str]:
    return [label for pattern, label in _PROHIBITED if pattern.search(text)]


def sender_domain(sender: str) -> str:
    return sender.rsplit("@", 1)[-1].strip().lower() if "@" in sender else ""


def evaluate(
    task: EmailTask,
    c: Classification,
    *,
    minutes_threshold: int,
    internal_domains: set[str],
) -> GateDecision:
    prohibited = find_prohibited(task.text)
    if prohibited:
        return GateDecision("refused", [f"prohibited operation: {p}" for p in prohibited])

    reasons: list[str] = []
    risky = sorted(set(c.actions) & RISKY_ACTIONS)
    if risky:
        reasons.append(f"side effects need sign-off: {', '.join(risky)}")
    if c.estimated_minutes > minutes_threshold:
        reasons.append(f"estimated {c.estimated_minutes} min > {minutes_threshold} min limit")
    if c.confidence < MIN_CONFIDENCE:
        reasons.append(f"low classification confidence ({c.confidence:.2f})")
    if c.actions and sender_domain(task.sender) not in internal_domains:
        reasons.append("external sender requested a side effect")

    return GateDecision("needs_approval", reasons) if reasons else GateDecision("proceed")
