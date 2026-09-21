from __future__ import annotations

import re
import unittest
from pathlib import Path

from audit_rules import list_rule_items
from extractors import list_extractor_items


_ITEM_MODULE_RE = re.compile(r"^[a-z]+_\d{2}\.py$")


def _implemented_items(directory: Path) -> set[str]:
    return {
        path.stem.upper().replace("_", "-")
        for path in directory.iterdir()
        if _ITEM_MODULE_RE.fullmatch(path.name)
    }


class RegistryCompletenessTests(unittest.TestCase):
    def test_every_extractor_module_is_registered(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            _implemented_items(project_root / "extractors"),
            set(list_extractor_items()),
        )

    def test_every_rule_module_is_registered(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        self.assertEqual(
            _implemented_items(project_root / "audit_rules"),
            set(list_rule_items()),
        )
