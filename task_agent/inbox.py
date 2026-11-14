"""Reading email tasks from disk.

Each task is a folder:

    <task_id>/
        email.json          {"from", "subject", "body", "thread_id", "received_at"}
        attachments/        optional files (CSV, TXT, ...)

In production this folder is written by an email integration; here the sample
inbox in `sample_inbox/` plays that role.
"""

from __future__ import annotations

import json
from pathlib import Path

from .models import EmailTask


class InvalidTask(ValueError):
    pass


REQUIRED = ("from", "subject", "body")


def load_task(folder: Path) -> EmailTask:
    meta_path = folder / "email.json"
    if not meta_path.exists():
        raise InvalidTask(f"{folder.name}: missing email.json")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidTask(f"{folder.name}: email.json is not valid JSON ({exc})") from exc
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise InvalidTask(f"{folder.name}: missing fields {missing}")

    att_dir = folder / "attachments"
    attachments = sorted(p for p in att_dir.iterdir() if p.is_file()) if att_dir.is_dir() else []
    return EmailTask(
        task_id=folder.name,
        sender=meta["from"],
        subject=meta["subject"],
        body=meta["body"],
        thread_id=meta.get("thread_id") or folder.name,
        received_at=meta.get("received_at", ""),
        attachments=attachments,
    )


def list_task_folders(inbox: Path) -> list[Path]:
    if not inbox.is_dir():
        return []
    return sorted(
        p for p in inbox.iterdir() if p.is_dir() and p.name != "processed" and not p.name.startswith(".")
    )
