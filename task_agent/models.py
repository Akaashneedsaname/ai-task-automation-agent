"""Plain data objects passed between pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EmailTask:
    """One incoming request: an email plus any attachments."""

    task_id: str
    sender: str
    subject: str
    body: str
    thread_id: str
    received_at: str
    attachments: list[Path] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}"


@dataclass
class Classification:
    """What the LLM thinks the email is asking for."""

    intent: str                      # e.g. sales_summary, draft_reply, data_update
    summary: str
    plan: list[str]
    estimated_minutes: int
    actions: list[str]               # side effects the task would cause, e.g. "update_records"
    confidence: float = 0.0
    raw: str = ""                    # the raw model output, kept for the audit trail


@dataclass
class GateDecision:
    """Result of the deterministic safety check that runs after classification."""

    status: str                      # "proceed" | "needs_approval" | "refused"
    reasons: list[str] = field(default_factory=list)


@dataclass
class TaskResult:
    status: str                      # "completed" | "failed"
    response_md: str
    artifacts: dict[str, str] = field(default_factory=dict)   # filename -> content
    memory_md: str = ""
