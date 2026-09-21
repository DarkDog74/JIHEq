from __future__ import annotations

import csv
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import tools.evaluate_batch_results as evaluation


SYSTEMS = (
    "系统A-3",
    "系统B-2025-3",
    "系统C-3",
    "系统D-3",
    "系统E-3",
)


def _classification(system: str, item_number: int) -> str:
    if item_number != 1:
        return "passed"
    return {
        "系统A-3": "passed",
        "系统B-2025-3": "failed",
        "系统C-3": "not_applicable",
        "系统D-3": "passed",
        "系统E-3": "failed",
    }[system]


def _ground_truth_payload(
    *,
    source_name: str = "核心网部",
    scope: str = "核心网5",
    systems: tuple[str, ...] = SYSTEMS,
    item_count: int = 56,
) -> dict[str, object]:
    items = []
    for number in range(1, item_count + 1):
        item_id = f"TST-{number:02d}"
        category_number = (number - 1) // 20 + 1
        items.append(
            {
                "item_id": item_id,
                "name": f"{item_id} 测试稽核项",
                "category": f"2.{category_number} 测试分类",
            }
        )
    return {
        "schema_version": 1,
        "kind": "audit_ground_truth",
        "source_name": source_name,
        "scope": scope,
        "items": items,
        "systems": {
            system: {
                f"TST-{number:02d}": _classification(system, number)
                for number in range(1, item_count + 1)
            }
            for system in systems
        },
    }


