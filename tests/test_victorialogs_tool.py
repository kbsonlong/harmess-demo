import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from agent_core.release_context import load_latest_release_failure, summarize_release_failure_context
from agent_core.victorialogs import victorialogs_query


class TestReleaseContext(TestCase):
    def test_load_latest_release_failure_picks_newest(self):
        with tempfile.TemporaryDirectory() as d:
            reports = Path(d)
            p1 = reports / "release_failure-a.json"
            p2 = reports / "release_failure-b.json"
            p1.write_text(json.dumps({"release_id": "a"}, ensure_ascii=False), encoding="utf-8")
            p2.write_text(json.dumps({"release_id": "b"}, ensure_ascii=False), encoding="utf-8")
            now = int(os.path.getmtime(p2))
            os.utime(p1, (now - 10, now - 10))
            os.utime(p2, (now - 5, now - 5))
            data = load_latest_release_failure(reports_dir=str(reports), max_age_s=3600)
            self.assertIsInstance(data, dict)
            self.assertEqual(data.get("release_id"), "b")
            self.assertTrue(str(p2) in str(data.get("_report_path")))

    def test_summarize_release_failure_context_keeps_core_fields(self):
        s = summarize_release_failure_context(
            {
                "schema_version": "1",
                "release_id": "r1",
                "mode": "imagepull",
                "observed_at": "2026-05-01T00:00:00Z",
                "time_window": {"start": "x", "end": "y", "start_epoch": 1, "end_epoch": 2},
                "targets": [{"kind": "Deployment", "namespace": "ns", "name": "demo"}],
                "_report_path": "/tmp/release_failure-r1.json",
                "wait_result": {"ignored": True},
            }
        )
        self.assertEqual(s["release_id"], "r1")
        self.assertEqual(s["time_window"]["start"], "x")
        self.assertEqual(s["targets"][0]["name"], "demo")
        self.assertNotIn("wait_result", s)


class TestVictoriaLogsTool(TestCase):
    def test_victorialogs_query_rejects_empty_query(self):
        out = victorialogs_query(query="  ")
        self.assertEqual(out.get("error"), "invalid_query")

    def test_victorialogs_query_parses_sandbox_stdout_json(self):
        sandbox_stdout = json.dumps(
            {"endpoint": "http://x/select/logsql/query", "generated_at": 1, "results": [{"id": "q1", "items": []}]},
            ensure_ascii=False,
        )

        captured = {}

        def fake_exec_in_sandbox(**kwargs):
            captured.update(kwargs)
            return {"stdout": sandbox_stdout, "stderr": "", "exit_code": 0, "namespace": "default", "pod_name": "p", "container": "c"}

        with patch("agent_core.victorialogs.exec_in_sandbox", new=fake_exec_in_sandbox):
            out = victorialogs_query(query="error", limit=5)
        self.assertEqual(out["tool"], "victorialogs_query")
        self.assertIn("/select/logsql/query", out["input"]["endpoint"])
        self.assertIsInstance(out["result"], dict)
        self.assertIn("results", out["result"])
        self.assertEqual(captured["command"][0], "python")
        self.assertEqual(captured["command"][1], "-c")
