class ExtractorNotApplicableError(ValueError):
    """当前稽核项的提取器不适用于这份输入文档。"""


class RevisionHistoryNotFoundError(ExtractorNotApplicableError):
    """输入文档中不存在修订历史。"""


class TargetSectionNotFoundError(ExtractorNotApplicableError):
    """输入文档中不存在当前稽核项要求的目标章节。"""


class RequiredDocumentNotFoundError(FileNotFoundError):
    """输入单位目录中没有稽核项要求的 Markdown。"""

    def __init__(self, message: str, *, requirement: str | None = None) -> None:
        super().__init__(message)
        self.requirement = requirement


class MultipleRequiredDocumentsError(ValueError):
    """输入单位目录中存在多份符合要求的 Markdown，无法唯一选择。"""
