from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import STATE_ROOT


def event_path(job_id: str) -> Path:
    return STATE_ROOT / "jobs" / job_id / "events.jsonl"


def append_event(job_id: str, payload: dict[str, object]) -> dict[str, object]:
    target = event_path(job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        **payload,
    }
    with target.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        stream.flush()
    return event
