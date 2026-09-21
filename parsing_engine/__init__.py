"""Single-unit parsing integration used by both the web app and tests."""

from .service import ParseResult, preview_source, run_unit_parse

__all__ = ["ParseResult", "preview_source", "run_unit_parse"]
