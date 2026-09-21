from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import tools.extract_ground_truth_labels as labels


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_name(index: int) -> str:
    result = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _write_xlsx(path: Path, rows: list[list[str]]) -> None:
    ET.register_namespace("", MAIN_NS)
    ET.register_namespace("r", REL_NS)
    worksheet = ET.Element(f"{{{MAIN_NS}}}worksheet")
    sheet_data = ET.SubElement(worksheet, f"{{{MAIN_NS}}}sheetData")
    for row_index, values in enumerate(rows, start=1):
        row = ET.SubElement(sheet_data, f"{{{MAIN_NS}}}row", {"r": str(row_index)})
        for column_index, value in enumerate(values):
            if value == "":
                continue
            cell = ET.SubElement(
                row,
                f"{{{MAIN_NS}}}c",
                {"r": f"{_column_name(column_index)}{row_index}", "t": "inlineStr"},
            )
            inline = ET.SubElement(cell, f"{{{MAIN_NS}}}is")
            text = ET.SubElement(inline, f"{{{MAIN_NS}}}t")
            text.text = value

    workbook = ET.Element(f"{{{MAIN_NS}}}workbook")
    sheets = ET.SubElement(workbook, f"{{{MAIN_NS}}}sheets")
    ET.SubElement(
        sheets,
        f"{{{MAIN_NS}}}sheet",
        {"name": "标签", "sheetId": "1", f"{{{REL_NS}}}id": "rId1"},
    )
    relationships = ET.Element(f"{{{PKG_REL_NS}}}Relationships")
    ET.SubElement(
        relationships,
        f"{{{PKG_REL_NS}}}Relationship",
        {
            "Id": "rId1",
            "Type": f"{REL_NS}/worksheet",
            "Target": "worksheets/sheet1.xml",
        },
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", ET.tostring(workbook, encoding="utf-8"))
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            ET.tostring(relationships, encoding="utf-8"),
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            ET.tostring(worksheet, encoding="utf-8"),
        )


def _fixture_rows(unknown_label: bool = False) -> list[list[str]]:
    return [
        ["", "", "", "", "传输部1", "核心网1"],
        ["", "稽核文件", "检查项", "判定逻辑", "系统甲", "系统乙"],
        ["", "2.1 定级报告", "DJ-01 第一项", "逻辑", "符合，完整", "不符合，缺失"],
        ["", "", "DJ-06 已停用项", "逻辑", "符合", "符合"],
        ["", "2.5 符合性评测表", "FHX-05 第二项", "逻辑", "不适用，一级系统", "符合"],
        [
            "",
            "2.6 证明文件",
            "ZM-01 第三项",
            "逻辑",
            "待确认" if unknown_label else "无法检测，无法判断",
            "是，存在证据",
        ],
    ]


class GroundTruthLabelExportTests(unittest.TestCase):
    def _root(self, directory: Path) -> Path:
        root = directory / "网管中心"
        (root / "传输部" / "系统甲-3").mkdir(parents=True)
        (root / "核心网部" / "系统乙-2025-3").mkdir(parents=True)
        return root

    def test_exports_each_department_and_excludes_dj_06(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            workbook = directory / "labels.xlsx"
            _write_xlsx(workbook, _fixture_rows())
            root = self._root(directory)

            results = labels.export_ground_truth_labels(workbook, root)

            self.assertEqual(len(results), 2)
            transport = json.loads(
                (root / "传输部label" / "ground_truth.v1.json").read_text(
                    encoding="utf-8"
                )
            )
            core = json.loads(
                (root / "核心网部label" / "ground_truth.v1.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(transport["source_name"], "传输部")
            self.assertEqual(transport["scope"], "传输部1")
            self.assertEqual(
                [item["item_id"] for item in transport["items"]],
                ["DJ-01", "FHX-05", "ZM-01"],
            )
            self.assertEqual(
                transport["systems"]["系统甲-3"],
                {
                    "DJ-01": "passed",
                    "FHX-05": "not_applicable",
                    "ZM-01": "failed",
                },
            )
            self.assertEqual(
                core["systems"]["系统乙-2025-3"],
                {"DJ-01": "failed", "FHX-05": "passed", "ZM-01": "passed"},
            )

    def test_existing_json_requires_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            workbook = directory / "labels.xlsx"
            _write_xlsx(workbook, _fixture_rows())
            root = self._root(directory)
            labels.export_ground_truth_labels(workbook, root)

            with self.assertRaises(FileExistsError):
                labels.export_ground_truth_labels(workbook, root)

            results = labels.export_ground_truth_labels(workbook, root, overwrite=True)
            self.assertEqual(len(results), 2)

    def test_unknown_label_stops_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            workbook = directory / "labels.xlsx"
            _write_xlsx(workbook, _fixture_rows(unknown_label=True))
            root = self._root(directory)

            with self.assertRaisesRegex(labels.LabelExtractionError, "待确认"):
                labels.export_ground_truth_labels(workbook, root)
            self.assertFalse((root / "传输部label").exists())
            self.assertFalse((root / "核心网部label").exists())

    def test_classification_matches_previous_json_convention(self) -> None:
        self.assertEqual(labels.classify_label("一致，全部匹配"), "passed")
        self.assertEqual(labels.classify_label("是\n附录存在"), "passed")
        self.assertEqual(labels.classify_label("不齐全（缺少网络结构）"), "failed")
        self.assertEqual(labels.classify_label("未识别到，报警"), "failed")
        self.assertEqual(labels.classify_label("无法检测，依赖历史报告"), "failed")
        self.assertEqual(labels.classify_label("不适用，该系统为一级"), "not_applicable")

    def test_exact_name_distinguishes_expansion_sample_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            department = Path(temporary) / "扩充测评集"
            expected = department / "某系统-3-01"
            expected.mkdir(parents=True)
            (department / "某系统-3-02").mkdir()
            (department / "某系统-3-03").mkdir()

            resolved = labels._resolve_system(department, "某系统-3-01")

            self.assertEqual(resolved, expected)

    def test_legacy_match_still_accepts_directory_level_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            department = Path(temporary) / "传输部"
            expected = department / "某系统-3"
            expected.mkdir(parents=True)

            resolved = labels._resolve_system(department, "某系统")

            self.assertEqual(resolved, expected)


if __name__ == "__main__":
    unittest.main()
