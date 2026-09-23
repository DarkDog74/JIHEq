from __future__ import annotations

import base64
import glob
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    ModelReply,
    chat,
    chat_with_images,
    extract_json_object,
)
from audit_rules.model_support import (
    confidence_of,
    extract_node_ips,
    extract_node_names,
)

try:
    from PIL import Image

    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake-model", elapsed_ms=3
    )


class ExtractJsonObjectTests(unittest.TestCase):
    def test_extracts_plain_and_prose_wrapped(self) -> None:
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(
            extract_json_object('结论如下：\n{"a": {"b": 2}} 以上。'), {"a": {"b": 2}}
        )

    def test_skips_broken_block_and_finds_next(self) -> None:
        text = '前缀 {"broken": } 中间 {"ok": true} 后缀'
        self.assertEqual(extract_json_object(text), {"ok": True})

    def test_raises_when_no_json(self) -> None:
        with self.assertRaises(ModelCallError):
            extract_json_object("完全没有 JSON 的回复")

    def test_unbalanced_braces_inside_string_value(self) -> None:
        text = '{"片段": "右括号 } 未配对", "代码": "if (a) {{ 开两层", "ok": true}'
        self.assertEqual(
            extract_json_object(text),
            {"片段": "右括号 } 未配对", "代码": "if (a) {{ 开两层", "ok": True},
        )

    def test_escaped_quotes_inside_string_value(self) -> None:
        text = '{"文本": "他说\\"{你好}\\"", "ok": true}'
        self.assertEqual(
            extract_json_object(text), {"文本": '他说"{你好}"', "ok": True}
        )

    def test_fenced_json_block_fallback(self) -> None:
        text = '说明 { 未闭合的片段\n```json\n{"ok": true}\n```\n以上。'
        self.assertEqual(extract_json_object(text), {"ok": True})

    def test_invalid_fenced_block_still_raises(self) -> None:
        with self.assertRaises(ModelCallError):
            extract_json_object('```json\n{不是JSON}\n```')

    def test_odd_bare_quotes_before_json_falls_back_to_legacy_scan(self) -> None:
        # JSON 前出现奇数个裸引号：字符串状态扫描会整体卡进字符串态，
        # 围栏也没有——由不带字符串状态的旧版配平扫描兜底（两版行为取并集）
        text = '拓扑识别结果为"高可信，具体如下：{"节点": [], "置信度": 0.9}'
        self.assertEqual(extract_json_object(text), {"节点": [], "置信度": 0.9})


class ChatConfigurationTests(unittest.TestCase):
    def test_chat_raises_when_not_configured(self) -> None:
        empty = {
            "base_url": "",
            "api_key": "",
            "model": "",
            "timeout_seconds": 30,
        }
        with patch("core.model_client.load_model_config", return_value=empty):
            with self.assertRaises(ModelNotConfiguredError):
                chat("ping")

    def test_glm_53_flash_uses_supported_low_thinking_mode(self) -> None:
        config = {
            "base_url": "https://model.example/v1",
            "api_key": "test-key",
            "model": "glm-5.3-flash",
            "timeout_seconds": 30,
        }
        response = Mock(
            status_code=200,
            text='{"choices":[{"message":{"content":"OK"}}]}',
            headers={},
        )
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        with (
            patch("core.model_client.load_model_config", return_value=config),
            patch("core.model_client.httpx.post", return_value=response) as post,
        ):
            reply = chat("ping", max_tokens=128)

        self.assertEqual(reply.text, "OK")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(
            payload["thinking"], {"type": "enabled", "clear_thinking": False}
        )
        self.assertEqual(payload["reasoning_effort"], "low")


