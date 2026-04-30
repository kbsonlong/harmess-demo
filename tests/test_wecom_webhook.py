import json
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from wecom_webhook import (
    WECOM_MARKDOWN_MAX_CHARS,
    build_wecom_markdown_from_report,
    find_report_path,
    send_wecom_markdown,
)


class _FakeResponse:
    def __init__(self, status: int, body: str):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class TestWecomWebhook(TestCase):
    def test_send_wecom_markdown_ok(self):
        with patch(
            "urllib.request.urlopen",
            return_value=_FakeResponse(200, json.dumps({"errcode": 0, "errmsg": "ok"})),
        ):
            r = send_wecom_markdown("https://example.invalid/webhook", "# hi")
            self.assertTrue(r.ok)
            self.assertEqual(r.status_code, 200)

    def test_send_wecom_markdown_truncates(self):
        long_md = "a" * (WECOM_MARKDOWN_MAX_CHARS + 100)
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["body"] = req.data
            return _FakeResponse(200, json.dumps({"errcode": 0, "errmsg": "ok"}))

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            r = send_wecom_markdown("https://example.invalid/webhook", long_md)
            self.assertTrue(r.ok)
            body = json.loads(captured["body"].decode("utf-8"))
            sent = body["markdown"]["content"]
            self.assertLessEqual(len(sent), WECOM_MARKDOWN_MAX_CHARS)

    def test_find_report_path_by_thread_id(self):
        with tempfile.TemporaryDirectory() as d:
            reports = Path(d)
            p = reports / "inspection_report-abc.md"
            p.write_text("ok", encoding="utf-8")
            found = find_report_path(reports, thread_id="abc", max_age_s=1)
            self.assertEqual(found, p)

    def test_build_markdown_from_report(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "inspection_report-x.md"
            p.write_text("# r\n\nhello", encoding="utf-8")
            md = build_wecom_markdown_from_report(p)
            self.assertIn("inspection_report-x.md", md)
            self.assertIn("hello", md)

