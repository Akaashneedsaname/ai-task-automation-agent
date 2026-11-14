"""Runtime settings, read from environment variables (optionally via a .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny .env loader so the project has no extra dependency for it.

    Existing environment variables always win over values in the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class Settings:
    inbox_dir: Path = Path("runtime/inbox")
    output_dir: Path = Path("runtime/output")
    db_path: Path = Path("runtime/tasks.db")
    llm_provider: str = "mock"          # mock | anthropic | openai
    llm_model: str = ""                 # empty = provider default
    max_workers: int = 4
    settle_seconds: float = 2.0         # wait for a task folder to finish copying
    approval_minutes_threshold: int = 30
    max_retries: int = 2
    internal_domains: set[str] = field(default_factory=lambda: {"bluebird.example.com"})

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        env = os.environ
        return cls(
            inbox_dir=Path(env.get("INBOX_DIR", "runtime/inbox")),
            output_dir=Path(env.get("OUTPUT_DIR", "runtime/output")),
            db_path=Path(env.get("DB_PATH", "runtime/tasks.db")),
            llm_provider=env.get("LLM_PROVIDER", "mock").lower(),
            llm_model=env.get("LLM_MODEL", ""),
            max_workers=int(env.get("MAX_WORKERS", "4")),
            settle_seconds=float(env.get("SETTLE_SECONDS", "2")),
            approval_minutes_threshold=int(env.get("APPROVAL_MINUTES_THRESHOLD", "30")),
            max_retries=int(env.get("MAX_RETRIES", "2")),
            internal_domains={
                d.strip().lower()
                for d in env.get("INTERNAL_DOMAINS", "bluebird.example.com").split(",")
                if d.strip()
            },
        )
