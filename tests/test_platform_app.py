from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.audit_catalog import (
    AUDIT_ITEM_NAMES,
    DISABLED_ITEMS,
    audit_group_name,
    audit_item_name,
)
from audit_rules import list_rule_items
from extractors import list_extractor_items
from parsing_engine import preview_source
from platform_app.audit_service import (
    _field_summary,
    normalize_audit_summary,
    paired_item_ids,
    run_unit_audits,
)
from platform_app.database import Database
from platform_app.folder_picker import choose_system_folder
from platform_app.job_manager import safe_output_name, system_id_for
from platform_app.report import generate_report
from platform_app.worker import _run_ai_item, _seed_ai_cache, _should_narrate


class PlatformAppTests(unittest.TestCase):
    def test_audit_catalog_covers_every_registered_item(self) -> None:
        self.assertEqual(
            set(AUDIT_ITEM_NAMES) - set(DISABLED_ITEMS), set(paired_item_ids())
        )
        self.assertEqual(
            audit_item_name("dj_04"),
            "检查修订历史的版本号、日期和作者是否填写完整",
        )
        self.assertEqual(len(list_extractor_items()), 55)
        self.assertEqual(len(list_rule_items()), 55)
        self.assertEqual(len(paired_item_ids()), 54)
        self.assertNotIn("ZM-01", paired_item_ids())
        self.assertEqual(audit_group_name("dj-02"), "定级报告")
        self.assertEqual(audit_group_name("FX_11"), "风险评估报告")

    def test_field_summary_keeps_scalars_and_compacts_large_records(self) -> None:
        preview = _field_summary({"社会影响力赋值": 2, "资产记录": [{"IP": "1.1.1.1"}] * 8})

        self.assertEqual(preview[0], {"field": "社会影响力赋值", "value": "2"})
        self.assertEqual(preview[1]["value"], "8 条记录（完整内容见详情）")

    @patch("platform_app.folder_picker.subprocess.run")
    def test_native_folder_picker_returns_absolute_directory(self, run) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run.return_value = CompletedProcess([], 0, stdout=f"{temporary}\n", stderr="")

            with patch("platform_app.folder_picker.sys.platform", "darwin"):
                selected = choose_system_folder()

            self.assertEqual(selected, Path(temporary).resolve())
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/osascript")

    def test_preview_treats_selected_folder_as_the_only_unit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            unit = root / "网络安全部" / "测试系统-2"
            sibling = root / "网络安全部" / "其他系统-1"
            unit.mkdir(parents=True)
            sibling.mkdir(parents=True)
            (unit / "测试系统定级报告.docx").write_bytes(b"word")
            (unit / "测试系统资产清单.xlsx").write_bytes(b"excel")
            (sibling / "其他系统备案信息表.docx").write_bytes(b"other")

            preview = preview_source(unit)

            self.assertEqual(preview["system_name"], "测试系统-2")
            self.assertEqual(preview["recognized_count"], 2)
            self.assertNotIn("备案信息表", preview["category_counts"])
            self.assertEqual(preview["expected_category_count"], 12)
            self.assertEqual(preview["present_category_count"], 2)
            self.assertIn("备案信息表", preview["missing_categories"])
            self.assertEqual(
                [item["category_name"] for item in preview["category_statuses"]],
                [
                    "定级报告",
                    "备案信息表",
                    "资产清单",
                    "三同步报告",
                    "符合性评测表",
                    "符合性评测表证明材料",
                    "风险评估报告",
                    "应急预案",
                    "应急演练报告",
                    "扫描报告",
                    "符合性评测报告",
                    "演练计划",
                ],
            )

    def test_preview_recognizes_html_scan_zip_as_scan_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary) / "部门A" / "测试系统-1"
            unit.mkdir(parents=True)
            with zipfile.ZipFile(unit / "report.zip", "w") as archive:
                archive.writestr("index.html", "<html><title>渗透测试报告</title></html>")

            preview = preview_source(unit)

            self.assertEqual(preview["category_counts"]["扫描报告"], 1)
            self.assertNotIn("扫描报告", preview["missing_categories"])
            scan_status = next(
                item
                for item in preview["category_statuses"]
                if item["category_name"] == "扫描报告"
            )
            self.assertEqual(scan_status["count"], 1)

    def test_all_registered_pairs_run_without_batch_directory_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            on_item_ready = Mock(
                return_value={"status": "completed", "text": "LLM已处理"}
            )

            result = run_unit_audits(
                parsed,
                root / "audits",
                on_item_ready=on_item_ready,
            )

            self.assertEqual(result["item_count"], len(paired_item_ids()))
            self.assertEqual(len(result["items"]), len(paired_item_ids()))
            self.assertTrue(all(item["status"] == "failed" for item in result["items"]))
            reason_by_item = {
                item["item_id"]: item["reason_code"] for item in result["items"]
            }
            self.assertTrue(
                all(
                    reason == "rule_failed"
                    if item_id.startswith("DCZ-")
                    else reason == "missing_document"
                    for item_id, reason in reason_by_item.items()
                )
            )
            self.assertEqual(result["counts"], {"failed": len(paired_item_ids())})
            self.assertEqual(on_item_ready.call_count, len(paired_item_ids()))
            self.assertTrue(
                all(
                    item["ai_narration"]["status"] == "completed"
                    for item in result["items"]
                )
            )

    def test_ai_callback_is_strictly_serial_and_failure_keeps_rule_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            source = parsed / "source.md"
            source.write_text("测试", encoding="utf-8")
            order: list[str] = []

            def extractor_spec(item_id: str) -> SimpleNamespace:
                return SimpleNamespace(extractor=lambda _: {"稽核项": item_id})

            def rule_spec(item_id: str) -> SimpleNamespace:
                def rule(fields: dict[str, object]) -> dict[str, object]:
                    if item_id == "TEST-02":
                        return {
                            "passed": False,
                            "applicable": False,
                            "message": "不适用",
                            "issues": [],
                        }
                    return {"passed": True, "message": "规则通过", "issues": []}

                return SimpleNamespace(rule=rule)

            emitted_events: list[dict[str, object]] = []

            def on_event(event: dict[str, object]) -> None:
                emitted_events.append(event)
                order.append(f"{event['type']}:{event['item_id']}")

            def on_item_ready(payload: dict[str, object]) -> dict[str, object]:
                item_id = str(payload["item_id"])
                order.append(f"ai:{item_id}")
                if item_id == "TEST-01":
                    return {
                        "status": "failed",
                        "text": "AI说明生成失败",
                        "error_code": "timeout",
                    }
                return {"status": "completed", "text": "已复述"}

            with (
                patch(
                    "platform_app.audit_service.paired_item_ids",
                    return_value=["TEST-01", "TEST-02"],
                ),
                patch(
                    "platform_app.audit_service.get_extractor_spec",
                    side_effect=extractor_spec,
                ),
                patch(
                    "platform_app.audit_service.select_input_documents",
                    return_value=(source,),
                ),
                patch(
                    "platform_app.audit_service.get_rule_spec",
                    side_effect=rule_spec,
                ),
            ):
                result = run_unit_audits(
                    parsed,
                    root / "audits",
                    on_event=on_event,
                    on_item_ready=on_item_ready,
                )

            self.assertEqual(
                order,
                [
                    "audit_started:TEST-01",
                    "ai:TEST-01",
                    "audit_complete:TEST-01",
                    "audit_started:TEST-02",
                    "ai:TEST-02",
                    "audit_complete:TEST-02",
                ],
            )
            self.assertEqual(
                [item["status"] for item in result["items"]],
                ["passed", "not_applicable"],
            )
            self.assertTrue(
                all(event.get("item_name") for event in emitted_events)
            )
            completed_events = [
                event for event in emitted_events
                if event["type"] == "audit_complete"
            ]
            self.assertEqual(
                [event.get("reason_code") for event in completed_events],
                ["passed", "not_applicable"],
            )
            self.assertEqual(
                [event["ai_narration"]["status"] for event in completed_events],
                ["failed", "completed"],
            )
            self.assertEqual(result["items"][0]["ai_narration"]["status"], "failed")
            self.assertEqual(
                result["items"][1]["ai_narration"]["status"], "completed"
            )

    def test_completed_event_embeds_llm_result_without_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            source = parsed / "source.md"
            source.write_text("测试", encoding="utf-8")
            events: list[dict[str, object]] = []

            with (
                patch(
                    "platform_app.audit_service.paired_item_ids",
                    return_value=["TEST-01"],
                ),
                patch(
                    "platform_app.audit_service.get_extractor_spec",
                    return_value=SimpleNamespace(extractor=lambda _: {"字段": "值"}),
                ),
                patch(
                    "platform_app.audit_service.select_input_documents",
                    return_value=(source,),
                ),
                patch(
                    "platform_app.audit_service.get_rule_spec",
                    return_value=SimpleNamespace(
                        rule=lambda _: {
                            "passed": True,
                            "message": "稽核已完成",
                            "issues": [],
                        }
                    ),
                ),
            ):
                result = run_unit_audits(
                    parsed,
                    root / "audits",
                    on_event=events.append,
                    on_item_ready=lambda _: {
                        "status": "completed",
                        "text": "LLM处理完成后才发布",
                        "cached": False,
                    },
                )

            completed = next(event for event in events if event["type"] == "audit_complete")
            self.assertNotIn("ai_pending", completed)
            self.assertEqual(completed["ai_narration"]["status"], "completed")
            self.assertEqual(
                completed["ai_narration"]["text"],
                "LLM处理完成后才发布",
            )
            self.assertEqual(result["items"][0]["status"], "passed")
            self.assertEqual(
                result["items"][0]["ai_narration"]["status"],
                "completed",
            )

    def test_worker_ai_events_stream_chunks_and_sanitize_unexpected_failure(self) -> None:
        class StreamingService:
            def generate(self, payload, *, on_delta=None):
                self.payload = payload
                if on_delta:
                    on_delta("关键提取结果：")
                    on_delta("已提取。")
                return {
                    "status": "completed",
                    "model": "glm-5.3",
                    "text": "关键提取结果：已提取。",
                    "cached": False,
                }

        class BrokenService:
            settings = SimpleNamespace(model="glm-5.3")

            def generate(self, payload, *, on_delta=None):
                raise RuntimeError("should-not-be-persisted")

        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "audits" / "TEST-01"
            normalized = {
                "status": "passed",
                "message": "固定结论为通过",
                "extracted_summary": [{"field": "A", "value": "B"}],
            }
            payload = {
                "item_id": "TEST-01",
                "item_name": "测试项",
                "current": 1,
                "total": 2,
                "normalized": normalized,
                "item_dir": item_dir,
            }
            events: list[dict[str, object]] = []

            completed = _run_ai_item(StreamingService(), payload, events.append)

            self.assertEqual(completed["status"], "completed")
            self.assertEqual(
                [event["type"] for event in events],
                ["ai_started", "ai_delta", "ai_delta", "ai_completed"],
            )
            self.assertEqual([event["chunk_seq"] for event in events], [0, 1, 2, 3])
            self.assertTrue(
                all(event["audit_status"] == "passed" for event in events)
            )

            failure_events: list[dict[str, object]] = []
            failed = _run_ai_item(BrokenService(), payload, failure_events.append)
            persisted = (item_dir / "AI复述.json").read_text(encoding="utf-8")

            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failure_events[-1]["type"], "ai_failed")
            self.assertEqual(normalized["status"], "passed")
            self.assertNotIn("should-not-be-persisted", persisted)

    def test_precomputed_semantic_analysis_is_reused_without_second_model_call(self) -> None:
        service = SimpleNamespace(generate=Mock(side_effect=AssertionError("不得二次调用")))
        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "audits" / "DJ-01"
            payload = {
                "item_id": "DJ-01",
                "item_name": "检查三要素赋值",
                "current": 1,
                "total": 41,
                "fields": {"社会影响力": 2},
                "normalized": {"status": "passed", "message": "三要素一致"},
                "rule_result": {
                    "passed": True,
                    "message": "三要素一致",
                    "_precomputed_narration": {
                        "attempted": True,
                        "status": "completed",
                        "model": "glm-5v-turbo",
                        "text": "我核对了三要素赋值及依据。因此，本项判断为通过。",
                        "usage": {"completion_tokens": 16},
                    },
                },
                "item_dir": item_dir,
            }
            events: list[dict[str, object]] = []
            result = _run_ai_item(service, payload, events.append)
            saved = json.loads((item_dir / "AI复述.json").read_text(encoding="utf-8"))

        service.generate.assert_not_called()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(saved["model"], "glm-5v-turbo")
        self.assertEqual(
            [event["type"] for event in events],
            ["ai_started", "ai_delta", "ai_completed"],
        )

    def test_worker_seeds_previous_ai_file_only_when_service_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            previous = root / "previous"
            staging_item = root / "staging" / "audits" / "TEST-01"
            cached = previous / "audits" / "TEST-01" / "AI复述.json"
            cached.parent.mkdir(parents=True)
            cached.write_text('{"status":"completed"}', encoding="utf-8")
            payload = {"item_id": "TEST-01", "item_dir": staging_item}

            _seed_ai_cache(SimpleNamespace(available=True), payload, previous)

            target = staging_item / "AI复述.json"
            self.assertEqual(target.read_text(encoding="utf-8"), cached.read_text())

            target.unlink()
            _seed_ai_cache(SimpleNamespace(available=False), payload, previous)
            self.assertFalse(target.exists())

    def test_explicit_ai_disable_restores_pre_ai_callback_flow(self) -> None:
        disabled = SimpleNamespace(settings=SimpleNamespace(enabled=False))
        unconfigured_but_enabled = SimpleNamespace(
            settings=SimpleNamespace(enabled=True), available=False
        )

        self.assertFalse(_should_narrate(disabled))
        self.assertTrue(_should_narrate(unconfigured_but_enabled))

    def test_legacy_audit_statuses_are_exposed_as_three_public_outcomes(self) -> None:
        result = normalize_audit_summary(
            {
                "audit": {
                    "items": [
                        {"item_id": "A", "status": "passed"},
                        {"item_id": "B", "status": "not_applicable"},
                        {"item_id": "C", "status": "missing_document"},
                        {"item_id": "D", "status": "extraction_error"},
                        {"item_id": "E", "status": "needs_review"},
                    ]
                }
            }
        )

        self.assertEqual(
            [item["status"] for item in result["audit"]["items"]],
            ["passed", "not_applicable", "failed", "failed", "failed"],
        )
        self.assertEqual(
            [item.get("reason_code") for item in result["audit"]["items"]][2:],
            ["missing_document", "extraction_error", "needs_review"],
        )
        self.assertEqual(
            result["audit"]["counts"],
            {"failed": 3, "not_applicable": 1, "passed": 1},
        )

    def test_system_id_uses_full_resolved_path_not_only_folder_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "部门A" / "同名系统-2"
            second = root / "部门B" / "同名系统-2"
            first.mkdir(parents=True)
            second.mkdir(parents=True)

            first_id = system_id_for(first)
            second_id = system_id_for(second)

            self.assertNotEqual(first_id, second_id)
            self.assertNotEqual(
                safe_output_name(first.name, first_id),
                safe_output_name(second.name, second_id),
            )

    def test_database_enforces_one_system_per_source_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "platform.db")
            database.upsert_system(
                system_id="system-a",
                source_path="/tmp/system-a",
                display_name="系统A",
                department="部门A",
                output_dir="/tmp/result-a",
            )
            database.upsert_system(
                system_id="system-a",
                source_path="/tmp/system-a",
                display_name="系统A（更新）",
                department="部门A",
                output_dir="/tmp/result-a",
            )

            systems = database.list_systems()

            self.assertEqual(len(systems), 1)
            self.assertEqual(systems[0]["display_name"], "系统A（更新）")

    def test_sse_event_ids_are_utf8_byte_offsets_and_resume_without_replay(self) -> None:
        from platform_app import api
        from platform_app.api import _parse_last_event_id, _read_event_records

        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "events.jsonl"
            first = json.dumps(
                {"type": "ai_delta", "delta": "中文"}, ensure_ascii=False
            ) + "\n"
            second = json.dumps(
                {"type": "ai_completed", "message": "完成"}, ensure_ascii=False
            ) + "\n"
            target.write_bytes((first + second).encode("utf-8"))

            cursor, records = _read_event_records(target, 0)
            first_offset = len(first.encode("utf-8"))

            self.assertEqual(records[0][0], first_offset)
            self.assertEqual(cursor, len((first + second).encode("utf-8")))
            resumed_cursor, resumed = _read_event_records(target, first_offset)
            self.assertEqual(resumed, [records[1]])
            self.assertEqual(resumed_cursor, cursor)
            self.assertEqual(_parse_last_event_id(str(first_offset)), first_offset)
            self.assertEqual(_parse_last_event_id("invalid"), 0)

            async def consume() -> str:
                response = await api.stream_events("job", str(first_offset))
                chunks: list[str] = []
                async for chunk in response.body_iterator:
                    chunks.append(
                        chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
                    )
                return "".join(chunks)

            terminal_db = SimpleNamespace(
                get_job=lambda _: {"id": "job", "status": "completed"}
            )
            with (
                patch.object(api, "db", terminal_db),
                patch.object(api, "event_path", return_value=target),
                patch.object(api.jobs, "get_job", side_effect=terminal_db.get_job),
            ):
                streamed = asyncio.run(consume())

            self.assertNotIn(first.rstrip(), streamed)
            self.assertIn(second.rstrip(), streamed)
            self.assertIn(f"id: {cursor}", streamed)

    def test_health_never_exposes_ai_key(self) -> None:
        from platform_app import api

        with patch.object(
            api,
            "ai_config_status",
            return_value={
                "enabled": True,
                "configured": True,
                "available": True,
                "model": "glm-5.3",
                "endpoint_host": "open.bigmodel.cn",
                "api_key": "must-not-leak",
            },
        ):
            payload = api.health()

        self.assertNotIn("ai", payload)
        self.assertNotIn("must-not-leak", json.dumps(payload, ensure_ascii=False))

    def test_frontend_index_is_never_cached_across_upgrades(self) -> None:
        from platform_app import api

        with tempfile.TemporaryDirectory() as temporary:
            frontend = Path(temporary)
            (frontend / "index.html").write_text("<html></html>", encoding="utf-8")
            with patch.object(api, "FRONTEND_DIST", frontend):
                response = api.frontend("")

        self.assertEqual(
            response.headers["cache-control"],
            "no-store, no-cache, must-revalidate",
        )
        self.assertEqual(response.headers["pragma"], "no-cache")
        self.assertEqual(response.headers["expires"], "0")

    def test_audit_detail_reads_ai_narration_file(self) -> None:
        from platform_app import api

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            item_dir = output / "audits" / "DJ-02"
            item_dir.mkdir(parents=True)
            (item_dir / "DJ-02.json").write_text("{}", encoding="utf-8")
            (item_dir / "稽核结果.json").write_text("{}", encoding="utf-8")
            narration = {
                "status": "completed",
                "model": "glm-5.3",
                "text": "关键提取结果。\n判定说明。",
            }
            (item_dir / "AI复述.json").write_text(
                json.dumps(narration, ensure_ascii=False), encoding="utf-8"
            )
            summary = {
                "audit": {
                    "items": [
                        {
                            "item_id": "DJ-02",
                            "status": "passed",
                            "message": "通过",
                            "fields_file": "audits/DJ-02/DJ-02.json",
                            "result_file": "audits/DJ-02/稽核结果.json",
                        }
                    ]
                }
            }
            (output / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8"
            )

            with (
                patch.object(
                    api,
                    "_system_or_404",
                    return_value={"output_dir": str(output)},
                ),
                patch.object(api, "paired_item_ids", return_value=["DJ-02"]),
            ):
                detail = api.audit_detail("system", "dj_02")

            self.assertEqual(detail["ai_narration"], narration)
            self.assertEqual(detail["summary"]["ai_narration"], narration)

    def test_offline_report_embeds_data_and_has_no_remote_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "汇总报告.html"
            fields_file = root / "audits" / "DJ-02" / "DJ-02.json"
            fields_file.parent.mkdir(parents=True)
            fields_file.write_text(
                json.dumps({"社会影响力赋值": 2}, ensure_ascii=False), encoding="utf-8"
            )
            ai_file = fields_file.parent / "AI复述.json"
            ai_file.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "model": "glm-5.3",
                        "text": "关键提取结果：社会影响力赋值为2。\n判定说明：等级一致。",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            summary = {
                "generated_at": "2026-08-24T12:00:00+08:00",
                "system": {"name": "测试系统", "source_path": "/tmp/测试系统"},
                "parse": {"file_count": 3},
                "audit": {
                    "item_count": 3,
                    "counts": {"passed": 1, "failed": 1, "missing_document": 1},
                    "items": [
                        {"item_id": "DJ-02", "status": "passed", "message": "等级一致", "issues": [], "source_documents": [], "fields_file": "audits/DJ-02/DJ-02.json", "extracted_summary": [{"field": "社会影响力赋值", "value": "2"}]},
                        {"item_id": "DJ-04", "status": "failed", "message": "作者为空", "issues": ["第2行缺少作者"], "source_documents": []},
                        {"item_id": "DJ-05", "status": "missing_document", "message": "缺少定级报告", "issues": ["缺少定级报告"], "source_documents": []},
                    ],
                },
            }

            generate_report(summary, target)
            content = target.read_text(encoding="utf-8")

            self.assertIn("测试系统", content)
            self.assertIn("DJ-04", content)
            self.assertIn("中间提取内容", content)
            self.assertIn('data-report-version="2"', content)
            self.assertIn("稽核说明", content)
            self.assertIn("全部分类", content)
            self.assertIn("社会影响力赋值为2", content)
            self.assertIn(".ai-narration.skipped", content)
            self.assertIn("n.status==='skipped'", content)
            self.assertNotIn("SECURITY AUDIT INTELLIGENCE", content)
            self.assertNotIn("https://", content)
            self.assertNotIn("http://", content)
            embedded = content.split('<script type="application/json" id="audit-data">', 1)[1].split("</script>", 1)[0]
            payload = json.loads(embedded)
            self.assertEqual(payload["audit"]["item_count"], 3)
            self.assertTrue(
                all(
                    item["group_name"] == "定级报告"
                    for item in payload["audit"]["items"]
                )
            )
            self.assertEqual(payload["audit"]["items"][0]["extracted_fields"]["社会影响力赋值"], 2)
            self.assertEqual(
                payload["audit"]["items"][0]["ai_narration"]["status"],
                "completed",
            )
            self.assertEqual(payload["audit"]["items"][2]["status"], "failed")
            self.assertEqual(payload["audit"]["items"][2]["reason_code"], "missing_document")
            self.assertEqual(payload["audit"]["counts"], {"failed": 2, "passed": 1})
            self.assertNotIn("需关注", content)


if __name__ == "__main__":
    unittest.main()
