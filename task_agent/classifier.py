"""Turn an email into a structured Classification using the LLM.

The model is asked for strict JSON. Its output is never trusted blindly: it is
parsed, validated and clamped here, and anything malformed raises
`ClassificationError` so the pipeline can retry or fall back.
"""

from __future__ import annotations

import json
import re

from .llm import LLMProvider
from .models import Classification, EmailTask

KNOWN_INTENTS = {
    "sales_summary",
    "invoice_extraction",
    "inventory_check",
    "data_update",
    "draft_reply",
    "schedule_meeting",
    "general_question",
}

PROMPT = """You are the intake step of an operations assistant. Read the email and
reply with ONLY a JSON object, no markdown, using exactly these keys:

{{"intent": one of {intents},
 "summary": "<one sentence>",
 "plan": ["<step>", ...],
 "estimated_minutes": <integer>,
 "actions": [side effects the task needs, from: "update_records", "delete_records",
             "send_external_email", "send_calendar_invite", "make_payment"],
 "confidence": <0.0-1.0>}}

If the request is unclear, use "general_question" with low confidence.
When unsure about time, overestimate.

{memory}EMAIL:
From: {sender}
{text}
Attachments: {attachments}
"""


class ClassificationError(ValueError):
    pass


def build_prompt(task: EmailTask, memory: str = "") -> str:
    memory_block = f"PREVIOUS CONTEXT FOR THIS THREAD:\n{memory}\n\n" if memory else ""
    return PROMPT.format(
        intents=sorted(KNOWN_INTENTS),
        memory=memory_block,
        sender=task.sender,
        text=task.text,
        attachments=", ".join(p.name for p in task.attachments) or "none",
    )


def parse_classification(raw: str) -> Classification:
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        raise ClassificationError("no JSON object in model output")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise ClassificationError(f"invalid JSON: {exc}") from exc

    intent = str(data.get("intent", "")).strip()
    if intent not in KNOWN_INTENTS:
        raise ClassificationError(f"unknown intent {intent!r}")
    try:
        minutes = int(data.get("estimated_minutes", 0))
        confidence = float(data.get("confidence", 0.0))
    except (TypeError, ValueError) as exc:
        raise ClassificationError(f"bad numeric field: {exc}") from exc

    plan = data.get("plan") or []
    actions = data.get("actions") or []
    if not isinstance(plan, list) or not isinstance(actions, list):
        raise ClassificationError("plan and actions must be lists")

    return Classification(
        intent=intent,
        summary=str(data.get("summary", "")).strip(),
        plan=[str(p) for p in plan],
        estimated_minutes=max(minutes, 0),
        actions=[str(a) for a in actions],
        confidence=min(max(confidence, 0.0), 1.0),
        raw=raw,
    )


def classify(provider: LLMProvider, task: EmailTask, memory: str = "") -> Classification:
    return parse_classification(provider.complete(build_prompt(task, memory), purpose="classify"))


def fallback_classification(reason: str) -> Classification:
    """Used when the model keeps failing: park the task for a human."""
    return Classification(
        intent="general_question",
        summary=f"Could not classify automatically ({reason}).",
        plan=["Human review required"],
        estimated_minutes=0,
        actions=[],
        confidence=0.0,
    )
