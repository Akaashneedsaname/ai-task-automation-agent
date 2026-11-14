"""The task lifecycle: classify -> gate -> (approve) -> execute -> record.

    received -> classified -> refused
                           -> awaiting_approval -> approved -> running -> completed | failed
                                                -> rejected
                           -> running -> completed | failed
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import classifier, safety
from .config import Settings
from .db import Database
from .executors import EXECUTORS, Context, ExecutionError
from .inbox import load_task
from .llm import LLMProvider
from .memory import ThreadMemory
from .models import Classification, EmailTask, TaskResult

log = logging.getLogger("task_agent")


class Pipeline:
    def __init__(self, settings: Settings, llm: LLMProvider, db: Database,
                 retry_delay: float = 0.5):
        self.settings = settings
        self.llm = llm
        self.db = db
        self.memory = ThreadMemory(settings.output_dir)
        self.retry_delay = retry_delay

    # -- entry points -------------------------------------------------------
    def handle(self, folder: Path) -> str:
        """Process one task folder end to end (or until it needs a human)."""
        task = load_task(folder)
        self.db.insert_task(task_id=task.task_id, thread_id=task.thread_id, sender=task.sender,
                            subject=task.subject, source_path=folder)
        self.db.audit(task.task_id, "received", f"from {task.sender}: {task.subject}")

        memory = self.memory.load(task.thread_id)
        if memory:
            self.db.audit(task.task_id, "memory_loaded", f"thread {task.thread_id}")
        c = self._classify(task, memory)
        self.db.update_task(task.task_id, status="classified", intent=c.intent, summary=c.summary,
                            estimated_minutes=c.estimated_minutes)
        self.db.audit(task.task_id, "classified",
                      f"intent={c.intent} est={c.estimated_minutes}m conf={c.confidence:.2f}")

        gate = safety.evaluate(task, c, minutes_threshold=self.settings.approval_minutes_threshold,
                               internal_domains=self.settings.internal_domains)
        if gate.status == "refused":
            self.db.update_task(task.task_id, status="refused", gate_reasons=gate.reasons)
            self.db.audit(task.task_id, "refused", "; ".join(gate.reasons))
            self._write(task.task_id, "# Request refused\n\n" + "\n".join(
                f"- {r}" for r in gate.reasons) + "\n\nNo action was taken.\n", {})
            return "refused"
        if gate.status == "needs_approval":
            self.db.update_task(task.task_id, status="awaiting_approval", gate_reasons=gate.reasons)
            self.db.audit(task.task_id, "held_for_approval", "; ".join(gate.reasons))
            plan = "\n".join(f"{i}. {s}" for i, s in enumerate(c.plan, 1))
            self._write(task.task_id, f"# Waiting for approval\n\n**Why:** {'; '.join(gate.reasons)}"
                        f"\n\n**Proposed plan ({c.estimated_minutes} min est.):**\n{plan}\n", {})
            return "awaiting_approval"
        return self._execute(task, c, memory)

    def approve(self, task_id: str, approver: str) -> str:
        row = self._require(task_id, "awaiting_approval")
        self.db.update_task(task_id, status="approved")
        self.db.audit(task_id, "approved", "", actor=approver)
        task = load_task(Path(row["source_path"]))
        c = Classification(intent=row["intent"], summary=row["summary"] or "", plan=[],
                           estimated_minutes=row["estimated_minutes"] or 0, actions=[], confidence=1.0)
        return self._execute(task, c, self.memory.load(task.thread_id))

    def reject(self, task_id: str, approver: str, reason: str = "") -> str:
        self._require(task_id, "awaiting_approval")
        self.db.update_task(task_id, status="rejected")
        self.db.audit(task_id, "rejected", reason, actor=approver)
        self._write(task_id, f"# Rejected by {approver}\n\n{reason or 'No reason given.'}\n", {})
        return "rejected"

    # -- internals ----------------------------------------------------------
    def _require(self, task_id: str, status: str) -> dict:
        row = self.db.get_task(task_id)
        if row is None:
            raise KeyError(task_id)
        if row["status"] != status:
            raise ValueError(f"task {task_id} is {row['status']}, not {status}")
        return row

    def _classify(self, task: EmailTask, memory: str) -> Classification:
        last_error = ""
        for attempt in range(1, self.settings.max_retries + 2):
            try:
                return classifier.classify(self.llm, task, memory)
            except Exception as exc:  # malformed JSON, provider/network error, ...
                last_error = str(exc)
                self.db.audit(task.task_id, "classify_retry", f"attempt {attempt}: {exc}")
                time.sleep(self.retry_delay * attempt)
        return classifier.fallback_classification(last_error)

    def _execute(self, task: EmailTask, c: Classification, memory: str) -> str:
        self.db.update_task(task.task_id, status="running")
        files = list(task.attachments) + [
            f for f in self.memory.attachments(task.thread_id)
            if f.name not in {a.name for a in task.attachments}
        ]
        ctx = Context(task=task, files=files, memory=memory, llm=self.llm)
        executor = EXECUTORS[c.intent]

        result: TaskResult | None = None
        error = ""
        for attempt in range(1, self.settings.max_retries + 2):
            self.db.increment_attempts(task.task_id)
            started = time.perf_counter()
            try:
                result = executor(ctx)
                self.db.audit(task.task_id, "executed",
                              f"{c.intent} in {time.perf_counter() - started:.2f}s")
                break
            except ExecutionError as exc:          # bad input: retrying won't help
                error = str(exc)
                break
            except Exception as exc:               # transient: back off and retry
                error = f"{type(exc).__name__}: {exc}"
                self.db.audit(task.task_id, "execute_retry", f"attempt {attempt}: {error}")
                time.sleep(self.retry_delay * attempt)

        if result is None:
            self.db.update_task(task.task_id, status="failed")
            self.db.audit(task.task_id, "failed", error)
            self._write(task.task_id, f"# Task failed\n\n{error}\n", {})
            return "failed"

        out_dir = self._write(task.task_id, result.response_md, result.artifacts, "completed")
        self.memory.remember(task.thread_id, task_id=task.task_id, intent=c.intent,
                             request=task.text, findings=result.memory_md, files=task.attachments)
        self.db.update_task(task.task_id, status="completed", output_dir=str(out_dir))
        self.db.audit(task.task_id, "completed", f"outputs in {out_dir.name}")
        return "completed"

    def _write(self, task_id: str, response_md: str, artifacts: dict[str, str],
               status: str | None = None) -> Path:
        out = self.settings.output_dir / task_id
        out.mkdir(parents=True, exist_ok=True)
        (out / "response.md").write_text(response_md, encoding="utf-8")
        for name, content in artifacts.items():
            (out / name).write_text(content, encoding="utf-8")
        row = self.db.get_task(task_id) or {}
        info = {k: row.get(k) for k in ("id", "thread_id", "status", "intent", "attempts")}
        if status:
            info["status"] = status
        info["written_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        info["llm_provider"] = self.llm.name
        (out / "run_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
        return out
