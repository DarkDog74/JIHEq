"""模型调用的传输层公共工具：SSE 解析与流式正文拼装。

平台里有三处会打模型服务——网页的「测试连接」探活、规则层的同步调用
（core/model_client.py）、稽核说明的流式调用（platform_app/ai_service.py）。
它们面对的都是同一套 OpenAI 风格响应，解析逻辑放这里共用一份，
避免三个地方各写一遍、改一处漏两处。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any, Iterable, Mapping

# 单次流式回复拼接后的上限，防御性上限，正常回复远达不到
MAX_STREAM_CHARS = 4 * 1024 * 1024

# 请求日志。排查「参数到底发出去没有」只能靠它：配置页填的东西经过好几层
# 合并（附加参数 → 自动思考参数 → 流式 → 认证头），光看配置猜不出最终发了什么。
# 默认开启，跑通了可以设 AUDIT_LLM_LOG=off 关掉。
_LOG_FLAG = os.environ.get("AUDIT_LLM_LOG", "on").strip().casefold()
_LOG_ENABLED = _LOG_FLAG not in ("0", "false", "off", "no")
_LOG_PREFIX = "[LLM]"
_MAX_LOG_BODY_CHARS = 2000
_MAX_LOG_REPLY_CHARS = 300
# 这些请求头的值不能进日志
_SENSITIVE_HEADERS = frozenset({"authorization", "api-key", "x-api-key"})


def _mask(value: str) -> str:
    text = str(value)
    if len(text) <= 8:
        return "••••"
    return f"{text[:4]}••••{text[-4:]}"


def _emit(message: str) -> None:
    # 走 stderr：容器里 docker logs 一样能看到，且不会混进业务输出
    print(f"{_LOG_PREFIX} {message}", file=sys.stderr, flush=True)


def log_llm_request(url: str, headers: Mapping[str, str], body: Any) -> None:
    """把实际发出的请求打出来。密钥类请求头做脱敏。"""
    if not _LOG_ENABLED:
        return
    safe_headers = {
        key: (_mask(value) if key.lower() in _SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }
    _emit(f">>> POST {url}")
    _emit(f">>> 请求头 {json.dumps(safe_headers, ensure_ascii=False)}")
    try:
        text = json.dumps(body, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(body)
    if len(text) > _MAX_LOG_BODY_CHARS:
        text = text[:_MAX_LOG_BODY_CHARS] + f"…（共 {len(text)} 字符）"
    _emit(f">>> 请求体 {text}")


def log_llm_response(status: int, elapsed_ms: int, preview: str = "") -> None:
    """把响应状态和耗时打出来。耗时能直接区分「模型慢」和「参数没生效」。"""
    if not _LOG_ENABLED:
        return
    text = str(preview or "").replace("\n", " ").strip()
    if len(text) > _MAX_LOG_REPLY_CHARS:
        text = text[:_MAX_LOG_REPLY_CHARS] + "…"
    suffix = f" 正文={text}" if text else ""
    _emit(f"<<< HTTP {status}  耗时 {elapsed_ms}ms{suffix}")


def log_llm_note(message: str) -> None:
    if _LOG_ENABLED:
        _emit(message)


class _Timer:
    """小工具：给调用点算耗时，避免每处都写 time.perf_counter。"""

    def __init__(self) -> None:
        self.started = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self.started) * 1000)


def start_timer() -> _Timer:
    return _Timer()


class LLMStreamError(RuntimeError):
    """流式响应里带回了错误帧，或内容无法解析。"""


def content_text(content: Any) -> str:
    """从 content 字段里取纯文本。兼容字符串和分块数组两种形态。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "".join(part for part in parts if part)
    return ""


def iter_sse_events(lines: Iterable[str]) -> Iterable[tuple[str, str]]:
    """按 SSE 规范把行流切分成 (event, data) 对。

    接受「data: x」和「data:x」两种写法，忽略以冒号开头的注释行，
    多行 data 用换行拼起来，空行结束一个事件。
    """
    event_name = "message"
    data_lines: list[str] = []
    for line in lines:
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event_name, "\n".join(data_lines)


def consume_stream(lines: Iterable[str]) -> tuple[str, str | None]:
    """把 OpenAI 风格的 SSE 流拼成完整正文，返回 (正文, finish_reason)。

    同时看 delta 和 message 两种承载方式：多数服务逐块发 delta，
    也有网关把整段结果放在最后一帧的 message 里。
    """
    parts: list[str] = []
    finish_reason: str | None = None
    total = 0
    for event_name, data in iter_sse_events(lines):
        if data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as error:
            raise LLMStreamError(f"流式响应里有无法解析的数据帧：{data[:120]}") from error
        if not isinstance(chunk, dict):
            continue
        error_payload = chunk.get("error")
        if event_name == "error" or error_payload:
            raise LLMStreamError(f"模型服务返回错误：{str(error_payload)[:240]}")
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        if choice.get("finish_reason"):
            finish_reason = str(choice["finish_reason"])
        for holder_key in ("delta", "message"):
            holder = choice.get(holder_key)
            if not isinstance(holder, dict):
                continue
            text = content_text(holder.get("content"))
            if text:
                total += len(text)
                if total > MAX_STREAM_CHARS:
                    raise LLMStreamError("流式回复超过长度上限，已中止")
                parts.append(text)
    return "".join(parts), finish_reason
