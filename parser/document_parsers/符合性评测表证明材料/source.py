# -*- coding: utf-8 -*-
"""Reuse the tested OOXML source layer used by the risk-report parser."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_COMMON_PATH = Path(__file__).resolve().parents[1] / "风险评估报告" / "source.py"
if not _COMMON_PATH.is_file():
    raise ImportError(f"缺少共享 OOXML 解析模块: {_COMMON_PATH}")

_MODULE_NAME = "_shared_risk_report_word_source"
_SPEC = importlib.util.spec_from_file_location(_MODULE_NAME, _COMMON_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"无法加载共享 OOXML 解析模块: {_COMMON_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_MODULE_NAME] = _MODULE
_SPEC.loader.exec_module(_MODULE)

SourceError = _MODULE.SourceError
PreparedDocument = _MODULE.PreparedDocument
ImageRef = _MODULE.ImageRef
ParagraphPart = _MODULE.ParagraphPart
TableCell = _MODULE.TableCell
TableImage = _MODULE.TableImage
TableData = _MODULE.TableData
DocumentBlock = _MODULE.DocumentBlock
DocumentStructure = _MODULE.DocumentStructure
clean_text = _MODULE.clean_text
prepare_document = _MODULE.prepare_document
extract_ooxml = _MODULE.extract_ooxml

__all__ = [
    "SourceError",
    "PreparedDocument",
    "ImageRef",
    "ParagraphPart",
    "TableCell",
    "TableImage",
    "TableData",
    "DocumentBlock",
    "DocumentStructure",
    "clean_text",
    "prepare_document",
    "extract_ooxml",
]
