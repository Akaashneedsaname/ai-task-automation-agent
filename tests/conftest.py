import json
import shutil
from pathlib import Path

import pytest

from task_agent.config import Settings
from task_agent.db import Database
from task_agent.llm import MockProvider
from task_agent.pipeline import Pipeline

SAMPLES = Path(__file__).resolve().parent.parent / "sample_inbox"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(inbox_dir=tmp_path / "inbox", output_dir=tmp_path / "output",
                    db_path=tmp_path / "tasks.db", settle_seconds=0, max_workers=3)


@pytest.fixture
def pipeline(settings: Settings):
    db = Database(settings.db_path)
    yield Pipeline(settings, MockProvider(), db, retry_delay=0)
    db.close()


@pytest.fixture
def inbox(settings: Settings) -> Path:
    shutil.copytree(SAMPLES, settings.inbox_dir)
    return settings.inbox_dir


def make_task(root: Path, name: str, body: str, *, sender="a@bluebird.example.com",
              subject="test", thread_id=None, attachments: dict | None = None) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "email.json").write_text(json.dumps({
        "from": sender, "subject": subject, "body": body,
        "thread_id": thread_id or name, "received_at": "2026-11-09T09:00:00Z"}))
    if attachments:
        (folder / "attachments").mkdir()
        for fn, content in attachments.items():
            (folder / "attachments" / fn).write_text(content)
    return folder
