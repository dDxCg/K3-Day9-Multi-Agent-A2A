from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dispute_resolution.llm import GeminiPolicyReviewer  # noqa: E402


class ReviewerParserTests(unittest.TestCase):
    def test_parses_strict_json(self) -> None:
        result = GeminiPolicyReviewer._parse_json_object(
            '{"verdict":"agree","reason":"Rule matches."}'
        )
        self.assertEqual(result["verdict"], "agree")

    def test_parses_fenced_json(self) -> None:
        result = GeminiPolicyReviewer._parse_json_object(
            '```json\n{"verdict":"agree","reason":"Rule matches."}\n```'
        )
        self.assertEqual(result["verdict"], "agree")

    def test_parses_first_object_before_trailing_noise(self) -> None:
        result = GeminiPolicyReviewer._parse_json_object(
            '{"verdict":"agree","reason":"Rule matches."}\nreason."}\n"}'
        )
        self.assertEqual(result["verdict"], "agree")


if __name__ == "__main__":
    unittest.main()
