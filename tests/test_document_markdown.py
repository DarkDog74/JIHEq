from types import SimpleNamespace
import unittest

from parser.document_markdown import render_document


def value(**kwargs):
    defaults = {"content": None, "blocks": None, "list": None, "table": None}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class DocumentMarkdownCompatibilityTests(unittest.TestCase):
    def _render_list_item(self, item):
        document = value(
            blocks=[
                value(
                    kind="list",
                    list=value(marker="bullet", start=1, items=[item]),
                )
            ],
            notes=[],
        )
        return render_document(document, {}).markdown

    def test_renders_anydoc_024_inline_checkbox(self):
        item = value(
            marker_label=None,
            blocks=[
                value(
                    kind="paragraph",
                    content=[
                        value(kind="checkbox", checked=True),
                        value(kind="text", text="已选择", style=None),
                    ],
                )
            ],
        )

        self.assertEqual(self._render_list_item(item), "- [x] 已选择\n")

    def test_still_renders_legacy_list_item_checkbox(self):
        item = value(
            marker_label=None,
            checked=False,
            blocks=[
                value(
                    kind="paragraph",
                    content=[value(kind="text", text="未选择", style=None)],
                )
            ],
        )

        self.assertEqual(self._render_list_item(item), "- [ ] 未选择\n")


if __name__ == "__main__":
    unittest.main()