class RetryPolicyTests(unittest.TestCase):
    """确定性 4xx 立即失败不重试；429/5xx 维持现有重试与退避。"""

    def _config(self) -> dict:
        return {
            "base_url": "http://model.invalid",
            "api_key": "test-key",
            "model": "glm-5v-turbo",
            "timeout_seconds": 30,
        }

    def test_401_fails_immediately_without_retry(self) -> None:
        response = Mock(status_code=401, text="Unauthorized", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep") as sleep,
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("HTTP 401", str(context.exception))

    def test_429_still_retries_until_limit(self) -> None:
        response = Mock(status_code=429, text="", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep"),
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 4)
        self.assertIn("429", str(context.exception))

    def test_500_still_retries_until_limit(self) -> None:
        response = Mock(status_code=500, text="server error", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep"),
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 4)
        self.assertIn("HTTP 500", str(context.exception))


class ModelSupportTests(unittest.TestCase):
    def test_confidence_of_variants(self) -> None:
        self.assertEqual(confidence_of({"置信度": 0.8}), 0.8)
        self.assertEqual(confidence_of({"置信度": "0.9"}), 0.9)
        self.assertIsNone(confidence_of({"置信度": None}))
        self.assertIsNone(confidence_of({}))

    def test_extract_node_ips_filters_invalid(self) -> None:
        payload = {
            "节点": [
                {"名称": "a", "IP": "10.0.0.1"},
                {"名称": "b", "IP": "不是IP"},
                {"名称": "c", "IP": None},
                {"名称": "d", "IP": "10.0.0.1"},
            ]
        }
        self.assertEqual(extract_node_ips(payload), ["10.0.0.1"])
        self.assertEqual(extract_node_names(payload), ["a", "b", "c", "d"])


@unittest.skipUnless(HAS_PIL, "环境缺少 Pillow")
class ImageNormalizationTests(unittest.TestCase):
    """图片输入统一归一化：非常规格式转白底 RGB JPEG，避免识别服务 400。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _config(self) -> dict:
        return {
            "base_url": "http://model.invalid",
            "api_key": "test-key",
            "model": "glm-5v-turbo",
            "timeout_seconds": 30,
        }

    def _run(self, files: list[Path]):
        with (
            patch(
                "core.model_client.load_model_config", return_value=self._config()
            ),
            patch(
                "core.model_client._post",
                return_value=ModelReply("OK", "fake", 1),
            ) as post,
        ):
            chat_with_images("p", files)
        content = post.call_args.args[0]["messages"][0]["content"]
        return [
            part["image_url"]["url"]
            for part in content
            if part["type"] == "image_url"
        ]

    @staticmethod
    def _split(url: str) -> tuple[str, bytes]:
        header, b64 = url.split(",", 1)
        return header.split(":", 1)[1].split(";", 1)[0], base64.b64decode(b64)

    @staticmethod
    def _temp_count() -> int:
        return len(glob.glob(os.path.join(tempfile.gettempdir(), "jihe-img-*")))

    def test_uncommon_format_bmp_converted_to_jpeg(self) -> None:
        path = self.root / "拓扑图.bmp"
        Image.new("RGB", (8, 6), (200, 30, 40)).save(path, "BMP")
        before = self._temp_count()

        urls = self._run([path])

        self.assertEqual(len(urls), 1)
        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(raw[:2], b"\xff\xd8")
        with Image.open(io.BytesIO(raw)) as check:
            self.assertEqual(check.format, "JPEG")
            self.assertEqual(check.mode, "RGB")
        # 转换用的临时文件用完即删，不能堆积
        self.assertEqual(self._temp_count(), before)

    def test_tiff_converted_to_jpeg(self) -> None:
        path = self.root / "拓扑图.tiff"
        Image.new("RGB", (8, 6)).save(path, "TIFF")

        urls = self._run([path])

        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/jpeg")
        with Image.open(io.BytesIO(raw)) as check:
            self.assertEqual(check.format, "JPEG")

    def test_cmyk_jpeg_flattened_to_rgb_jpeg(self) -> None:
        path = self.root / "cmyk.jpg"
        Image.new("CMYK", (8, 6), (0, 255, 0, 0)).save(path, "JPEG")

        urls = self._run([path])

        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/jpeg")
        with Image.open(io.BytesIO(raw)) as check:
            self.assertEqual(check.format, "JPEG")
            self.assertEqual(check.mode, "RGB")

    def test_standard_rgb_jpeg_passes_through_unchanged(self) -> None:
        path = self.root / "普通.jpg"
        Image.new("RGB", (8, 6)).save(path, "JPEG")
        original = path.read_bytes()

        urls = self._run([path])

        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(raw, original)

    def test_palette_png_passes_through_unchanged(self) -> None:
        path = self.root / "索引.png"
        Image.new("RGB", (8, 6)).convert("P").save(path, "PNG")
        original = path.read_bytes()

        urls = self._run([path])

        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/png")
        self.assertEqual(raw, original)

    def test_corrupt_image_is_skipped(self) -> None:
        # 单文件全被剔除时明确报错，不发文本裸请求
        path = self.root / "坏图.png"
        path.write_bytes(b"not-an-image")
        with (
            patch(
                "core.model_client.load_model_config",
                return_value=self._config(),
            ),
            patch("core.model_client._post") as post,
        ):
            with self.assertRaises(ModelCallError) as ctx:
                chat_with_images("p", [path])
        self.assertIn("无法渲染为位图", str(ctx.exception))
        post.assert_not_called()

    def test_header_only_image_is_skipped(self) -> None:
        # EMF/嵌入 Office 对象在本机“能读文件头、转不出像素”，
        # 用截断的 PNG 模拟同一类失败
        full = self.root / "完整.png"
        Image.new("RGB", (8, 6)).save(full, "PNG")
        raw = full.read_bytes()
        path = self.root / "截断.png"
        path.write_bytes(raw[: len(raw) // 3])
        with (
            patch(
                "core.model_client.load_model_config",
                return_value=self._config(),
            ),
            patch("core.model_client._post") as post,
        ):
            with self.assertRaises(ModelCallError) as ctx:
                chat_with_images("p", [path])
        self.assertIn("无法渲染为位图", str(ctx.exception))
        post.assert_not_called()

    def test_valid_and_unsendable_mix_only_sends_valid(self) -> None:
        good = self.root / "正常.jpg"
        Image.new("RGB", (8, 6)).save(good, "JPEG")
        original = good.read_bytes()
        bad = self.root / "嵌入对象.bin"
        bad.write_bytes(b"PK\x03\x04 not an image")

        urls = self._run([bad, good])

        self.assertEqual(len(urls), 1)
        mime, raw = self._split(urls[0])
        self.assertEqual(mime, "image/jpeg")
        self.assertEqual(raw, original)

    def test_all_images_unsendable_raises_without_post(self) -> None:
        bad = self.root / "嵌入对象.bin"
        bad.write_bytes(b"PK\x03\x04 not an image")
        with (
            patch(
                "core.model_client.load_model_config",
                return_value=self._config(),
            ),
            patch("core.model_client._post") as post,
        ):
            with self.assertRaises(ModelCallError) as ctx:
                chat_with_images("p", [bad])
        self.assertIn("无法渲染为位图", str(ctx.exception))
        post.assert_not_called()

    def test_missing_image_raises(self) -> None:
        with (
            patch(
                "core.model_client.load_model_config",
                return_value=self._config(),
            ),
            patch("core.model_client._post"),
        ):
            with self.assertRaises(ModelCallError):
                chat_with_images("p", [self.root / "不存在.png"])

    def test_temp_file_removed_when_post_fails(self) -> None:
        path = self.root / "拓扑图.bmp"
        Image.new("RGB", (8, 6)).save(path, "BMP")
        before = self._temp_count()

        with (
            patch(
                "core.model_client.load_model_config",
                return_value=self._config(),
            ),
            patch(
                "core.model_client._post",
                side_effect=ModelCallError("boom"),
            ),
        ):
            with self.assertRaises(ModelCallError):
                chat_with_images("p", [path])

        self.assertEqual(self._temp_count(), before)


if __name__ == "__main__":
    unittest.main()
