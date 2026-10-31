"""Regression checks for Mermaid syntax in the bilingual project README."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

import validate_mermaid

ROOT = Path(__file__).resolve().parents[1]
README_FILES = (ROOT / "README.md", ROOT / "README.ru.md")
MERMAID_BLOCK = re.compile(r"```mermaid\s*(.*?)```", re.DOTALL | re.IGNORECASE)


class ReadmeMermaidTests(unittest.TestCase):
    @unittest.skipUnless(
        validate_mermaid._cli_path().is_file(),
        "real Mermaid rendering runs in the Unix CI job",
    )
    def test_readme_diagrams_parse_with_pinned_mermaid_cli(self) -> None:
        validator = ROOT / "tools" / "validate_mermaid.py"
        result = subprocess.run(
            [sys.executable, str(validator), *(str(path) for path in README_FILES)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
