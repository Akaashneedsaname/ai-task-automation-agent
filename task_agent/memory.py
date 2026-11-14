"""Per-thread memory (`memory.md`).

Problem this solves: a follow-up email ("can you also break that down by
product?") used to be treated as a brand-new task. The agent had no idea what
"that" was, the sender had to re-attach files, and the whole job re-ran from
scratch.

Fix: after every task, the pipeline writes a short `memory.md` for the email
thread (what was asked, what was found, which files were used) and keeps a copy
of the attachments. A follow-up in the same thread loads that memory into the
prompt and reuses the stored files, so it only does the new part of the work.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path


class ThreadMemory:
    def __init__(self, root: Path):
        self.root = root

    def _dir(self, thread_id: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", thread_id)
        return self.root / "threads" / safe

    def load(self, thread_id: str) -> str:
        path = self._dir(thread_id) / "memory.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def attachments(self, thread_id: str) -> list[Path]:
        folder = self._dir(thread_id) / "files"
        return sorted(p for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []

    def remember(self, thread_id: str, *, task_id: str, intent: str, request: str,
                 findings: str, files: list[Path]) -> Path:
        folder = self._dir(thread_id)
        (folder / "files").mkdir(parents=True, exist_ok=True)
        for f in files:
            target = folder / "files" / f.name
            if f.resolve() != target.resolve():
                shutil.copy2(f, target)

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = (
            f"## {task_id} ({stamp})\n"
            f"intent: {intent}\n"
            f"request: {request.strip()[:300]}\n"
            f"files: {', '.join(p.name for p in self.attachments(thread_id)) or 'none'}\n"
            f"findings:\n{findings.strip()[:1500]}\n\n"
        )
        path = folder / "memory.md"
        header = "" if path.exists() else f"# Thread memory: {thread_id}\n\n"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(header + entry)
        return path