def _write_label_json(
    path: Path, payload: dict[str, object] | None = None
) -> None:
    _write_json(path, payload or _ground_truth_payload())


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class TruthLabelTests(unittest.TestCase):
    def test_local_json_has_five_systems_and_complete_56_item_matrix(self) -> None:
        truth = evaluation.load_ground_truth_json(evaluation.DEFAULT_LABEL_FILE)

        self.assertEqual(truth.source_name, "核心网部")
        self.assertEqual(truth.scope, "核心网5")
        self.assertEqual(len(truth.systems), 5)
        self.assertEqual(len(truth.item_ids), 56)
        self.assertEqual(sum(len(items) for items in truth.labels.values()), 280)
        counts = Counter(
            label.label
            for system_labels in truth.labels.values()
            for label in system_labels.values()
        )
        self.assertEqual(
            counts,
            Counter(
                {
                    evaluation.LABEL_PASSED: 159,
                    evaluation.LABEL_FAILED: 111,
                    evaluation.LABEL_NOT_APPLICABLE: 10,
                }
            ),
        )

    def test_json_v1_supports_other_departments_and_dynamic_system_count(self) -> None:
        systems = ("传输系统甲-3", "传输系统乙-2026-3")
        payload = {
            "schema_version": 1,
            "kind": "audit_ground_truth",
            "source_name": "传输部",
            "scope": "传输部2",
            "items": [
                {"item_id": "TST-01", "name": "第一项", "category": "2.1 测试"},
                {"item_id": "TST-02", "name": "第二项", "category": "2.1 测试"},
            ],
            "systems": {
                systems[0]: {"TST-01": "passed", "TST-02": "not_applicable"},
                systems[1]: {"TST-01": "failed", "TST-02": "passed"},
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
            _write_label_json(label_file, payload)

            truth = evaluation.load_ground_truth_json(label_file)

        self.assertEqual(truth.source_name, "传输部")
        self.assertEqual(truth.scope, "传输部2")
        self.assertEqual(truth.systems, systems)
        self.assertEqual(truth.item_ids, ("TST-01", "TST-02"))
        self.assertEqual(
            truth.labels[systems[0]]["TST-02"].label,
            evaluation.LABEL_NOT_APPLICABLE,
        )
        self.assertEqual(
            truth.labels[systems[0]]["TST-02"].classification,
            "not_applicable",
        )
        self.assertEqual(
            truth.labels[systems[1]]["TST-01"].source_location,
            f"systems[{systems[1]!r}].TST-01",
        )

    def test_json_v1_schema_version_kind_and_fields_are_strict(self) -> None:
        cases = (
            (
                "version_as_text",
                lambda payload: payload.__setitem__("schema_version", "1"),
                "schema_version 必须是整数 1",
            ),
            (
                "version_as_bool",
                lambda payload: payload.__setitem__("schema_version", True),
                "schema_version 必须是整数 1",
            ),
            (
                "unknown_kind",
                lambda payload: payload.__setitem__("kind", "markdown_labels"),
                "kind 必须是",
            ),
            (
                "missing_source_name",
                lambda payload: payload.pop("source_name"),
                "缺少=source_name",
            ),
            (
                "extra_top_level_field",
                lambda payload: payload.__setitem__("extra", True),
                "多余=extra",
            ),
        )
        for name, mutate, error_pattern in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                payload = _ground_truth_payload(item_count=2)
                mutate(payload)
                label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
                _write_label_json(label_file, payload)

                with self.assertRaisesRegex(ValueError, error_pattern):
                    evaluation.load_ground_truth_json(label_file)

    def test_json_v1_rejects_values_outside_three_class_enum(self) -> None:
        invalid_values = ("通过", "符合，测试标签", "maybe", "")
        for invalid_value in invalid_values:
            with (
                self.subTest(value=invalid_value),
                tempfile.TemporaryDirectory() as temp_dir,
            ):
                payload = _ground_truth_payload(item_count=2)
                systems = payload["systems"]
                assert isinstance(systems, dict)
                labels = systems[SYSTEMS[0]]
                assert isinstance(labels, dict)
                labels["TST-01"] = invalid_value
                label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
                _write_label_json(label_file, payload)

                with self.assertRaisesRegex(ValueError, "classification 无效"):
                    evaluation.load_ground_truth_json(label_file)

    def test_json_v1_rejects_incomplete_or_extra_matrix_entries(self) -> None:
        cases = (
            (
                "missing",
                lambda labels: labels.pop("TST-02"),
                "缺少=TST-02",
            ),
            (
                "extra",
                lambda labels: labels.__setitem__("EXTRA-01", "passed"),
                "多余=EXTRA-01",
            ),
        )
        for name, mutate, error_pattern in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                payload = _ground_truth_payload(item_count=2)
                systems = payload["systems"]
                assert isinstance(systems, dict)
                labels = systems[SYSTEMS[0]]
                assert isinstance(labels, dict)
                mutate(labels)
                label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
                _write_label_json(label_file, payload)

                with self.assertRaisesRegex(ValueError, error_pattern):
                    evaluation.load_ground_truth_json(label_file)

    def test_json_v1_rejects_duplicate_item_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = _ground_truth_payload(item_count=2)
            items = payload["items"]
            assert isinstance(items, list)
            items.append(dict(items[0]))
            label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
            _write_label_json(label_file, payload)

            with self.assertRaisesRegex(ValueError, "items 中稽核项重复"):
                evaluation.load_ground_truth_json(label_file)

    def test_json_v1_rejects_duplicate_object_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            label_file = Path(temp_dir) / evaluation.GROUND_TRUTH_FILENAME
            label_file.write_text(
                '{"schema_version":1,"schema_version":1}', encoding="utf-8"
            )

            with self.assertRaisesRegex(ValueError, "JSON 包含重复键"):
                evaluation.load_ground_truth_json(label_file)

    def test_markdown_label_is_rejected_without_runtime_conversion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            label_file = Path(temp_dir) / "labels.md"
            label_file.write_text("| 稽核项 | 系统 |\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不再支持 Markdown"):
                evaluation.load_ground_truth_json(label_file)


class SystemMatchingTests(unittest.TestCase):
    def test_system_units_must_match_exactly(self) -> None:
        mapping = evaluation.match_system_names(SYSTEMS, SYSTEMS)

        self.assertEqual(mapping["系统A-3"]["result_unit"], "系统A-3")
        self.assertEqual(mapping["系统B-2025-3"]["match_method"], "exact")

    def test_numeric_version_suffix_is_not_inferred(self) -> None:
        with self.assertRaisesRegex(ValueError, "系统 unit 不一致"):
            evaluation.match_system_names(["系统A"], ["系统A-3"])

    def test_missing_or_extra_system_unit_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "结果缺失=系统A-3"):
            evaluation.match_system_names(["系统A-3"], ["系统B-3"])


class PredictionClassificationTests(unittest.TestCase):
    def test_explicit_three_way_classification_is_required(self) -> None:
        prediction = evaluation.classify_prediction_item(
            {
                "item_id": "FHX-05",
                "status": "completed",
                "classification": "not_applicable",
                "batch_passed": True,
                "summary_file": "/outside/not-read.json",
            }
        )

        self.assertEqual(prediction.label, evaluation.LABEL_NOT_APPLICABLE)
        self.assertEqual(prediction.classification, "not_applicable")
        self.assertEqual(prediction.prediction_source, "classification")

    def test_old_batch_passed_only_summary_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "旧版汇总"):
            evaluation.classify_prediction_item(
                {
                    "item_id": "DJ-01",
                    "status": "completed",
                    "batch_passed": True,
                }
            )

    def test_unknown_explicit_classification_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "classification 值无效"):
            evaluation.classify_prediction_item(
                {
                    "item_id": "DJ-01",
                    "status": "completed",
                    "classification": "maybe",
                    "batch_passed": True,
                }
            )


