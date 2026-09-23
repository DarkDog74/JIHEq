"""OpenAI 兼容模型客户端：稽核规则层共用。

配置来自平台「模型配置」页（platform_app/model_config.py 持久化），
网页平台与 CLI 共用同一份。测试通过 monkeypatch 本模块函数注入假模型，
规则代码从本模块 `import chat as _chat` 以获得可替换的模块级绑定。
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import httpx

from platform_app.llm_transport import (
    LLMStreamError,
    consume_stream,
    log_llm_request,
    log_llm_response,
    start_timer,
)
from platform_app.model_config import (
    build_auth_headers,
    build_endpoint,
    build_thinking_params,
    load_model_config,
    merge_extra,
    parse_extra_body,
)


class ModelNotConfiguredError(RuntimeError):
    """模型接入地址或模型名未配置。"""


class ModelCallError(RuntimeError):
    """模型调用失败（网络异常 / HTTP 错误 / 超时 / 回复结构异常）。"""


class ModelResponseFormatError(ModelCallError):
    """模型回复中无法提取出 JSON 对象。"""


@dataclass(frozen=True)
class ModelReply:
    text: str
    model: str
    elapsed_ms: int


_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_MAX_ERROR_DETAIL_CHARS = 240
_GLM_53_FLASH = "glm-5.3-flash"


def _thinking_controls(model: str) -> dict[str, Any]:
    """规则调用路径的默认思考参数。default 档在本地不发（这是它原本的行为）。"""
    return build_thinking_params(model, default_disabled=False)


def _merge_extra(
    payload: dict[str, Any], headers: dict[str, str], config: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    return merge_extra(
        payload,
        headers,
        extra_body=config.get("extra_body"),
        extra_headers=config.get("extra_headers"),
    )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part).strip()
    return ""


class _RateLimitedError(RuntimeError):
    """内部信号：HTTP 429，携带建议等待秒数。"""

    def __init__(self, retry_after: float | None):
        self.retry_after = retry_after
        super().__init__("rate limited")


class _EmptyReplyError(RuntimeError):
    """内部信号：回复内容为空（推理型模型可能偶发）。"""


class _NonRetryableHTTPError(ModelCallError):
    """内部信号：确定性 4xx（401/403/400 等，不含 429/408），重试无意义。"""


def _check_status(status_code: int, body_text: str, retry_after: str | None) -> None:
    """把非 2xx 响应转成对应的内部异常。流式与非流式共用。"""
    if status_code == 429:
        try:
            wait = float(retry_after) if retry_after else None
        except ValueError:
            wait = None
        raise _RateLimitedError(wait)
    if status_code >= 400:
        detail = str(body_text or "").strip()[:_MAX_ERROR_DETAIL_CHARS]
        if 400 <= status_code < 500 and status_code != 408:
            # 401/403/400 等确定性 4xx：凭据或请求格式错误，重试只会白拖时间。
            raise _NonRetryableHTTPError(
                f"模型服务返回 HTTP {status_code}：{detail}"
            )
        raise ModelCallError(f"模型服务返回 HTTP {status_code}：{detail}")


def _read_blocking(response: httpx.Response) -> str:
    """非流式响应：整体 JSON 里取 choices[0].message.content。"""
    _check_status(response.status_code, response.text, response.headers.get("Retry-After"))
    try:
        message = response.json()["choices"][0]["message"]
        reply = _content_text(message.get("content"))
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ModelCallError("模型回复结构异常：缺少 choices[0].message.content") from error
    if not reply:
        raise _EmptyReplyError()
    return reply


def _read_streaming(response: httpx.Response) -> str:
    """流式响应：把 SSE 增量拼成完整正文。"""
    if response.status_code >= 400:
        response.read()
        _check_status(response.status_code, response.text, response.headers.get("Retry-After"))
    try:
        reply, _finish_reason = consume_stream(response.iter_lines())
    except LLMStreamError as error:
        raise ModelCallError(f"流式响应无法解析：{error}") from error
    if not reply.strip():
        raise _EmptyReplyError()
    return reply


def _post_once(payload: dict[str, Any], timeout: int) -> ModelReply:
    config = load_model_config()
    base_url = str(config.get("base_url") or "")
    api_key = str(config.get("api_key") or "")
    model = str(payload.get("model") or "")
    if not base_url or not model or not api_key:
        raise ModelNotConfiguredError(
            "模型未配置：请先在稽核平台「模型配置」页填写接入地址和 API Key"
        )

    # 请求形态全部由配置决定：认证头名称/前缀、对话路径、是否流式。
    # 默认值与平台一直以来的行为一致（Authorization: Bearer + /chat/completions + 非流式）。
    url = build_endpoint(base_url, config.get("chat_path"))
    headers = build_auth_headers(config)
    streaming = bool(config.get("stream"))
    if streaming:
        payload = {**payload, "stream": True}
        headers["Accept"] = "text/event-stream"

    # 附加请求参数 / 附加请求头：这是适配各厂商差异的通用出口，放在最后合并，
    # 所以它能覆盖平台生成的任何字段（包括 max_tokens、thinking 等）
    payload, headers = _merge_extra(dict(payload), headers, config)

    started = time.perf_counter()
    log_llm_request(url, headers, payload)
    timer = start_timer()
    try:
        if streaming:
            with httpx.stream(
                "POST", url, headers=headers, json=payload, timeout=timeout
            ) as response:
                status = response.status_code
                reply = _read_streaming(response)
        else:
            response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
            status = response.status_code
            reply = _read_blocking(response)
        log_llm_response(status, timer.elapsed_ms, reply)
    except httpx.TimeoutException as error:
        log_llm_response(0, timer.elapsed_ms, f"超时：{error}")
        raise ModelCallError(f"模型请求超时（上限 {timeout} 秒）：{error}") from error
    except httpx.HTTPError as error:
        log_llm_response(0, timer.elapsed_ms, f"连接失败：{error}")
        raise ModelCallError(f"模型请求失败：{error}") from error
    except (ModelCallError, _RateLimitedError, _EmptyReplyError) as error:
        log_llm_response(0, timer.elapsed_ms, str(error))
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000)

    return ModelReply(text=reply, model=model, elapsed_ms=elapsed_ms)


_MAX_ATTEMPTS = 4
_MAX_TIMEOUT_SECONDS = 300


def _effective_timeout(config_timeout: int, prompt_chars: int, *, has_images: bool) -> int:
    """思考型模型推理耗时长，按调用规模抬高超时下限（用户配置只作为下限参与）。"""
    timeout = max(int(config_timeout or 30), 60)
    if has_images:
        timeout = max(timeout, 120)
    if prompt_chars > 10000:
        timeout = max(timeout, 180)
    return min(timeout, _MAX_TIMEOUT_SECONDS)


def _post(payload: dict[str, Any], timeout: int) -> ModelReply:
    """带重试的调用：429 指数退避，超时加倍重试，空回复重试；确定性 4xx 不重试。

    连续失败达到上限后抛 ModelCallError，由规则层转 needs_review。
    """
    current_timeout = timeout
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return _post_once(payload, current_timeout)
        except _NonRetryableHTTPError:
            # 401/403/400 等：配置或凭据问题，立即失败避免每次调用白拖数十秒。
            raise
        except _RateLimitedError as error:
            last_error = ModelCallError("模型服务速率受限（HTTP 429），多次退避后仍失败")
            # 智谱等服务的账户级 RPM 限流恢复较慢，退避窗口拉长。
            wait = error.retry_after if error.retry_after else min(10 * 2 ** (attempt - 1), 60)
            if attempt >= _MAX_ATTEMPTS:
                break
            time.sleep(wait)
        except ModelCallError as error:
            last_error = error
            if attempt >= _MAX_ATTEMPTS:
                break
            message = str(error)
            if "超时" in message:
                current_timeout = min(current_timeout * 2, _MAX_TIMEOUT_SECONDS)
                time.sleep(1)
            else:
                time.sleep(min(2 * attempt, 6))
        except _EmptyReplyError:
            last_error = ModelCallError("模型回复内容为空（多次重试后仍为空）")
            if attempt >= _MAX_ATTEMPTS:
                break
            # 思考型模型可能把 token 花在推理上导致 content 为空，加倍重试。
            payload["max_tokens"] = min(int(payload.get("max_tokens") or 4096) * 2, 32768)
            time.sleep(1)
    raise last_error or ModelCallError("模型调用失败")


def chat(prompt: str, *, max_tokens: int = 4096) -> ModelReply:
    """调用网页配置的统一模型，返回完整回复文本。"""
    config = load_model_config()
    model = str(config.get("model") or "")
    if not model:
        raise ModelNotConfiguredError("模型未配置：请在「模型配置」页完成配置")
    timeout = _effective_timeout(
        config.get("timeout_seconds"), len(prompt), has_images=False
    )
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    # 用户填了「附加请求参数」就完全以他写的为准，平台不再自动补思考参数，
    # 免得偷偷塞一个 thinking:disabled 把用户的 enable_thinking 盖掉
    if not parse_extra_body(config):
        body.update(_thinking_controls(model))
    return _post(body, timeout)


def chat_with_images(
    prompt: str, image_paths: Sequence[Path | str], *, max_tokens: int = 8192
) -> ModelReply:
    """调用网页配置的统一模型（图片以 base64 内嵌）。"""
    config = load_model_config()
    model = str(config.get("model") or "")
    if not model:
        raise ModelNotConfiguredError("模型未配置：请在「模型配置」页完成配置")
    timeout = _effective_timeout(
        config.get("timeout_seconds"), len(prompt), has_images=True
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for raw_path in image_paths:
        path = Path(raw_path)
        if not path.is_file():
            raise ModelCallError(f"图片文件不存在：{path}")
        mime = _IMAGE_MIME.get(path.suffix.casefold(), "image/png")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}}
        )
    body: dict[str, Any] = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": content}],
    }
    if not parse_extra_body(config):
        body.update(_thinking_controls(model))
    return _post(body, timeout)


_FENCED_JSON_PATTERN = re.compile(r"```json\s*(.+?)\s*```", re.DOTALL)


def _fenced_json_object(text: str) -> dict[str, Any] | None:
    """从 ```json 围栏中提取第一个可解析的 JSON 对象；没有则返回 None。"""
    for match in _FENCED_JSON_PATTERN.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            return payload
    return None


def _legacy_scan_object(text: str) -> dict[str, Any] | None:
    """不带字符串状态的旧版括号配平扫描，仅作最后兜底。

    字符串状态扫描在“JSON 前出现奇数个裸引号”的回复上会整体卡进字符串态
    而提取失败；旧扫描不看引号、反而能配平出对象。两条路径取并集，最大化
    可提取率：新扫描处理字符串内花括号，旧扫描兜住裸引号形态。
    """
    depth = 0
    start: int | None = None
    for index, character in enumerate(text):
        if character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        payload = json.loads(text[start : index + 1])
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        return payload
                    start = None
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    """从回复文本中提取第一个平衡的 JSON 对象；失败抛 ModelResponseFormatError。

    扫描时跟踪双引号字符串状态（含反斜杠转义），字符串内的花括号不参与配平，
    避免值中代码片段 / LaTeX 的未配对括号导致提前截断；常规扫描失败时依次退回
    ```json 围栏内容与不带字符串状态的旧版配平扫描（兜住 JSON 前有奇数个裸引号
    的回复形态），三路都失败才抛错。
    """
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                start = index
            depth += 1
        elif character == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : index + 1]
                    try:
                        payload = json.loads(candidate)
                    except json.JSONDecodeError:
                        payload = None
                    if isinstance(payload, dict):
                        return payload
                    start = None
    payload = _fenced_json_object(text)
    if payload is not None:
        return payload
    payload = _legacy_scan_object(text)
    if payload is not None:
        return payload
    raise ModelResponseFormatError("模型回复中未找到可解析的 JSON 对象")
