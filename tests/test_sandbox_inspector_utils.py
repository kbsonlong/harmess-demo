import unittest

from sandbox_inspector.utils import stable_id, truncate_lines, truncate_text


class TestInspectorUtils(unittest.TestCase):
    def test_stable_id_deterministic(self):
        self.assertEqual(stable_id("a", "b"), stable_id("a", "b"))
        self.assertNotEqual(stable_id("a", "b"), stable_id("a", "c"))

    def test_truncate_text(self):
        self.assertEqual(truncate_text("abc", max_chars=100), "abc")
        self.assertEqual(truncate_text(None, max_chars=100), "")
        out = truncate_text("abcdef", max_chars=4)
        self.assertTrue(out.endswith("(truncated)"))

    def test_truncate_lines(self):
        text = "\n".join([f"line{i}" for i in range(5)])
        out = truncate_lines(text, max_lines=2, max_chars_per_line=100)
        self.assertIn("line0", out)
        self.assertIn("line1", out)
        self.assertIn("(truncated)", out)


if __name__ == "__main__":
    unittest.main()

