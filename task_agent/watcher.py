"""Folder watcher with a worker pool.

Polls the inbox, waits until a task folder has stopped changing (so we never
read a half-copied attachment), moves it to `processed/`, and hands it to the
pipeline on a thread pool.

Emails in the same thread are processed in order on one worker, so a follow-up
always sees the memory written by the message before it. Different threads run
in parallel.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from collections import defaultdict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from .inbox import InvalidTask, list_task_folders
from .pipeline import Pipeline

log = logging.getLogger("task_agent")


def _latest_mtime(folder: Path) -> float:
    return max((p.stat().st_mtime for p in folder.rglob("*")), default=folder.stat().st_mtime)


def _thread_key(folder: Path) -> tuple[str, str]:
    try:
        meta = json.loads((folder / "email.json").read_text(encoding="utf-8"))
        return meta.get("thread_id") or folder.name, meta.get("received_at", "")
    except (OSError, json.JSONDecodeError):
        return folder.name, ""


class Watcher:
    def __init__(self, pipeline: Pipeline):
        self.pipeline = pipeline
        self.settings = pipeline.settings
        self.pool = ThreadPoolExecutor(max_workers=self.settings.max_workers,
                                       thread_name_prefix="task-worker")

    def ready_folders(self) -> list[Path]:
        known = self.pipeline.db.known_ids()
        cutoff = time.time() - self.settings.settle_seconds
        return [f for f in list_task_folders(self.settings.inbox_dir)
                if f.name not in known and _latest_mtime(f) <= cutoff]

    def scan_once(self) -> list[Future]:
        """Claim every settled folder and submit it. Returns the futures."""
        processed = self.settings.inbox_dir / "processed"
        processed.mkdir(parents=True, exist_ok=True)

        groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)
        for folder in self.ready_folders():
            thread_id, received = _thread_key(folder)
            target = processed / folder.name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(folder), target)
            groups[thread_id].append((received, target))

        return [self.pool.submit(self._run_thread, [p for _, p in sorted(items)])
                for items in groups.values()]

    def _run_thread(self, folders: list[Path]) -> list[str]:
        statuses = []
        for folder in folders:
            try:
                statuses.append(self.pipeline.handle(folder))
            except InvalidTask as exc:
                log.error("skipping invalid task: %s", exc)
                statuses.append("invalid")
            except Exception:  # never let one bad task kill the worker
                log.exception("unexpected error on %s", folder.name)
                statuses.append("error")
        return statuses

    def process_all(self) -> list[str]:
        """Process everything currently in the inbox and wait for it to finish."""
        results: list[str] = []
        for fut in self.scan_once():
            results.extend(fut.result())
        return results

    def run_forever(self, poll_seconds: float = 2.0) -> None:
        log.info("watching %s with %d workers", self.settings.inbox_dir, self.settings.max_workers)
        try:
            while True:
                self.scan_once()
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            log.info("stopping")
        finally:
            self.pool.shutdown(wait=True)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=True)
