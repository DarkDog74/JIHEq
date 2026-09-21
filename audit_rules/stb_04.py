from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    chat_with_images as _chat_with_images,
    extract_json_object,
)

from .model_support import (
    StagedImageExpiredError,
    call_record,
    failed_result,
    passed_result,
    resolve_staged_image,
)
from .registry import register_rule


_PROMPT_TEMPLATE = """你是公章印文核验员。下面是网络安全三同步情况报告图片（共 {count} 张，按顺序给出）。

任务：判断图片中的红色印章，其红色印文是否明确包含连续文字“网管中心”。

请严格执行：
1. 遍历整张图片定位所有红色印章，重点检查页面右下角落款区域的小尺寸圆章。
2. 对每枚红章进行局部放大式观察，沿圆章外圈依次检查上弧、右侧、下弧、左侧；印文可能沿圆周旋转排列。
3. 红色印文可能被横向黑色正文、公司名称或日期部分遮挡。必须区分红色印文与黑色打印文字，黑色正文不算印章文字。
4. 单独核对红色印文中的“网”“管”“中”“心”四个字，并说明每个字在圆章上的位置。
5. 不得因黑色正文出现或未出现“网管中心”就作判断。
6. 只有从红色印章中明确辨认出连续的“网管中心”，“红色印文包含网管中心”才返回 true；被遮挡或模糊到无法确认时返回 false，不得猜测。

只输出一个 JSON 对象，不要输出其他文字：
{{"发现红色印章": <true或false>, "红色印文包含网管中心": <true或false>, "印章位置": "<页内位置>", "红色印文": "<尽量逐字抄录>", "网管中心逐字证据": {{"网": "<位置或未见>", "管": "<位置或未见>", "中": "<位置或未见>", "心": "<位置或未见>"}}, "遮挡情况": "<黑色文字对红章的遮挡情况>", "依据": "<是否包含网管中心及直接证据，80字以内>", "分析说明": "<第一人称概括核验过程，100字以内>"}}"""


def _create_stamp_detail_images(
    image_paths: list[Path], output_dir: Path
) -> list[Path]:
    """按红色像素自动裁出印章区域并放大，原图不修改。"""
    import cv2
    import numpy as np

    output_dir.mkdir(parents=True, exist_ok=True)
    details: list[Path] = []
    for index, source in enumerate(image_paths, start=1):
        try:
            encoded = np.fromfile(str(source), dtype=np.uint8)
            image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        except (OSError, ValueError):
            continue
        if image is None or image.size == 0:
            continue

        blue = image[:, :, 0].astype(np.int16)
        green = image[:, :, 1].astype(np.int16)
        red = image[:, :, 2].astype(np.int16)
        mask = (red >= 120) & (red - green >= 30) & (red - blue >= 25)
        y_coordinates, x_coordinates = np.nonzero(mask)
        minimum_red_pixels = max(50, round(image.shape[0] * image.shape[1] * 0.00005))
        if len(x_coordinates) < minimum_red_pixels:
            continue

        x_start, x_end = int(x_coordinates.min()), int(x_coordinates.max())
        y_start, y_end = int(y_coordinates.min()), int(y_coordinates.max())
        width = x_end - x_start + 1
        height = y_end - y_start + 1
        padding_x = max(30, width // 4)
        padding_y = max(30, height // 4)
        left = max(0, x_start - padding_x)
        right = min(image.shape[1], x_end + padding_x + 1)
        top = max(0, y_start - padding_y)
        bottom = min(image.shape[0], y_end + padding_y + 1)
        crop = image[top:bottom, left:right]
        if crop.size == 0:
            continue

        longest_side = max(crop.shape[:2])
        scale = min(4.0, max(1.0, 1500 / longest_side))
        if scale > 1.0:
            crop = cv2.resize(
                crop,
                None,
                fx=scale,
                fy=scale,
                interpolation=cv2.INTER_LANCZOS4,
            )
        destination = output_dir / f"stamp-detail-{index:03d}.png"
        success, payload = cv2.imencode(".png", crop)
        if not success:
            continue
        payload.tofile(str(destination))
        details.append(destination)
    return details


@register_rule("STB-04", required_fields=("has_images", "图片文件"), uses_model=True)
def audit_stb_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    # 判定口径（2026-08-26 定稿）：锚定三同步.json 清单——
    # 纯 Markdown 报告无图片 → 必然无章，直接不通过；
    # 有图片 → 喂视觉模型识别“网管中心”章，有→通过，无→不通过。
    if not fields.get("has_images"):
        return failed_result(
            "STB-04",
            ["三同步报告仅为 Markdown，未包含任何图片，未加盖网管中心章"],
            "三同步报告无图片，未加盖网管中心章",
        )

    staged = [item for item in (fields.get("图片文件") or []) if isinstance(item, Mapping)]
    if not staged:
        return failed_result(
            "STB-04",
            ["三同步报告声明存在图片，但提取阶段未记录图片文件路径"],
            "无法核验印章：图片文件路径缺失",
        )
    try:
        image_paths = [resolve_staged_image(entry) for entry in staged]
    except StagedImageExpiredError as error:
        return failed_result(
            "STB-04", [str(error)], "三同步报告图片路径已失效，无法核验印章"
        )

    prompt = _PROMPT_TEMPLATE.format(count=len(image_paths))
    with tempfile.TemporaryDirectory(prefix="stb04-stamp-detail-") as directory:
        detail_paths = _create_stamp_detail_images(image_paths, Path(directory))
        model_images = image_paths + detail_paths
        if detail_paths:
            prompt += (
                f"\n\n辅助图说明：前 {len(image_paths)} 张是完整报告页，"
                f"后 {len(detail_paths)} 张是从对应原图自动截取并放大的红色印章区域。"
                "放大图不是新报告，请结合原图定位，优先依据放大图逐字核对红色印文。"
            )
        try:
            reply = _chat_with_images(prompt, model_images, max_tokens=2048)
            payload = extract_json_object(reply.text)
        except (ModelNotConfiguredError, ModelCallError) as error:
            return failed_result(
                "STB-04",
                [f"模型调用失败：{error}"],
                "无法核验印章：模型调用失败",
                call_records=[call_record(prompt, error=error)],
            )

    record = [call_record(prompt, reply)]
    detail = {
        "模型依据": payload.get("依据"),
        "图片数量": len(image_paths),
        "印章放大图数量": len(detail_paths),
        "发现红色印章": payload.get("发现红色印章"),
        "印章位置": payload.get("印章位置"),
        "红色印文": payload.get("红色印文"),
        "网管中心逐字证据": payload.get("网管中心逐字证据"),
        "遮挡情况": payload.get("遮挡情况"),
    }
    if payload.get("红色印文包含网管中心") is True:
        return passed_result(
            "STB-04",
            f"三同步报告图片中识别到「网管中心」章（{payload.get('依据') or '—'}）",
            call_records=record,
            **detail,
        )
    return failed_result(
        "STB-04",
        [
            f"三同步报告图片中未见「网管中心」章"
            f"（{payload.get('依据') or '模型判定无章'}）"
        ],
        "未在三同步报告图片中识别到「网管中心」章",
        call_records=record,
        **detail,
    )
