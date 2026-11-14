"""Small FastAPI service for humans: see tasks, approve or reject held ones, read the audit log.

Run:  uvicorn --factory task_agent.api:create_app --reload
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .db import Database
from .llm import get_provider
from .pipeline import Pipeline


class Decision(BaseModel):
    approver: str = Field(min_length=1, examples=["ops.manager@bluebird.example.com"])
    reason: str = ""


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    if pipeline is None:
        settings = Settings.from_env()
        pipeline = Pipeline(settings, get_provider(settings.llm_provider, settings.llm_model),
                            Database(settings.db_path))

    app = FastAPI(title="AI Task Automation Agent", version="0.1.0")
    app.state.pipeline = pipeline
    db = pipeline.db

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "llm_provider": pipeline.llm.name}

    @app.get("/tasks")
    def list_tasks(status: str | None = None) -> list[dict]:
        return db.list_tasks(status)

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = db.get_task(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return {**task, "audit": db.audit_log(task_id)}

    def _decide(task_id: str, action) -> dict:
        try:
            status = action()
        except KeyError:
            raise HTTPException(404, "task not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"id": task_id, "status": status}

    @app.post("/tasks/{task_id}/approve")
    def approve(task_id: str, decision: Decision) -> dict:
        return _decide(task_id, lambda: pipeline.approve(task_id, decision.approver))

    @app.post("/tasks/{task_id}/reject")
    def reject(task_id: str, decision: Decision) -> dict:
        return _decide(task_id, lambda: pipeline.reject(task_id, decision.approver, decision.reason))

    @app.get("/audit")
    def audit(task_id: str | None = None) -> list[dict]:
        return db.audit_log(task_id)

    return app
