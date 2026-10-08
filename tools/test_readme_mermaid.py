"""Regression checks for Mermaid syntax in the bilingual project README."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README_FILES = (ROOT / "README.md", ROOT / "README.ru.md")
MERMAID_BLOCK = re.compile(r"```mermaid\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ReadmeMermaidTests(unittest.TestCase):
    def test_mermaid_blocks_do_not_label_unsupported_bidirectional_edges(self) -> None:
        for readme in README_FILES:
            with self.subTest(readme=readme.name):
                source = readme.read_text(encoding="utf-8")
                blocks = MERMAID_BLOCK.findall(source)
                self.assertTrue(blocks, f"No Mermaid diagram found in {readme.name}")
                for block in blocks:
                    self.assertNotRegex(
                        block,
                        r"<-->\s*\|",
                        "Mermaid edge labels must use a one-way arrow; represent a "
                        "two-way exchange with two labeled arrows.",
                    )


if __name__ == "__main__":
    unittest.main()
