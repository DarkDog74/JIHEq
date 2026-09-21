from __future__ import annotations

import unittest

from extractors.dj_07 import extract_dj_07_text


VALID_MARKDOWN = r"""
[[IMAGE id=IMG-COVER file="cover.jpg"]]

- 1\) **物理环境**

- 2\) **网络拓扑结构**

[[IMAGE id=IMG-TOPOLOGY file="topology.png"]]

- 3\) **硬件设备部署情况**

- 4\) **网络边界划分**

边界划分说明。
"""


class DJ07ExtractorTests(unittest.TestCase):
    def test_extracts_image_only_from_topology_section(self) -> None:
        self.assertEqual(
            extract_dj_07_text(VALID_MARKDOWN),
            {
                "网络拓扑结构章节存在": True,
                "网络拓扑图数量": 1,
                "网络边界划分章节存在": True,
            },
        )

    def test_image_under_another_title_does_not_count(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            '[[IMAGE id=IMG-TOPOLOGY file="topology.png"]]\n\n- 3\\)',
            '- 3\\)',
        ).replace(
            "- 3\\) **硬件设备部署情况**",
            '- 3\\) **硬件设备部署情况**\n\n[[IMAGE id=IMG-OTHER file="other.png"]]',
        )
        result = extract_dj_07_text(markdown)
        self.assertEqual(result["网络拓扑图数量"], 0)

    def test_cover_image_does_not_count(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            '[[IMAGE id=IMG-TOPOLOGY file="topology.png"]]', ""
        )
        self.assertEqual(extract_dj_07_text(markdown)["网络拓扑图数量"], 0)

    def test_supports_full_width_parenthesis_and_markdown_heading(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "- 2\\) **网络拓扑结构**", "### 2） **网络拓扑结构**"
        ).replace("- 4\\) **网络边界划分**", "### 4） **网络边界划分**")
        result = extract_dj_07_text(markdown)
        self.assertTrue(result["网络拓扑结构章节存在"])
        self.assertTrue(result["网络边界划分章节存在"])

    def test_table_of_contents_links_are_not_treated_as_sections(self) -> None:
        markdown = r"""
[2\) 网络拓扑结构 5](#2-网络拓扑结构)
[4\) 网络边界划分 6](#4-网络边界划分)
[[IMAGE id=IMG-COVER file="cover.jpg"]]
"""
        result = extract_dj_07_text(markdown)
        self.assertFalse(result["网络拓扑结构章节存在"])
        self.assertFalse(result["网络边界划分章节存在"])
        self.assertEqual(result["网络拓扑图数量"], 0)

    def test_system_topology_title_is_accepted(self) -> None:
        markdown = VALID_MARKDOWN.replace("网络拓扑结构", "系统拓扑结构")
        result = extract_dj_07_text(markdown)
        self.assertTrue(result["网络拓扑结构章节存在"])
        self.assertEqual(result["网络拓扑图数量"], 1)

    def test_any_numbered_title_containing_topology_is_accepted(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "- 2\\) **网络拓扑结构**", "- 7\\) **业务拓扑图**"
        )
        result = extract_dj_07_text(markdown)
        self.assertTrue(result["网络拓扑结构章节存在"])
        self.assertEqual(result["网络拓扑图数量"], 1)

    def test_unnumbered_markdown_heading_containing_topology_is_accepted(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "- 2\\) **网络拓扑结构**", "### **应用拓扑图**"
        )
        result = extract_dj_07_text(markdown)
        self.assertTrue(result["网络拓扑结构章节存在"])
        self.assertEqual(result["网络拓扑图数量"], 1)

    def test_network_boundary_title_does_not_require_number_four(self) -> None:
        for replacement in (
            "- 3\\) **网络边界划分**",
            "- 5\\) **网络边界划分**",
            "### **网络边界划分**",
        ):
            with self.subTest(replacement=replacement):
                markdown = VALID_MARKDOWN.replace(
                    "- 4\\) **网络边界划分**", replacement
                )
                self.assertTrue(
                    extract_dj_07_text(markdown)["网络边界划分章节存在"]
                )


if __name__ == "__main__":
    unittest.main()