class BatchEvaluationTests(unittest.TestCase):
    def _build_result_fixture(
        self, root: Path
    ) -> tuple[Path, Path, Path, dict[str, str]]:
        source = root / "核心网部"
        source.mkdir()
        result = root / "核心网部_全流程结果"
        result.mkdir()
        label_file = root / evaluation.GROUND_TRUTH_FILENAME
        _write_label_json(label_file)
        result_units = {system: system for system in SYSTEMS}
        flow_systems = []
        for system, unit in result_units.items():
            system_dir = result / "稽核结果" / unit
            normal_detail = system_dir / "details.json"
            not_applicable_detail = system_dir / "not-applicable.json"
            _write_json(normal_detail, {"runs": [{"applicable": None}]})
            _write_json(not_applicable_detail, {"runs": [{"applicable": False}]})
            items = []
            for number in range(1, 57):
                item_id = f"TST-{number:02d}"
                if system == "系统D-3" and item_id == "TST-02":
                    continue
                if item_id == "TST-01":
                    passed = system != "系统E-3"
                    detail = (
                        not_applicable_detail
                        if system == "系统C-3"
                        else normal_detail
                    )
                    classification = (
                        "not_applicable"
                        if system == "系统C-3"
                        else "passed"
                        if passed
                        else "failed"
                    )
                else:
                    passed = True
                    detail = normal_detail
                    classification = "passed"
                items.append(
                    {
                        "item_id": item_id,
                        "status": "completed",
                        "classification": classification,
                        "batch_passed": passed,
                        "summary_file": str(detail),
                    }
                )
            if system == "系统E-3":
                items.append(
                    {
                        "item_id": "EXTRA-01",
                        "status": "completed",
                        "classification": "passed",
                        "batch_passed": True,
                        "summary_file": str(normal_detail),
                    }
                )
            system_summary = system_dir / "系统稽核汇总.json"
            _write_json(
                system_summary,
                {
                    "unit": unit,
                    "source_directory": str((source / unit).resolve()),
                    "audit": {"items": items},
                    "all_passed": False,
                },
            )
            flow_systems.append(
                {
                    "unit": unit,
                    "status": "completed",
                    "summary_file": str(system_summary),
                }
            )
        _write_json(
            result / "全流程汇总.json",
            {
                "source_directory": str(source.resolve()),
                "execution_mode": "rule_only",
                "audit": {"systems": flow_systems, "excluded_model_items": []},
            },
        )
        return source, result, label_file, result_units

    def test_end_to_end_scores_only_common_items_and_writes_four_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source, result, label_file, _ = self._build_result_fixture(Path(temp_dir))

            summary = evaluation.evaluate_batch_results(
                source_dir=source, result_dir=result, label_file=label_file
            )

            self.assertEqual(summary["label_format"], "audit_ground_truth/v1")
            self.assertEqual(summary["totals"]["max_score"], 279)
            self.assertEqual(summary["totals"]["score"], 278)
            self.assertEqual(summary["totals"]["incorrect_items"], 1)
            self.assertEqual(summary["totals"]["truth_only_items"], 1)
            self.assertEqual(summary["totals"]["prediction_only_items"], 1)
            self.assertEqual(summary["mismatches"][0]["system"], "系统B-2025-3")
            self.assertEqual(summary["mismatches"][0]["item_id"], "TST-01")
            self.assertEqual(
                summary["systems"][2]["stats"]["truth_label_counts"]["不适用"],
                1,
            )

            output_dir = result / "评估结果"
            for name in ("评分汇总.json", "评分汇总.md", "逐项对比.csv", "不一致项.csv"):
                self.assertTrue((output_dir / name).is_file(), name)
            persisted = json.loads(
                (output_dir / "评分汇总.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["totals"]["score"], 278)
            self.assertIn("output_files", persisted)
            self.assertEqual(len(persisted["label_sha256"]), 64)
            with (output_dir / "不一致项.csv").open(
                encoding="utf-8-sig", newline=""
            ) as stream:
                mismatch_rows = list(csv.DictReader(stream))
            self.assertEqual(len(mismatch_rows), 1)
            self.assertEqual(mismatch_rows[0]["得分"], "0")
            self.assertEqual(mismatch_rows[0]["程序标签依据"], "classification")

    def test_source_name_must_match_input_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source, result, label_file, _ = self._build_result_fixture(Path(temp_dir))
            _write_label_json(
                label_file,
                _ground_truth_payload(source_name="传输部"),
            )

            with self.assertRaisesRegex(ValueError, "source_name 与原始材料目录名不一致"):
                evaluation.evaluate_batch_results(
                    source_dir=source,
                    result_dir=result,
                    label_file=label_file,
                )

    def test_source_directory_must_match_full_flow_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source, result, _, _ = self._build_result_fixture(Path(temp_dir))
            wrong_source = Path(temp_dir) / "其他部门"
            wrong_source.mkdir()
            wrong_label = Path(temp_dir) / "wrong-ground-truth.v1.json"
            _write_label_json(
                wrong_label,
                _ground_truth_payload(source_name=wrong_source.name),
            )

            with self.assertRaisesRegex(ValueError, "原始材料目录与评估输入不一致"):
                evaluation.evaluate_batch_results(
                    source_dir=wrong_source,
                    result_dir=result,
                    label_file=wrong_label,
                )

    def test_result_directory_must_not_be_inside_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            result = source / "核心网部_全流程结果"
            result.mkdir(parents=True)
            label_file = root / evaluation.GROUND_TRUTH_FILENAME
            _write_label_json(label_file)

            with self.assertRaisesRegex(ValueError, "不能相同或互相包含"):
                evaluation.evaluate_batch_results(
                    source_dir=source,
                    result_dir=result,
                    label_file=label_file,
                )

    def test_evaluation_output_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source, result, label_file, _ = self._build_result_fixture(root)
            outside = root / "outside"
            outside.mkdir()
            (result / "评估结果").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "符号链接评估目录"):
                evaluation.evaluate_batch_results(
                    source_dir=source,
                    result_dir=result,
                    label_file=label_file,
                )

    def test_system_summary_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            result = (root / "result").resolve()
            audit_root = result / "稽核结果"
            audit_root.mkdir(parents=True)
            outside_system = root / "outside" / "系统A-3"
            outside_system.mkdir(parents=True)
            (audit_root / "系统A-3").symlink_to(
                outside_system, target_is_directory=True
            )

            with self.assertRaisesRegex(ValueError, "越出全流程结果根目录"):
                evaluation._resolve_system_summary(
                    result, {"unit": "系统A-3"}
                )

    def test_cli_accepts_structured_json_override(self) -> None:
        args = evaluation.build_parser().parse_args(
            [
                "--source-dir",
                "/raw/核心网部",
                "--result-dir",
                "/result",
                "--label-file",
                "/labels/ground_truth.v1.json",
            ]
        )

        self.assertEqual(args.source_dir, Path("/raw/核心网部"))
        self.assertEqual(args.result_dir, Path("/result"))
        self.assertEqual(
            args.label_file, Path("/labels/ground_truth.v1.json")
        )


if __name__ == "__main__":
    unittest.main()
