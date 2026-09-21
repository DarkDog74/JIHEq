# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path


POLICY_PATH = Path(__file__).resolve().parents[1] / "pdf_backend_policy.json"


def backend_for(category: str) -> str:
    if not POLICY_PATH.is_file():
        return "light"
    try:
        policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return "light"
    value = str(policy.get(category, "light")).lower()
    return value if value in {"light", "mineru"} else "light"

