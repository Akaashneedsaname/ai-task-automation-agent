"""One-command demo: python -m demo

Copies the synthetic sample inbox into runtime/demo/, processes it with the
offline mock LLM, plays the human approver for two held tasks, and prints what
happened. Outputs are left in runtime/demo/output/ to browse afterwards.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from task_agent.config import Settings
from task_agent.db import Database
from task_agent.llm import MockProvider
from task_agent.pipeline import Pipeline
from task_agent.watcher import Watcher

ROOT = Path(__file__).resolve().parent.parent
RUN = ROOT / "runtime" / "demo"

ICON = {"completed": "OK  ", "awaiting_approval": "HOLD", "refused": "STOP",
        "rejected": "NO  ", "failed": "FAIL"}


def table(db: Database, title: str) -> None:
    print(f"\n{title}\n" + "-" * 96)
    print(f"{'':4}  {'task':<26} {'intent':<19} {'status':<18} note")
    for t in db.list_tasks():
        note = "; ".join(t["gate_reasons"]) if t["status"] in {"awaiting_approval", "refused"} else ""
        print(f"{ICON.get(t['status'], '    ')}  {t['id']:<26} {t['intent'] or '':<19} "
              f"{t['status']:<18} {note[:60]}")


def show(path: Path, lines: int = 12) -> None:
    text = path.read_text(encoding="utf-8").strip().splitlines()
    print("\n".join("    " + line for line in text[:lines]))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    if RUN.exists():
        shutil.rmtree(RUN)
    shutil.copytree(ROOT / "sample_inbox", RUN / "inbox")

    settings = Settings(inbox_dir=RUN / "inbox", output_dir=RUN / "output",
                        db_path=RUN / "tasks.db", settle_seconds=0, max_workers=4)
    db = Database(settings.db_path)
    pipeline = Pipeline(settings, MockProvider(), db, retry_delay=0)
    watcher = Watcher(pipeline)

    print("Processing 10 synthetic emails with 4 workers (mock LLM, no API key)...")
    watcher.process_all()
    watcher.shutdown()
    table(db, "After the first pass")

    print("\nA manager reviews the held tasks:")
    print("  approve 05_price_update   ->", pipeline.approve("05_price_update", "ops.manager@bluebird.example.com"))
    print("  reject  07_schedule_meeting ->", pipeline.reject(
        "07_schedule_meeting", "ops.manager@bluebird.example.com", "Already booked by phone"))
    table(db, "After approvals")

    out = settings.output_dir
    print("\nFollow-up email answered from thread memory (no re-attached file):")
    show(out / "02_followup_by_product" / "response.md")
    print("\nInvoice check caught a mismatch:")
    show(out / "03_vendor_invoice" / "response.md", 6)
    print("\nAudit trail for 05_price_update:")
    for a in db.audit_log("05_price_update"):
        print(f"    {a['event']:<18} {a['actor']:<34} {a['detail'][:50]}")
    print(f"\nAll outputs: {out.relative_to(ROOT)}")
    db.close()


if __name__ == "__main__":
    main()
