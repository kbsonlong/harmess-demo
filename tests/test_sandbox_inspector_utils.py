import unittest

from sandbox_inspector.utils import json_dumps, stable_id, status_only_object, strip_managed_fields, truncate_lines, truncate_text


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

    def test_json_dumps_compact(self):
        s = json_dumps({"a": 1, "b": 2})
        self.assertNotIn("\n", s)
        self.assertIn('"a":1', s)
        self.assertIn('"b":2', s)

    def test_strip_managed_fields_recursive(self):
        obj = {"metadata": {"name": "x", "managedFields": [{"manager": "y"}]}, "managedFields": [1]}
        out = strip_managed_fields(obj)
        self.assertNotIn("managedFields", out)
        self.assertNotIn("managedFields", out.get("metadata", {}))

    def test_status_only_object(self):
        obj = {"metadata": {"name": "p", "managedFields": [{"manager": "y"}]}, "status": {"phase": "Running"}}
        out = status_only_object(obj)
        self.assertEqual(out, {"phase": "Running"})


if __name__ == "__main__":
    unittest.main()
