"""Command line entry point.

    python -m task_agent watch            # watch INBOX_DIR forever
    python -m task_agent once             # process what's in the inbox now, then exit
    python -m task_agent approve <id> <approver>
    python -m task_agent reject  <id> <approver> [reason]
"""

from __future__ import annotations

import logging
import sys

from .config import Settings
from .db import Database
from .llm import get_provider
from .pipeline import Pipeline
from .watcher import Watcher


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(threadName)s %(message)s")
    if not argv:
        print(__doc__)
        return 1
    settings = Settings.from_env()
    pipeline = Pipeline(settings, get_provider(settings.llm_provider, settings.llm_model),
                        Database(settings.db_path))
    cmd, args = argv[0], argv[1:]
    if cmd == "watch":
        Watcher(pipeline).run_forever()
    elif cmd == "once":
        w = Watcher(pipeline)
        print(w.process_all())
        w.shutdown()
    elif cmd == "approve" and len(args) >= 2:
        print(pipeline.approve(args[0], args[1]))
    elif cmd == "reject" and len(args) >= 2:
        print(pipeline.reject(args[0], args[1], " ".join(args[2:])))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
